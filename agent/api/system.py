"""/health, /audit, /fs, /sessions, /apps, /logs, /bus, /diffs,
/reports, /notifications router.

The cross-cutting "system surface" — health probe, audit log + feature
audit, filesystem browser, session management, app permissions, log
viewer + tail, bus/diff history, periodic report generation,
notifications SSE.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.core import app_permissions, audit, bus, session_store
from agent.core.session import manager

router = APIRouter(tags=["system"])


# ── Health ───────────────────────────────────────────────────────────────────

# Cache for the diagnose() call. UI polls /health every ~5s; running
# diagnose every poll could spawn `ss` 12×/min and burn up to 6s on the
# unhappy path. Cache for 30s — the underlying state (LM Studio running,
# OpenSnitch rules) doesn't flip every second.
_DIAGNOSE_CACHE: dict[str, Any] = {"ts": 0.0, "verdict": None}
_DIAGNOSE_CACHE_TTL_S = 30.0


@router.get("/health")
async def health():
    from agent.core import analytics
    from agent.core.lm_studio import get_client
    client = get_client()
    status = await client.check_connection()
    analytics.update_lm_cache(status.reachable, status.models, status.latency_ms)

    # If LM Studio looks unreachable, run the diagnostic to distinguish
    # "not running" from "firewall blocked" so the UI can show the
    # right banner. Cached for 30s so the worst-case ~6s probe doesn't
    # fire on every poll.
    blocked = False
    blocked_hint: str | None = None
    if not status.reachable:
        try:
            import time as _t
            if _t.time() - _DIAGNOSE_CACHE["ts"] > _DIAGNOSE_CACHE_TTL_S:
                from scripts.diagnose_lm_block import diagnose
                d = await asyncio.to_thread(diagnose)
                _DIAGNOSE_CACHE["ts"] = _t.time()
                _DIAGNOSE_CACHE["verdict"] = d["verdict"]
            verdict = _DIAGNOSE_CACHE["verdict"]
            if verdict == "firewall_blocked":
                blocked = True
                blocked_hint = "OpenSnitch is likely blocking JARVIS → LM Studio. Run `venv/bin/python3 scripts/diagnose_lm_block.py` for the exact rule."
            elif verdict == "lm_studio_not_running":
                blocked_hint = "LM Studio is not running. Open it and click Start Server."
            elif verdict == "ipv6_only":
                blocked_hint = "LM Studio is bound to IPv6 only. Set host to 127.0.0.1 in Server settings."
        except Exception:
            pass  # diagnostic failure must not break /health

    return {
        "status": "online",
        "lm_studio": {
            "connected": status.reachable,
            "models": status.models,
            "latency_ms": round(status.latency_ms, 1),
            "error": status.error,
            "blocked": blocked,
            "blocked_hint": blocked_hint,
        },
    }


# ── Feature audit ────────────────────────────────────────────────

@router.get("/audit/features")
async def audit_features(phase: str | None = Query(default=None)):
    """Feature completion audit. Walks feature audit checkboxes
    and reports done / pending counts per Part."""
    from agent.core import feature_audit
    return await asyncio.to_thread(feature_audit.audit, phase)


# ── Filesystem browser ───────────────────────────────────────────────────────

@router.get("/fs/ls")
async def fs_ls(path: str = Query(default="~")):
    p = Path(path).expanduser().resolve()
    try:
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        result = []
        for e in entries:
            try:
                size = e.lstat().st_size if not e.is_dir() else None
            except OSError:
                size = None
            result.append({
                "name": e.name,
                "type": "dir" if e.is_dir() else "file",
                "size": size,
            })
        return {"path": str(p), "entries": result}
    except FileNotFoundError:
        return {"error": f"not found: {path}"}
    except PermissionError:
        return {"error": f"permission denied: {path}"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/fs/cat")
async def fs_cat(path: str = Query(...)):
    p = Path(path).expanduser().resolve()
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"path": str(p), "content": content}
    except FileNotFoundError:
        return {"error": f"not found: {path}"}
    except PermissionError:
        return {"error": f"permission denied: {path}"}
    except IsADirectoryError:
        return {"error": f"is a directory: {path}"}
    except Exception as e:
        return {"error": str(e)}


class FileWriteRequest(BaseModel):
    path: str
    content: str


@router.post("/fs/write")
async def fs_write(req: FileWriteRequest):
    p = Path(req.path).expanduser().resolve()
    blocked = ["/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc"]
    if any(str(p).startswith(b) for b in blocked):
        raise HTTPException(status_code=403,
                            detail=f"Write to {p} is blocked by security policy")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(req.content, encoding="utf-8")
        return {"path": str(p), "bytes": len(req.content.encode()), "ok": True}
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"permission denied: {p}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Session management ───────────────────────────────────────────────────────

@router.delete("/session/{session_id}")
async def clear_session(session_id: str):
    s = manager.get_or_create(session_id)
    s.clear_history()
    return {"cleared": True}


# ── Audit log ────────────────────────────────────────────────────────────────

@router.get("/audit")
async def audit_recent(n: int = Query(default=100), session_id: Optional[str] = Query(default=None)):
    """Return recent audit log entries."""
    return {"entries": audit.recent(n=n, session_id=session_id)}


@router.get("/audit/verify")
async def audit_verify():
    """Verify the audit log chain integrity."""
    ok, message = audit.verify_chain()
    return {"ok": ok, "message": message}


@router.get("/audit/stats")
async def audit_stats_endpoint():
    """Return audit log statistics."""
    return audit.stats()


# ── Logs ─────────────────────────────────────────────────────────────────────

_LOG_SOURCES = {
    "agent":   str(Path.home() / "jarvis/logs/agent.log"),
    "audit":   str(Path.home() / ".jarvis/audit.log"),
    "access":  str(Path.home() / "jarvis/logs/access.log"),
    "verifier": str(Path.home() / "jarvis/logs/verifier.log"),
}


@router.get("/logs")
async def get_logs(
    source: str = Query(default="agent"),
    tail: int = Query(default=200),
    level: str = Query(default=""),
):
    """Return last N log lines, optionally filtered by level."""
    log_path = _LOG_SOURCES.get(source)

    # Auto-discover log files if specified path doesn't exist
    if not log_path or not Path(log_path).exists():
        log_dir = Path.home() / "jarvis/logs"
        found = list(log_dir.glob("*.log")) if log_dir.exists() else []
        if not found:
            return {"source": source, "lines": [], "note": "no log file found"}
        log_path = str(found[0])

    try:
        lines = Path(log_path).read_text(errors="replace").split("\n")
        lines = [l for l in lines if l.strip()]
        if level:
            lines = [l for l in lines if level.upper() in l.upper()]
        return {"source": source, "path": log_path, "lines": lines[-tail:], "total": len(lines)}
    except Exception as e:
        return {"source": source, "lines": [], "error": str(e)}


@router.get("/logs/stream")
async def stream_logs(source: str = Query(default="agent")):
    """SSE stream of live log lines."""
    log_path = _LOG_SOURCES.get(source, _LOG_SOURCES["agent"])

    async def generate():
        pos = 0
        if Path(log_path).exists():
            pos = Path(log_path).stat().st_size
        while True:
            await asyncio.sleep(1)
            if Path(log_path).exists():
                with open(log_path) as f:
                    f.seek(pos)
                    new = f.read()
                    if new:
                        pos += len(new.encode())
                        for line in new.strip().split("\n"):
                            if line:
                                yield f"data: {json.dumps({'line': line})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Plugin toggle ────────────────────────────────────────────────────────────

@router.patch("/plugins/{name}/toggle")
async def toggle_plugin(name: str):
    """Enable or disable a plugin at runtime."""
    override_path = Path.home() / ".jarvis" / "plugin_overrides.json"
    overrides: dict = {}
    if override_path.exists():
        try:
            overrides = json.loads(override_path.read_text())
        except Exception:
            pass
    current = overrides.get(name, True)
    overrides[name] = not current
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(json.dumps(overrides, indent=2))
    return {"plugin": name, "enabled": overrides[name]}


# ── App permissions ──────────────────────────────────────────────────────────

class PermissionRequest(BaseModel):
    permission: Literal["allow", "ask", "block"]


@router.get("/apps/permissions")
async def list_app_permissions():
    return app_permissions.list_all()


@router.put("/apps/permissions/{app_name}")
async def set_app_permission(app_name: str, req: PermissionRequest):
    app_permissions.set_permission(app_name, req.permission)
    return {"app": app_name, "permission": req.permission}


@router.delete("/apps/permissions/{app_name}")
async def delete_app_permission(app_name: str):
    ok = app_permissions.remove(app_name)
    if not ok:
        raise HTTPException(status_code=404,
                            detail=f"No permission entry for {app_name}")
    return {"deleted": app_name}


# ── Sessions ─────────────────────────────────────────────────────────────────

class SessionUpdateRequest(BaseModel):
    project: str = "default"
    last_message: str = ""
    summary: str = ""
    message_count: int = 0


@router.get("/sessions")
async def list_sessions_endpoint():
    return {"sessions": session_store.list_sessions()}


@router.put("/sessions/{session_id}")
async def upsert_session(session_id: str, req: SessionUpdateRequest):
    import time
    rec = session_store.SessionRecord(
        session_id=session_id,
        project=req.project,
        started=time.time(),
        last_active=time.time(),
        message_count=req.message_count,
        last_message=req.last_message[:120],
        summary=req.summary[:400],
    )
    session_store.upsert(rec)
    return {"saved": session_id}


@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    ok = session_store.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"deleted": session_id}


# ── Notifications SSE ────────────────────────────────────────────────────────

@router.get("/notifications/stream")
async def notification_stream(request: Request):
    """SSE stream of internal notifications (drive alerts, council blocks, etc.).

    Reads the notification queue off `app.state.notifications` which the
    main.py lifespan creates. This handler stays in the system router but
    grabs the queue via the request — no module-level coupling."""
    queue: asyncio.Queue = request.app.state.notifications

    async def generate():
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=30)
                yield f"data: {json.dumps(item)}\n\n"
            except asyncio.TimeoutError:
                yield "data: {\"type\":\"ping\"}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Bus recent ───────────────────────────────────────────────────────────────

@router.get("/bus/recent")
async def bus_recent(limit: int = Query(default=50), topic: str = Query(default="")):
    """Query the message bus history."""
    return {"events": bus.recent(limit, topic_prefix=topic)}


# ── Diff audit log ───────────────────────────────────────────────────────────

@router.get("/diffs/recent")
async def diffs_recent(limit: int = Query(default=20)):
    """Return the most recent file change diffs from the audit log (1.9)."""
    diff_log = Path.home() / ".jarvis" / "diff_audit.jsonl"
    if not diff_log.exists():
        return {"diffs": []}
    lines = diff_log.read_text().strip().splitlines()
    out = []
    for line in reversed(lines[-limit * 2:]):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
        if len(out) >= limit:
            break
    return {"diffs": out}


# ── Periodic Reports (2.3) ───────────────────────────────────────────────────

@router.get("/reports/latest")
async def reports_latest(hours: int = Query(default=24)):
    """Auto-generated summary of JARVIS activity in the last N hours."""
    import time as _time
    cutoff = _time.time() - hours * 3600
    # Pull a wide window so busy days don't get clipped by the 500-row cap.
    recent_events = bus.recent(5000)
    filtered = [e for e in recent_events if e.get("ts", 0) >= cutoff]
    truncated = len(recent_events) >= 5000 and (
        recent_events and recent_events[-1].get("ts", 0) > cutoff
    )

    counts: dict[str, int] = {}
    for e in filtered:
        prefix = e["topic"].split(".")[0]
        counts[prefix] = counts.get(prefix, 0) + 1

    agent_done = [e for e in filtered if e["topic"] == "agent.completed"]
    agent_failed = [e for e in filtered if e["topic"] == "agent.failed"]

    auto_cycles = [e for e in filtered if e["topic"] == "autonomy.cycle_done"]
    total_auto_actions = sum(e.get("actions", 0) for e in auto_cycles)

    audit_stats = audit.stats()

    diff_log = Path.home() / ".jarvis" / "diff_audit.jsonl"
    diff_count = 0
    if diff_log.exists():
        for line in diff_log.read_text().splitlines():
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("ts", 0) >= cutoff:
                diff_count += 1

    return {
        "period_hours": hours,
        "event_count": len(filtered),
        "topic_breakdown": counts,
        "agents": {
            "completed": len(agent_done),
            "failed": len(agent_failed),
            "success_rate": round(
                len(agent_done) / max(len(agent_done) + len(agent_failed), 1), 2
            ),
        },
        "autonomy": {
            "cycles": len(auto_cycles),
            "actions_taken": total_auto_actions,
        },
        "audit_entries": audit_stats.get("total_entries", 0),
        "file_changes": diff_count,
        "truncated": truncated,
    }


# ─── C15.1 — Coder FIM completion ───────────────────────────────────────────

class CoderCompleteRequest(BaseModel):
    prefix: str
    suffix: str = ""
    max_tokens: int = 80
    model: str | None = None


@router.post("/coder/complete")
async def coder_complete(body: CoderCompleteRequest):
    """C15.1 — Fill-in-the-Middle inline completion. Caller supplies
    `prefix` (text before cursor) and `suffix` (text after cursor); the
    LM returns the middle. Designed for Qwen 2.5 Coder's FIM template
    but degrades gracefully on non-FIM models.

    Bound: prefix/suffix trimmed to the 4 KB closest to the cursor,
    max_tokens clamped to [16, 256]."""
    import asyncio
    from agent.core import fim_completer
    return await asyncio.to_thread(
        fim_completer.complete,
        body.prefix, body.suffix, body.max_tokens, body.model,
    )


# ─── D10 — Self-onboarding ──────────────────────────────────────────────────

class OnboardingPathRequest(BaseModel):
    path: str


@router.post("/onboarding/check")
async def onboarding_check(body: OnboardingPathRequest):
    """D10 — surface signals about a project directory. Returns is_new
    + file_count + markers + language histogram + readme preview +
    suggested actions. Cheap — never reads file bodies past one readme."""
    import asyncio
    from agent.core import onboarding
    return await asyncio.to_thread(onboarding.propose, body.path)


@router.post("/onboarding/seen")
async def onboarding_seen(body: OnboardingPathRequest):
    """D10 — mark a project path as seen so /onboarding/check no longer
    flags it as new. Called when the user dismisses or accepts an offer."""
    import asyncio
    from agent.core import onboarding
    return await asyncio.to_thread(onboarding.mark_seen, body.path)


@router.post("/onboarding/summarize")
async def onboarding_summarize(body: OnboardingPathRequest):
    """D10 — LM-write a 1-paragraph orientation. Best-effort; returns
    ok=False with `error` if LM Studio is unreachable."""
    import asyncio
    from agent.core import onboarding
    return await asyncio.to_thread(onboarding.summarize, body.path)
