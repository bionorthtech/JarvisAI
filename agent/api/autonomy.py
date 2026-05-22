"""/autonomy, /drives, /wants router.

Owns the autonomy-daemon control surface (level, goals, slots), the live
drive readouts, and the human-facing "wants & needs" feed (3.3).

URLs unchanged from pre-split main.py.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.core import bus, drives
from agent.core.autonomy import autonomy as autonomy_daemon

router = APIRouter(tags=["autonomy"])


# ── Autonomy daemon ──────────────────────────────────────────────────────────

class AutonomyLevelBody(BaseModel):
    level: int


class AutonomyGoalBody(BaseModel):
    goal: str


@router.get("/autonomy/status")
async def autonomy_status():
    return autonomy_daemon.status()


@router.get("/autonomy/slots")
async def autonomy_slots():
    """F5.3 — in-flight autonomous tasks and queued dispatches."""
    return autonomy_daemon.slots()


@router.post("/autonomy/level")
async def autonomy_set_level(body: AutonomyLevelBody):
    old = autonomy_daemon.level
    result = autonomy_daemon.set_level(body.level)
    bus.publish("config.changed", "main",
                {"key": "autonomy_level", "old": old, "new": body.level})
    return result


@router.post("/autonomy/goals")
async def autonomy_add_goal(body: AutonomyGoalBody):
    return autonomy_daemon.add_goal(body.goal)


@router.delete("/autonomy/goals")
async def autonomy_remove_goal(body: AutonomyGoalBody):
    return autonomy_daemon.remove_goal(body.goal)


@router.get("/autonomy/goals")
async def autonomy_list_goals():
    """D5 — return standing goals enriched with decay metadata so the UI
    can show age, stale badge, and reinforce/drop affordances."""
    return {"goals": autonomy_daemon.goals_with_meta()}


@router.post("/autonomy/goals/reinforce")
async def autonomy_reinforce_goal(body: AutonomyGoalBody):
    """D5 — user explicitly keeps a goal alive (resets decay clock)."""
    return autonomy_daemon.reinforce_goal(body.goal)


# ── Drives ──────────────────────────────────────────────────────────────────

@router.get("/drives")
async def get_drives():
    """Return current drive levels and status summary."""
    return {**drives.get_state(), "summary": drives.status_summary()}


@router.post("/drives/reset/{drive_name}")
async def reset_drive(drive_name: str):
    """Reset a single drive to 0 (e.g., after satisfying it)."""
    ok = drives.reset_drive(drive_name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown drive: {drive_name}")
    return {"reset": drive_name.upper(), "state": drives.get_state()}


@router.post("/drives/bump/{drive_name}")
async def bump_drive(drive_name: str, amount: float = 0.15):
    """Manually decrease a drive level (call after satisfying the drive)."""
    drives.bump(drive_name, amount)
    return {"bumped": drive_name.upper(), "amount": amount, "state": drives.get_state()}


# ── Wants & needs feed (3.3) ─────────────────────────────────────────────────

_WANTS_REGISTRY: list[dict] = [
    {"id": "lm_studio", "want": "LM Studio running with a model loaded", "check": "lm_studio_online"},
    {"id": "chromadb", "want": "ChromaDB indexed with project files", "check": "chromadb_populated"},
    {"id": "offline_mode", "want": "Stay offline — no internet access", "check": "offline_mode"},
]


async def _check_want(want_id: str) -> str:
    """Return 'satisfied' | 'unmet' | 'unknown' for a want."""
    try:
        if want_id == "lm_studio_online":
            from agent.core.lm_studio import get_client
            client = get_client()
            s = await client.check_connection()
            return "satisfied" if s.reachable else "unmet"
        if want_id == "chromadb_populated":
            from agent.core import memory as mem
            stats = mem.ltm_stats()
            return "satisfied" if stats.get("ltm_entries", 0) > 0 else "unmet"
        if want_id == "offline_mode":
            # JARVIS is offline-only by design — confirm SecurityConfig
            # hasn't been flipped. "satisfied" means we are properly offline.
            from agent.core.config import config
            return "satisfied" if not config.security.internet_access else "unmet"
    except Exception:
        pass
    return "unknown"


@router.get("/wants")
async def wants_feed():
    """Return JARVIS's current wants & needs with live satisfaction checks."""
    results = []
    for w in _WANTS_REGISTRY:
        status = await _check_want(w["check"])
        results.append({**w, "status": status})
    return {"wants": results}
