"""/agents, /swarm, /tasks router.

Owns the typed-agent surface: director goal submission, agent catalog,
swarm history, and task replay (D6).

URLs unchanged from pre-split main.py.
"""
from __future__ import annotations
import asyncio
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from agent.core import bus
from agent.core.swarm import director as swarm_director

router = APIRouter(tags=["agents"])


class SwarmGoalBody(BaseModel):
    goal: str
    depth: int = 0


@router.post("/swarm/run")
async def swarm_run(body: SwarmGoalBody):
    """Submit a goal to the Director. Returns immediately with a task ID;
    result streams via /ws/live."""
    async def _bg():
        result = await swarm_director.run_goal(body.goal, depth=body.depth)
        bus.publish("director.result", "director",
                    {"goal": body.goal[:200], "result": result})

    asyncio.create_task(_bg())
    return {"ok": True,
            "message": "Goal submitted to director — watch /ws/live for updates"}


@router.get("/swarm/status")
async def swarm_status():
    """Current director and agent pool status."""
    return swarm_director.status()


@router.get("/swarm/history")
async def swarm_history(limit: int = Query(default=50)):
    """Recent bus messages — full swarm event log."""
    return {"events": bus.recent(limit)}


@router.get("/agents/list")
async def agents_list():
    """List typed agents and their F1.2 autonomy contract."""
    from agent.core import swarm

    def _autonomy(cls) -> dict[str, Any]:
        return {
            "min_autonomy_level": getattr(cls, "min_autonomy_level", 99),
            "wake_conditions":    list(getattr(cls, "wake_conditions", []) or []),
            "skills":             list(getattr(cls, "skills", []) or []),
        }

    return {
        "agents": [
            {"name": cls.name, "agent_type": cls.agent_type, **_autonomy(cls)}
            for cls in swarm._AGENT_TYPES
        ],
    }


@router.get("/tasks/recent")
async def tasks_recent(limit: int = 30):
    """D6 — list the most recent unique task_ids seen in the bus, with
    task_desc + agent_type + event_count. Populates the replay picker."""
    return {"tasks": bus.recent_task_ids(limit=limit)}


@router.get("/tasks/{task_id}/replay")
async def task_replay(task_id: str, limit: int = 500):
    """D6 — return every persisted bus event for a given task_id,
    oldest-first, so the UI can re-render the full task lifecycle."""
    events = bus.by_task_id(task_id, limit=limit)
    return {"task_id": task_id, "events": events, "count": len(events)}
