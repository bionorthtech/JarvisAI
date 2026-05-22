"""/analytics, /perf, /probe, /lm router.

Analytics + performance instrumentation surface. Owns latency probes,
reasoning-effort knob, perf compare (G4.2), live latency snapshot,
dep-graph, output summarization, and the LM Studio progress SSE relay.
"""
from __future__ import annotations
import asyncio
import json
import os
import time as _t
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.core import bus

router = APIRouter(tags=["analytics"])


# ── Analytics ────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def get_analytics():
    """Full analytics snapshot: LM latency, token budget, GPU, CPU/RAM."""
    from agent.core import analytics
    from agent.core.lm_studio import get_client
    return await analytics.get_analytics(lm_client=get_client())


@router.get("/analytics/dep-graph")
async def analytics_dep_graph(scope: str = Query(default="internal", regex="^(internal|all)$")):
    """Code dependency graph. Returns force-graph data of
    JARVIS Python modules and their import edges."""
    from agent.core import dep_graph
    return await asyncio.to_thread(dep_graph.graph, None, scope)


class SummarizeRequest(BaseModel):
    text: str
    source: str = "agent"


@router.post("/analytics/summarize")
async def analytics_summarize(body: SummarizeRequest):
    """Structured output summarization. Parses Python tracebacks,
    TS compile errors, vite output, shell failures into root_cause / location
    / suggested_fix."""
    from agent.core import output_summarizer
    return output_summarizer.summarize(body.text, body.source)


# ── LM Studio progress (G6.2) ────────────────────────────────────────────────

@router.get("/lm/progress/stream")
async def lm_progress_stream():
    """G6.2 — SSE relay of `lm.progress` bus events. Frontend chat pane
    subscribes and renders the current model phase (prompt processing %,
    reasoning seconds, generation tokens) inline."""
    queue = bus.subscribe(maxsize=200)

    async def generate():
        try:
            while True:
                evt = await queue.get()
                if evt.get("topic") != "lm.progress":
                    continue
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Reasoning effort knob (G6.6) ─────────────────────────────────────────────

@router.get("/perf/reasoning-effort")
async def perf_reasoning_get():
    """G6.6 — current reasoning_effort setting for the LM."""
    from agent.core.lm_studio import DEFAULT_REASONING_EFFORT
    return {"reasoning_effort": os.environ.get(
        "JARVIS_REASONING_EFFORT", DEFAULT_REASONING_EFFORT,
    )}


class ReasoningEffortRequest(BaseModel):
    effort: str   # low | medium | high | none


