"""
C14.1 — Closed-loop skill generation (Hermes-style).

After a successful agent task, distill what worked into a named,
reusable "skill" — a short markdown note stored under
``~/jarvis/memory/skills/<slug>.md``. Skills accumulate over time and
can be matched against future goals (Director integration is a
follow-up; today the library is just browsable).

A skill captures:
  - the original task description (what the user / Director asked for)
  - the agent type that handled it
  - a 60-80 word LM-written gist: *what worked* and *when this skill
    applies* — written prescriptively, second person, so the Director
    (or a human) can read it and decide whether to reuse the approach
  - usage_count (incremented when a future task matches and reuses it)

Triggered from a bus subscriber attached to `agent.completed` events,
filtered to non-trivial successful runs. Failures and tiny tasks
(<40 chars description, <60 chars result) are skipped — they rarely
generalize.

Distillation is **opportunistic**: if the LM call fails or times out,
we skip silently. Skills are valuable as a corpus, not as a hard
requirement of the pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from ..core import bus


logger = logging.getLogger("jarvis.skill_distiller")

_SKILLS_DIR = Path.home() / "jarvis" / "memory" / "skills"
_INDEX_FILE = _SKILLS_DIR / "_index.json"

# Tunables — kept narrow on purpose so noise doesn't crowd the library.
_MIN_TASK_DESC_LEN = 40
_MIN_RESULT_LEN = 60
_MAX_INDEX_SIZE = 500           # cap; oldest gets pruned past this
_DISTILL_TIMEOUT_S = 30

_PROMPT = (
    "You are extracting a reusable 'skill' from a completed JARVIS agent "
    "task so the Director can reuse the approach later.\n\n"
    "Write exactly two sections, no headers, no marketing, no apologies:\n"
    "  1. ONE sentence (≤25 words) starting with 'When …' that names the "
    "situation where this skill applies.\n"
    "  2. A 50-70 word how-to paragraph in the imperative (second person) "
    "describing the *approach* that worked. Reference specific tools / "
    "files / patterns when they're load-bearing. No fluff.\n\n"
    "Task description: {task_desc}\n"
    "Agent type: {agent_type}\n"
    "Result excerpt:\n\"\"\"\n{result}\n\"\"\""
)


def _slugify(text: str, max_len: int = 60) -> str:
    """Produce a filesystem-safe slug from arbitrary task text."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s[:max_len] or "skill").rstrip("-")


def _load_index() -> list[dict[str, Any]]:
    try:
        if _INDEX_FILE.exists():
            return json.loads(_INDEX_FILE.read_text())
    except Exception:
        pass
    return []


def _save_index(index: list[dict[str, Any]]) -> None:
    try:
        _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        _INDEX_FILE.write_text(json.dumps(index, indent=2))
    except Exception as e:
        logger.warning("could not save skill index: %s", e)


async def _distill_text(task_desc: str, agent_type: str, result: str) -> str | None:
    """One LM call. Returns the skill markdown body, or None on failure."""
    try:
        from agent.core.lm_studio import get_client
        prompt = _PROMPT.format(
            task_desc=task_desc[:500],
            agent_type=agent_type or "unknown",
            result=result[:1500],
        )
        completion = await asyncio.wait_for(
            get_client().complete(
                [{"role": "user", "content": prompt}],
                max_tokens=220,
                temperature=0.4,
            ),
            timeout=_DISTILL_TIMEOUT_S,
        )
        text = (completion.text or "").strip()
        return text or None
    except (asyncio.TimeoutError, Exception) as e:
        logger.debug("skill distillation failed: %s", e)
        return None


