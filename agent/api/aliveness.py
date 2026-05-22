"""/aliveness, /curiosity, /digest, /morning, /learning, /emotion,
/thought, /thoughts, /theater, /personality, /health-score, /self,
/model router.

The "JARVIS feels alive" surface — proactive notifier, curiosity engine,
daily digest + morning briefing, learning tracks, emotion state, theater
narrator, personality snapshot, health score, self-introspection.

URLs unchanged from pre-split main.py.
"""
from __future__ import annotations
import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel

from agent.core import bus
from agent.core.model_router import router as model_router
from agent.core.autonomy import _internal_state

router = APIRouter(tags=["aliveness"])


# ── Aliveness notifier (F3) ──────────────────────────────────────────────────

class AlivenessTickRequest(BaseModel):
    category: str | None = None
    ignore_quiet_hours: bool = False


@router.post("/aliveness/tick")
async def aliveness_tick(req: AlivenessTickRequest):
    """F3 — produce ONE LM-composed notification right now. Normally called
    by the autonomy daemon on a 25 min (active) / 120 min (idle) schedule;
    this endpoint exists for manual debug + Theater wake-the-stage."""
    from agent.aliveness import notifier
    return await asyncio.to_thread(
        notifier.tick, req.category, req.ignore_quiet_hours,
    )


@router.get("/aliveness/history")
async def aliveness_history(limit: int = 50):
    from agent.aliveness import notifier
    return {"notifications": await asyncio.to_thread(notifier.history, limit)}


# ── F4 Self-directed learning tracks ─────────────────────────────────────────

@router.get("/learning/tracks")
async def learning_tracks_list():
    from agent.core import learning_tracks
    return {"tracks": await asyncio.to_thread(learning_tracks.list_tracks)}


@router.get("/learning/tracks/{track_id}")
async def learning_tracks_get(track_id: str):
    from agent.core import learning_tracks
    t = await asyncio.to_thread(learning_tracks.get_track, track_id)
    if not t:
        return {"ok": False, "error": f"unknown track: {track_id}"}
    return t


class LearningCompleteRequest(BaseModel):
    topic: str | None = None


@router.post("/learning/tracks/{track_id}/complete")
async def learning_tracks_complete(track_id: str, req: LearningCompleteRequest):
    """Mark the current (or named) topic complete. Emits learning.completed,
    bumps LEARNING drive down, writes note stub at second_brain/learning/."""
    from agent.core import learning_tracks
    return await asyncio.to_thread(
        learning_tracks.complete_topic, track_id, req.topic,
    )


class LearningStatusRequest(BaseModel):
    status: str   # active | paused | dropped


@router.post("/learning/tracks/{track_id}/status")
async def learning_tracks_status(track_id: str, req: LearningStatusRequest):
    from agent.core import learning_tracks
    return await asyncio.to_thread(learning_tracks.set_status, track_id, req.status)


@router.get("/learning/due")
async def learning_due():
    """Track IDs whose cadence has elapsed — what autonomy daemon spawns."""
    from agent.core import learning_tracks
    return {"due": await asyncio.to_thread(learning_tracks.due_tracks)}


# ── F6 Daily digest ──────────────────────────────────────────────────────────

@router.get("/digest/today")
async def digest_today():
    """Return today's digest if composed; otherwise the empty stub."""
    from agent.core import daily_digest
    return await asyncio.to_thread(daily_digest.today)


class DigestComposeRequest(BaseModel):
    date: str | None = None
    force: bool = False


@router.post("/digest/compose")
async def digest_compose(req: DigestComposeRequest):
    """Force-compose a digest now (default: today). force=True overwrites."""
    from agent.core import daily_digest
    return await asyncio.to_thread(daily_digest.compose, req.date, force=req.force)


# ── Curiosity (F2) ───────────────────────────────────────────────────────────

@router.get("/curiosity/queue")
async def curiosity_queue(limit: int = 20, state: str = "open"):
    """F2 — Curiosity queue. State: open / acted / dismissed / faded / all.
    Newest-first."""
    from agent.core import curiosity
    return await asyncio.to_thread(curiosity.queue, limit, state)


@router.post("/curiosity/generate")
async def curiosity_generate(max_new: int = 5):
    """F2 — Run the curiosity-generation pass. Idempotent."""
    from agent.core import curiosity
    return await asyncio.to_thread(curiosity.generate, max_new)


class CuriosityActRequest(BaseModel):
    outcome: str | None = None


@router.post("/curiosity/{item_id}/act")
async def curiosity_act(item_id: str, req: CuriosityActRequest):
    from agent.core import curiosity
    return await asyncio.to_thread(curiosity.act, item_id, req.outcome)


