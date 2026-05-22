"""/bots router.

All endpoints under the `/bots` prefix. Owns the scheduled-bot triggers
and the homelab warden's user-initiated restart action.
"""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(tags=["bots"])


# ── Scheduled bots ───────────────────────────────────────────────────────────

@router.post("/bots/memory-gardener/run")
async def memory_gardener_run():
    """Trigger a Memory Gardener scan immediately."""
    from agent.bots.memory_gardener import gardener
    return await asyncio.to_thread(gardener.run)


@router.post("/bots/code-health/run")
async def code_health_run():
    """Trigger a Code Health Monitor scan immediately."""
    from agent.bots.code_health import code_monitor
    return await asyncio.to_thread(code_monitor.run)


@router.post("/bots/performance-watchdog/run")
async def performance_watchdog_run():
    """Trigger a Performance Watchdog scan immediately."""
    from agent.bots.performance_watchdog import watchdog
    return await asyncio.to_thread(watchdog.run)


@router.post("/bots/knowledge-curator/run")
async def knowledge_curator_run():
    """Trigger a Knowledge Curator scan immediately."""
    from agent.bots.knowledge_curator import curator
    return await asyncio.to_thread(curator.run)


@router.post("/bots/homelab-warden/run")
async def homelab_warden_run():
    """Run the homelab warden sweep (failed services, stopped containers,
    journal errors). Read-only — no restart performed."""
    from agent.bots.homelab_warden import warden
    return await asyncio.to_thread(warden.run)


class HomelabRestartRequest(BaseModel):
    kind: str            # "service" | "container"
    id: str              # systemd unit name OR container name
    engine: str | None = None  # only for kind="container": docker|podman


@router.post("/bots/homelab-warden/restart")
async def homelab_warden_restart(body: HomelabRestartRequest):
    """User-initiated only (UI two-click confirm); never autonomous.
    Publishes homelab.restart_ok / homelab.restart_failed on the bus."""
    from agent.bots.homelab_warden import warden
    return await asyncio.to_thread(
        warden.restart, body.kind, body.id, body.engine,
    )


# ── Catalog endpoints ────────────────────────────────────────────────────────

@router.get("/bots/list")
async def bots_list():
    """List all configured bots and their F1.1 autonomy contract."""
    from agent.bots.memory_gardener import MemoryGardener
    from agent.bots.code_health import CodeHealthMonitor
    from agent.bots.performance_watchdog import PerformanceWatchdog
    from agent.bots.knowledge_curator import KnowledgeCurator
    from agent.bots.homelab_warden import HomelabWarden

    def _autonomy(cls) -> dict[str, Any]:
        return {
            "min_autonomy_level": getattr(cls, "min_autonomy_level", 99),
            "wake_conditions":    list(getattr(cls, "wake_conditions", []) or []),
        }

    return {
        "bots": [
            {"id": "memory-gardener", "name": "Memory Gardener",
             "schedule": "nightly 02:00", "endpoint": "/bots/memory-gardener/run",
             **_autonomy(MemoryGardener)},
            {"id": "code-health", "name": "Code Health Monitor",
             "schedule": "weekly Sunday", "endpoint": "/bots/code-health/run",
             **_autonomy(CodeHealthMonitor)},
            {"id": "performance-watchdog", "name": "Performance Watchdog",
             "schedule": "every 6h + weekly full",
             "endpoint": "/bots/performance-watchdog/run",
             **_autonomy(PerformanceWatchdog)},
            {"id": "knowledge-curator", "name": "Knowledge Curator",
             "schedule": "daily + on research.gap",
             "endpoint": "/bots/knowledge-curator/run",
             **_autonomy(KnowledgeCurator)},
            {"id": "homelab-warden", "name": "Homelab Warden",
             "schedule": "every 5 min",
             "endpoint": "/bots/homelab-warden/run",
             **_autonomy(HomelabWarden)},
        ],
    }


