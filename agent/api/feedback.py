"""1C — /feedback router.

UI reaction signals come in here and land in the style_learner ring
buffer. Today the gateway emits server-side "retry" detection; the
frontend will start pushing "stop"/"copied"/"dismissed" in 1C.2 once
the new design system lands.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from agent.core import style_learner

router = APIRouter(tags=["feedback"])


class TurnFeedback(BaseModel):
    kind: str   # retry | stop | copied | continue | dismissed
    # Optional turn-time snapshot of the style state — lets the
    # learner classify which axis the signal applied to without
    # racing the personality_traits refresh cadence.
    style_snapshot: dict | None = None


@router.post("/feedback/turn/{turn_id}")
async def feedback_turn(turn_id: str, body: TurnFeedback):
    """Record one user reaction against `turn_id`. Idempotent on a
    duplicate (turn_id, kind) pair — only the first counts. Returns
    `{ok: False}` on invalid `kind`."""
    ok = style_learner.record_signal(
        turn_id, body.kind, style_snapshot=body.style_snapshot,
    )
    return {"ok": ok}


@router.post("/personality/adjustments/reset")
async def reset_adjustments():
    """1C — clear all learned style adjustments. Anchor preferences
    (terse/verbose/code-first/tone) survive — they're sampled fresh
    from `personality_traits`."""
    return style_learner.reset_adjustments()