async def distill(
    task_desc: str,
    agent_type: str,
    result: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Produce one skill .md file. Returns metadata + write-path. Skips
    quietly if inputs are too thin or the LM call fails."""
    if len(task_desc) < _MIN_TASK_DESC_LEN or len(result) < _MIN_RESULT_LEN:
        return {"ok": False, "reason": "input too thin"}

    text = await _distill_text(task_desc, agent_type, result)
    if not text:
        return {"ok": False, "reason": "LM unavailable"}

    slug_base = _slugify(task_desc)
    now = time.time()
    # De-dup by exact slug — append timestamp if the same task type
    # produces a second skill so we keep both variants.
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    candidate = _SKILLS_DIR / f"{slug_base}.md"
    if candidate.exists():
        candidate = _SKILLS_DIR / f"{slug_base}-{int(now)}.md"

    frontmatter = (
        f"---\n"
        f"slug: {candidate.stem}\n"
        f"task_desc: {json.dumps(task_desc[:200])}\n"
        f"agent_type: {agent_type}\n"
        f"task_id: {task_id or ''}\n"
        f"created_at: {now}\n"
        f"usage_count: 0\n"
        f"---\n\n"
    )
    candidate.write_text(frontmatter + text + "\n")

    # Update the index (newest-first, capped).
    index = _load_index()
    entry = {
        "slug": candidate.stem,
        "path": str(candidate),
        "task_desc": task_desc[:200],
        "agent_type": agent_type,
        "task_id": task_id,
        "created_at": now,
        "usage_count": 0,
    }
    index.insert(0, entry)
    if len(index) > _MAX_INDEX_SIZE:
        # Drop the oldest excess entries. Don't delete the files —
        # they remain on disk for forensic reads but stop showing in
        # the API list.
        index = index[:_MAX_INDEX_SIZE]
    _save_index(index)

    bus.publish("skill.distilled", "skill_distiller", {
        "slug": candidate.stem, "agent_type": agent_type, "task_id": task_id,
    })
    return {"ok": True, "slug": candidate.stem, "path": str(candidate)}


def list_skills(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent `limit` skills from the index."""
    return _load_index()[:limit]


def get_skill(slug: str) -> dict[str, Any] | None:
    """Read one skill's frontmatter + body. Returns None if missing."""
    path = _SKILLS_DIR / f"{slug}.md"
    if not path.exists():
        return None
    try:
        raw = path.read_text()
    except OSError:
        return None
    # Lightweight frontmatter parse — keys we wrote, plus body.
    meta: dict[str, Any] = {}
    body = raw
    if raw.startswith("---\n"):
        try:
            end = raw.index("\n---\n", 4)
            fm = raw[4:end]
            for line in fm.splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                v = v.strip()
                if v.startswith('"') and v.endswith('"'):
                    v = json.loads(v)
                meta[k.strip()] = v
            body = raw[end + 5:].lstrip("\n")
        except ValueError:
            pass
    return {**meta, "body": body, "path": str(path)}


def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Keyword-match skills against `query` (case-insensitive substring
    across task_desc + slug). Director integration hook — today returns
    a sorted hit list; full embedding-based match is a follow-up."""
    q = query.lower().strip()
    if not q:
        return []
    hits: list[dict[str, Any]] = []
    for entry in _load_index():
        haystack = (entry.get("task_desc", "") + " " + entry.get("slug", "")).lower()
        if q in haystack:
            hits.append(entry)
        if len(hits) >= limit:
            break
    return hits


async def maybe_distill_from_event(
    payload: dict[str, Any],
    reflection_score: float | None = None,
    reflection_lesson: str | None = None,
) -> dict[str, Any]:
    """Bus-handler glue — call this when an `agent.completed` event lands.
    Filters out failures and trivial results, then delegates to distill().
    Safe to call from any context; idempotent if the LM is unreachable.

    `reflection_score` (1A.2): when a `reflection.recorded` event with
    matching parent_id arrives within the buffer window, we score-gate
    distillation on score >= 0.7. Below that, the task technically
    "completed" but didn't actually solve the goal — distilling its
    pattern would pollute the library."""
    status = payload.get("status", "")
    if status and status not in ("DONE", "done", "ok"):
        return {"ok": False, "reason": f"non-success status: {status}"}
    if reflection_score is not None and reflection_score < 0.7:
        return {"ok": False,
                "reason": f"reflection score {reflection_score:.2f} < 0.7"}
    task_desc = payload.get("task_desc", "") or ""
    agent_type = payload.get("agent_type", "") or "unknown"
    result = payload.get("result", "") or ""
    task_id = payload.get("task_id")
    # If we have a reflection lesson, append it to the result so the
    # distilled skill captures the "what worked" insight.
    if reflection_lesson:
        result = (result + f"\n\n[reflection: {reflection_lesson}]").strip()
    return await distill(task_desc, agent_type, result, task_id)


# Buffer window for matching reflection.recorded with agent.completed.
# Both events go to the bus; reflection is fire-and-forget so it lands
# a few seconds after completion. 10s is generous for 7B-instruct.
_PENDING_TTL_S = 10.0


async def run_subscriber_loop() -> None:
    """Long-running coroutine — subscribes to the bus and distills a
    skill from every `agent.completed` event that passes the filter.
    Started fire-and-forget from the FastAPI lifespan.

    Two-event correlation (1A.2): we buffer agent.completed events by
    task_id, and when reflection.recorded lands with matching parent_id
    we distill with the score gate applied. A sweeper fires any
    completions that haven't seen a reflection within 10s using the
    legacy status-only gate.

    Cancellation-safe: catches asyncio.CancelledError and re-raises.
    Crashes inside the LM call are logged but don't kill the loop —
    skill distillation is opportunistic, not load-bearing."""
    q = bus.subscribe(maxsize=200)
    # task_id → (completion_payload, ts) of completions awaiting a matching
    # reflection. Keyed by task_id so re-runs replace old entries.
    pending: dict[str, tuple[dict[str, Any], float]] = {}

    async def _flush_expired():
        now = time.time()
        expired = [tid for tid, (_, ts) in pending.items() if now - ts > _PENDING_TTL_S]
        for tid in expired:
            payload, _ = pending.pop(tid)
            try:
                # No reflection arrived — distill with status-only gate.
                await maybe_distill_from_event(payload)
            except Exception as e:
                logger.warning("expired distill failed: %s", e)

    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=2.0)
            except asyncio.TimeoutError:
                await _flush_expired()
                continue

            topic = msg.get("topic", "")
            if topic == "agent.completed":
                tid = msg.get("task_id") or msg.get("agent_id") or f"_{time.time()}"
                pending[tid] = (msg, time.time())
            elif topic == "reflection.recorded":
                # 1A.2 — also hand the payload to curiosity in case the
                # turn was tagged [CURIOSITY:<id>]. Keeps the close-the-
                # loop wiring in one place until Phase 1B's orchestrator.
                try:
                    from agent.core import curiosity as _cur
                    _cur.apply_reflection(msg)
                except Exception as e:
                    logger.warning("curiosity apply_reflection failed: %s", e)

                pid = msg.get("parent_id")
                if pid and pid in pending:
                    payload, _ = pending.pop(pid)
                    try:
                        await maybe_distill_from_event(
                            payload,
                            reflection_score=msg.get("score"),
                            reflection_lesson=msg.get("lesson"),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning("reflection-gated distill failed: %s", e)
            await _flush_expired()
    except asyncio.CancelledError:
        raise
    finally:
        bus.unsubscribe(q)