@router.post("/bots/run-all")
async def bots_run_all():
    """Run every bot once (used by Bot Control Panel sweep button)."""
    from agent.bots.memory_gardener import gardener
    from agent.bots.code_health import code_monitor
    from agent.bots.performance_watchdog import watchdog
    from agent.bots.knowledge_curator import curator
    from agent.bots.homelab_warden import warden

    results: dict[str, Any] = {}
    for name, bot in [
        ("memory_gardener", gardener),
        ("code_health", code_monitor),
        ("performance_watchdog", watchdog),
        ("knowledge_curator", curator),
        ("homelab_warden", warden),
    ]:
        try:
            results[name] = await asyncio.to_thread(bot.run)
        except Exception as e:
            results[name] = {"error": str(e)[:300]}
    return {"results": results}


@router.get("/bots/reports")
async def bots_reports(limit: int = Query(default=10)):
    """Return recent bot reports from ~/jarvis/reports/."""
    import json as _json
    report_dir = Path.home() / "jarvis" / "reports"
    if not report_dir.exists():
        return {"reports": []}
    files = sorted(report_dir.glob("*.json"), reverse=True)[:limit]
    out = []
    for f in files:
        try:
            out.append({"name": f.name, **_json.loads(f.read_text())})
        except Exception:
            pass
    return {"reports": out}


@router.get("/bots/status")
async def bots_status():
    """Per-bot runtime status for the Bots mode: schedule, last run,
    next due, last status string, autonomy gate, wake conditions.
    Pulls from the autonomy daemon's persistent state."""
    import time as _t
    from agent.core import autonomy as auto_mod
    from agent.core.autonomy import _BOT_SCHEDULE

    # The live daemon is exposed as the module-level `autonomy` singleton.
    try:
        live = auto_mod.autonomy._state
    except Exception:
        live = {}
    bot_last_run    = (live or {}).get("bot_last_run", {})    or {}
    bot_last_status = (live or {}).get("bot_last_status", {}) or {}
    bot_last_error  = (live or {}).get("bot_last_error", {})  or {}
    autonomy_level  = int((live or {}).get("level", 0))
    now = _t.time()

    def _autonomy_meta(bot_id: str) -> dict:
        # Each bot class declares min_autonomy_level + wake_conditions.
        # Resolve via the same path the daemon uses.
        try:
            bot, _err = auto_mod.AutonomyDaemon._resolve_bot(bot_id)
            if bot is None:
                return {"min_autonomy_level": None, "wake_conditions": []}
            cls = type(bot)
            return {
                "min_autonomy_level": getattr(cls, "min_autonomy_level", None),
                "wake_conditions":    list(getattr(cls, "wake_conditions", []) or []),
            }
        except Exception:
            return {"min_autonomy_level": None, "wake_conditions": []}

    bots = []
    for bot_id, interval in _BOT_SCHEDULE.items():
        last = float(bot_last_run.get(bot_id, 0))
        meta = _autonomy_meta(bot_id)
        min_level = meta["min_autonomy_level"]
        eligible = (min_level is None) or (autonomy_level >= min_level)
        bots.append({
            "id": bot_id,
            "interval_s":         interval,
            "last_run_ts":        last or None,
            "last_run_age_s":     int(now - last) if last else None,
            "next_due_ts":        (last + interval) if last else now,
            "due_in_s":           max(0, int((last + interval) - now)) if last else 0,
            "last_status":        bot_last_status.get(bot_id, "never_run"),
            "last_error":         bot_last_error.get(bot_id),
            "min_autonomy_level": min_level,
            "autonomy_eligible":  eligible,
            "wake_conditions":    meta["wake_conditions"],
            "endpoint":           f"/bots/{bot_id.replace('_', '-')}/run",
        })
    return {
        "autonomy_level": autonomy_level,
        "now_ts": now,
        "bots": bots,
    }