@router.post("/perf/reasoning-effort")
async def perf_reasoning_set(req: ReasoningEffortRequest):
    """Set the runtime reasoning_effort. Affects every subsequent LM call."""
    from agent.core.lm_studio import DEFAULT_REASONING_EFFORT
    effort = req.effort.lower().strip()
    if effort not in ("low", "medium", "high", "none"):
        return {"ok": False, "error": "effort must be low|medium|high|none"}
    old = os.environ.get("JARVIS_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)
    os.environ["JARVIS_REASONING_EFFORT"] = effort
    bus.publish("config.reasoning_effort_changed", "main",
                {"new": effort, "ts": _t.time()})
    bus.publish("config.changed", "main",
                {"key": "reasoning_effort", "old": old, "new": effort})
    return {"ok": True, "reasoning_effort": effort}


class PerfWsPing(BaseModel):
    token: str


@router.post("/perf/ws-ping")
async def perf_ws_ping(body: PerfWsPing):
    """Publish a `perf.ws_ping` bus event carrying `token` so
    `scripts/perf_bench.py` can measure true WS publish→recv RTT.
    Cheap (no I/O beyond the bus put)."""
    bus.publish("perf.ws_ping", "perf_bench", {"token": body.token})
    return {"ok": True}


@router.get("/perf/live")
async def perf_live():
    """G3.3 — live latency snapshot (LM + ChromaDB + WS p50/p95/p99 with
    sample counts). Reads the same in-memory deques the watchdog uses;
    no bot run required."""
    from agent.bots.performance_watchdog import watchdog
    return await asyncio.to_thread(watchdog._collect_metrics)


# ── Probe (G5.2) ─────────────────────────────────────────────────────────────

@router.get("/probe/all")
async def probe_all():
    """G5.2 — unified connection probe across the 4 services Settings can
    test. Each result is `{ok, latency_ms, detail}`. All four run in parallel."""

    async def _lm():
        t0 = _t.monotonic()
        from agent.core.lm_studio import get_client
        try:
            s = await get_client().check_connection()
            return {"ok": s.reachable, "latency_ms": round((_t.monotonic() - t0) * 1000, 1),
                    "detail": f"{len(s.models)} model(s)" if s.reachable else (s.error or "unreachable")}
        except Exception as e:
            return {"ok": False, "latency_ms": round((_t.monotonic() - t0) * 1000, 1), "detail": str(e)}

    async def _chroma():
        t0 = _t.monotonic()
        try:
            from agent.core import memory as mem
            stats = await asyncio.to_thread(mem.get_stats, "default")
            return {"ok": True, "latency_ms": round((_t.monotonic() - t0) * 1000, 1),
                    "detail": f"{stats.get('file_chunks', 0)} chunks · {stats.get('chat_turns', 0)} turns"}
        except Exception as e:
            return {"ok": False, "latency_ms": round((_t.monotonic() - t0) * 1000, 1), "detail": str(e)}

    async def _audit():
        t0 = _t.monotonic()
        try:
            from agent.core import audit as _adt
            ok, message = await asyncio.to_thread(_adt.verify_chain)
            return {"ok": ok, "latency_ms": round((_t.monotonic() - t0) * 1000, 1), "detail": message}
        except Exception as e:
            return {"ok": False, "latency_ms": round((_t.monotonic() - t0) * 1000, 1), "detail": str(e)}

    lm, chroma, adt = await asyncio.gather(_lm(), _chroma(), _audit())
    return {"lm_studio": lm, "chromadb": chroma, "audit_db": adt}


# ── Perf compare (G4.2) ──────────────────────────────────────────────────────

class PerfCompareRequest(BaseModel):
    prompt: str
    model: Optional[str] = None


@router.post("/perf/compare")
async def perf_compare(req: PerfCompareRequest):
    """G4.2 — fan the same prompt out to (a) direct LM Studio and (b) the
    full JARVIS pipeline. Returns side-by-side latencies so the user can see
    exactly where JARVIS spends time beyond the raw LM call."""
    from agent.core.lm_studio import get_client
    from agent.core import gateway
    client = get_client()

    async def _direct():
        t0 = _t.monotonic()
        try:
            r = await client.complete(
                [{"role": "user", "content": req.prompt}], model=req.model,
            )
            text = r.text or ""
            err = None
        except Exception as e:
            text, err = "", str(e)
        return {"ms": round((_t.monotonic() - t0) * 1000, 1), "text": text, "error": err}

    async def _full():
        t0 = _t.monotonic()
        breakdown = None
        final = ""
        err = None
        try:
            async for ev in gateway.ask_events(req.prompt, history=[], model=req.model):
                if ev.get("type") == "text":
                    final = ev.get("content", "")
                elif ev.get("type") == "perf":
                    breakdown = ev.get("breakdown")
                elif ev.get("type") == "error":
                    err = ev.get("message")
        except Exception as e:
            err = str(e)
        return {"ms": round((_t.monotonic() - t0) * 1000, 1), "text": final,
                "error": err, "breakdown": breakdown}

    direct, full = await asyncio.gather(_direct(), _full())
    return {"prompt": req.prompt, "direct": direct, "full": full,
            "delta_ms": round(full["ms"] - direct["ms"], 1)}