@router.post("/curiosity/{item_id}/dismiss")
async def curiosity_dismiss(item_id: str):
    from agent.core import curiosity
    return await asyncio.to_thread(curiosity.dismiss, item_id)


@router.get("/curiosity/stats")
async def curiosity_stats():
    from agent.core import curiosity
    return await asyncio.to_thread(curiosity.stats)


# ── Health Score (D1) ────────────────────────────────────────────────────────

@router.get("/health-score")
async def health_score():
    """D1 — JARVIS Health Score. One number 0-100 blending Code Health,
    Memory Garden freshness, and Drives balance."""
    from agent.core import health_score as hs
    return await asyncio.to_thread(hs.compute)


# ── Self-introspection (B6.9) ────────────────────────────────────────────────

@router.get("/self/module")
async def self_module(id: str, max_lines: int = 400):
    """B6.9 — JARVIS reads its own source. Returns the file(s) implementing
    MASTER_PLAN item `id` (e.g. C6.1, D2.2)."""
    from agent.core import self_introspection
    return await asyncio.to_thread(self_introspection.module_for_item, id, max_lines)


@router.get("/self/recent-changes")
async def self_recent_changes(days: int = 7, max_files: int = 25):
    """B6.9 — files in the jarvis repo touched in the last N days."""
    from agent.core import self_introspection
    return await asyncio.to_thread(self_introspection.recent_changes, days, max_files)


class SelfExplainRequest(BaseModel):
    item_id: str
    model: str | None = None


@router.post("/self/explain")
async def self_explain(req: SelfExplainRequest):
    """B6.9 — generate a plain-English explanation of what a MASTER_PLAN item
    does, grounded in its actual source code."""
    from agent.core import self_introspection
    from agent.core.lm_studio import get_client
    ep = await asyncio.to_thread(self_introspection.explain_prompt, req.item_id)
    if "error" in ep:
        return ep
    try:
        client = get_client()
        resp = await client.chat.completions.create(
            model=req.model or "local",
            messages=[{"role": "user", "content": ep["prompt"]}],
            max_tokens=1500,
            temperature=0.3,
        )
        explanation = resp.choices[0].message.content or ""
    except Exception as e:
        return {**ep, "explanation": None, "error": f"LM Studio call failed: {e}"}
    return {
        "item_id":      req.item_id,
        "source_files": ep["source_files"],
        "source_bytes": ep["source_bytes"],
        "explanation":  explanation.strip(),
    }


# ── Morning briefing (D3) ────────────────────────────────────────────────────

@router.get("/morning/today")
async def morning_today():
    """D3 — return today's morning briefing markdown (empty if not yet
    composed). The maintenance cycle composes it after 08:00 local."""
    from agent.aliveness import morning_briefing
    md = morning_briefing.read_today()
    return {"markdown": md, "composed": bool(md)}


@router.post("/morning/compose")
async def morning_compose(force: bool = False):
    """D3 — explicitly compose (or recompose) today's brief now."""
    from agent.aliveness import morning_briefing
    return await morning_briefing.compose(force=force)


# ── Emotion state (3.2) ──────────────────────────────────────────────────────

@router.get("/emotion/state")
async def emotion_state():
    """Current JARVIS internal state vector."""
    return _internal_state.snapshot()


@router.post("/emotion/nudge")
async def emotion_nudge(body: dict):
    """Manually nudge an emotion dimension (for testing / UI control)."""
    dim = body.get("dim", "")
    delta = float(body.get("delta", 0.0))
    reason = body.get("reason", "manual")
    _internal_state.nudge(dim, delta, reason)
    return _internal_state.snapshot()


@router.get("/emotion/transparency")
async def emotion_transparency():
    """B6.1 — payload for the Settings transparency panel: dominant
    mood + intensity %, every dim's level, compound moods, the top 3
    triggers cited in the last 24h, and which dims can be reset."""
    return _internal_state.transparency()


class EmotionResetBody(BaseModel):
    dim: str


@router.post("/emotion/reset")
async def emotion_reset(body: EmotionResetBody):
    """B6.1 — reset one emotion dimension to baseline. The Settings
    transparency panel's "reset trait X" knob calls this."""
    _internal_state.reset(body.dim, reason="settings panel reset")
    return _internal_state.transparency()


# ── Model routing stats (1.8) ────────────────────────────────────────────────

@router.get("/model/stats")
async def model_stats():
    """Performance history per model+tier combo."""
    return {"stats": model_router.stats()}


# ── Thought broadcast (3.4) ──────────────────────────────────────────────────

class ThoughtBody(BaseModel):
    thought: str
    actor: str = "jarvis"
    priority: str = "low"  # low | medium | high


@router.post("/thought")
async def broadcast_thought(body: ThoughtBody):
    """Publish a thought snippet to the bus (surfaces in live event feed)."""
    bus.publish("thought.broadcast", body.actor, {
        "thought": body.thought[:500], "priority": body.priority,
    })
    return {"ok": True}


@router.get("/thoughts/recent")
async def recent_thoughts(limit: int = Query(default=20)):
    """Last N thought broadcasts."""
    return {"thoughts": bus.recent(limit, topic_prefix="thought.")}


# ── Reflections (1A — agentic reflection layer) ──────────────────────────────

@router.get("/reflections")
async def reflections_recent(limit: int = Query(default=50)):
    """Newest-first reflections from `~/.jarvis/reflections.jsonl`. Each
    entry was written by `agent.core.reflection.reflect` after a
    gateway/swarm turn — score, verdict, lesson."""
    from agent.core import reflection
    return {"reflections": reflection.read_recent(limit)}


# ── Theater narrator (D2) ────────────────────────────────────────────────────

@router.get("/theater/recent")
async def theater_recent(limit: int = 50, topic: str = ""):
    """Watch JARVIS Think (Part D2) — recent bus events shaped into
    character-driven narratives. Each actor has its own voice/color/tone."""
    from agent.core import narrator
    return {
        "narratives": await asyncio.to_thread(narrator.recent, limit, topic),
        "personas": narrator.PERSONAS,
    }


# ── Personality snapshot (3.6) ───────────────────────────────────────────────

@router.get("/personality/traits")
async def personality_traits():
    """B6.6 — long-running personality signals: topic affinities, working-
    hours histogram, tool preferences, comm style. Read on demand by the
    Settings personality panel. Refreshed on the autonomy maintenance
    cycle (every 10 min)."""
    from agent.core import personality_traits as pt
    return pt.snapshot()


@router.post("/personality/traits/refresh")
async def personality_traits_refresh():
    """B6.6 — force-resample now (bypasses the 10-min rate limit)."""
    from agent.core import personality_traits as pt
    return pt.refresh(force=True)


class PersonalityResetBody(BaseModel):
    trait: str   # topic_affinity | working_hours | tool_preferences | comm_style


@router.post("/personality/traits/reset")
async def personality_traits_reset(body: PersonalityResetBody):
    """B6.6 — reset one trait to defaults (Settings 'reset trait X')."""
    from agent.core import personality_traits as pt
    return pt.reset_trait(body.trait)


@router.get("/response-style")
async def response_style_current():
    """B6.6 — current ResponseStyle derived from personality_traits.
    Shows what reply-shaping the gateway is applying right now."""
    from agent.core import response_style
    return response_style.compute().as_dict()


@router.get("/personality")
async def personality_snapshot():
    """JARVIS learned preferences and working patterns from model routing
    history."""
    stats = model_router.stats()
    tier_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    for key, rec in stats.items():
        model, tier = key.split("::", 1)
        tier_counts[tier] = tier_counts.get(tier, 0) + rec["calls"]
        model_counts[model] = model_counts.get(model, 0) + rec["calls"]

    preferred_tier = max(tier_counts, key=lambda k: tier_counts[k]) if tier_counts else "medium"
    preferred_model = max(model_counts, key=lambda k: model_counts[k]) if model_counts else "unknown"
    emotion = _internal_state.snapshot()

    return {
        "preferred_task_tier": preferred_tier,
        "preferred_model": preferred_model,
        "tier_distribution": tier_counts,
        "model_distribution": model_counts,
        "dominant_emotion": emotion["dominant"],
        "emotion_state": emotion["state"],
        "total_tasks_routed": sum(tier_counts.values()),
    }


# ─── C14.1 — Skill library ────────────────────────────────────────────────────
# IMPORTANT: `/skills/search` MUST be declared before `/skills/{slug}` so
# FastAPI matches the literal path first. Same bug pattern that hit
# /confirm/preview in B5 — caught here before it cost a debugging hour.

@router.get("/skills")
async def skills_list(limit: int = 50):
    """C14.1 — recent distilled skills (newest first). Each skill is a
    short markdown note under ~/jarvis/memory/skills/ describing an
    approach that worked, derived from a past agent.completed event."""
    from agent.aliveness import skill_distiller
    return {"skills": skill_distiller.list_skills(limit)}


@router.get("/skills/search")
async def skills_search(q: str, limit: int = 10):
    """C14.1 — keyword search across task_desc + slug. Director hook."""
    from agent.aliveness import skill_distiller
    return {"hits": skill_distiller.search(q, limit), "query": q}


@router.get("/skills/{slug}")
async def skill_get(slug: str):
    """C14.1 — read one skill's frontmatter + body."""
    from agent.aliveness import skill_distiller
    s = skill_distiller.get_skill(slug)
    if s is None:
        return {"ok": False, "error": "not found"}
    return {"ok": True, "skill": s}
