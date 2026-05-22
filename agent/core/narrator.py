"""
Narrator (D2 — Watch JARVIS Think)

Turns raw bus events into character-driven narrative speech. Each actor
(Director, MemoryGardener, CodeHealthMonitor, ...) has its own voice:
Director is formal, MemoryGardener uses garden metaphors, Health is a
diagnostic physician.

Consumed by:
  - GET  /theater/recent?limit=N    → recent N narratives, newest-first.
  - WS   /ws/theater                → live stream as new events flow.

Pure-function — `narrate(raw_event)` takes a bus message dict and returns
a narrative dict `{actor, voice, saying, kind, ts, raw_topic, raw_sender}`.

The frontend pane renders these as chat bubbles attributed to the actor.

Note: this is intentionally OPINIONATED — boring "agent X published event Y"
is what bus.recent already gives you. The point of D2 is to make watching
JARVIS think feel like watching a small ensemble cast.
"""
from __future__ import annotations

import time
from typing import Any

from . import bus


# ── Actor personas ──────────────────────────────────────────────────────────

PERSONAS: dict[str, dict[str, str]] = {
    "director": {
        "name":   "Director",
        "voice":  "formal",
        "color":  "amber",
        "tone":   "precise, delegating, mission-control",
    },
    "MemoryGardener": {
        "name":   "Gardener",
        "voice":  "botanical",
        "color":  "lime",
        "tone":   "pastoral, patient, talks about pruning + soil",
    },
    "CodeHealthMonitor": {
        "name":   "Health",
        "voice":  "physician",
        "color":  "cyan",
        "tone":   "diagnostic, charts vitals, gentle nags",
    },
    "PerformanceWatchdog": {
        "name":   "Watchdog",
        "voice":  "coach",
        "color":  "blue",
        "tone":   "performance-obsessed, p99-aware, calls out regressions",
    },
    "KnowledgeCurator": {
        "name":   "Curator",
        "voice":  "librarian",
        "color":  "rose",
        "tone":   "curious, queues doc-wishes, gentle reminders",
    },
    "Curiosity": {
        "name":   "Curiosity",
        "voice":  "wanderer",
        "color":  "amber",
        "tone":   "wonders out loud; brings up topics it wants to learn",
    },
    "HealthScore": {
        "name":   "Pulse",
        "voice":  "dashboard",
        "color":  "green",
        "tone":   "one-number summary of how well JARVIS is feeling",
    },
    "lm_progress": {
        "name":   "Cortex",
        "voice":  "neurologist",
        "color":  "indigo",
        "tone":   "narrates the model's own processing — prompt, reasoning, generation",
    },
    "daemon": {
        "name":   "Autonomy",
        "voice":  "narrator",
        "color":  "slate",
        "tone":   "narrates lifecycle without flourish",
    },
    "sidecar": {
        "name":   "Sidecar",
        "voice":  "telemetry",
        "color":  "slate",
        "tone":   "streams metrics, doesn't editorialize",
    },
}

_DEFAULT_PERSONA = {"name": "?", "voice": "neutral", "color": "gray",
                    "tone": "unattributed"}


# ── Phrase shapers per topic ────────────────────────────────────────────────

def _phrase_thought_broadcast(payload: dict, sender: str) -> str:
    thought = payload.get("thought") or payload.get("text") or ""
    prio = payload.get("priority", "normal")
    if prio == "high":
        return f"💭 {thought}"
    return f"({thought})"


def _phrase_agent_lifecycle(payload: dict, sender: str) -> str:
    topic = payload.get("topic", "")
    task = payload.get("task_id") or payload.get("desc", "")
    agent = payload.get("agent") or payload.get("agent_type", "agent")
    if topic.endswith(".spawned"):
        return f"→ {agent} spawned for: {task}."
    if topic.endswith(".started"):
        return f"{agent} is running: {task}."
    if topic.endswith(".completed"):
        return f"{agent} done. ✓"
    if topic.endswith(".failed"):
        return f"{agent} hit a wall — {payload.get('error','?')}."
    return f"{agent}: {topic}"


def _phrase_curiosity(payload: dict, sender: str) -> str:
    subject = payload.get("subject", "?")
    bus_topic = payload.get("topic", "")
    if bus_topic == "curiosity.generated":
        why = payload.get("why_now", "")
        return f"💭 I'd like to learn about `{subject}` — {why}"
    if bus_topic == "curiosity.acted":
        outcome = payload.get("outcome") or "noted"
        return f"Followed up on `{subject}` — {outcome}."
    if bus_topic == "curiosity.dismissed":
        return f"Set aside `{subject}` — not now."
    if bus_topic == "curiosity.faded":
        return f"`{subject}` faded — never got to it."
    return f"curiosity about `{subject}`"


def _phrase_health_score(payload: dict, sender: str) -> str:
    score = payload.get("score", "?")
    mood  = payload.get("mood", "")
    return f"My health: {score}/100 — feeling {mood}."


def _phrase_autonomy(payload: dict, sender: str) -> str:
    topic = payload.get("topic", "")
    if topic == "autonomy.level_changed":
        old, new = payload.get("old"), payload.get("new")
        names = ["Off", "Maintenance", "Proactive", "Full Auto"]
        return f"Autonomy: {names[old]} → {names[new]}."
    return f"autonomy: {topic}"


def _phrase_goal(payload: dict, sender: str) -> str:
    """D5 — surface goal lifecycle, especially the stale alert."""
    topic = payload.get("topic", "")
    goal = (payload.get("goal") or "")[:80]
    if topic == "goal.stale":
        age = payload.get("age_days", "?")
        return f'Standing goal "{goal}" has gone {age}d without reinforcement — keep, re-scope, or drop?'
    if topic == "goal.added":
        return f'New standing goal: "{goal}".'
    if topic == "goal.dropped":
        return f'Dropped standing goal: "{goal}".'
    return ""


def _phrase_system_metrics(payload: dict, sender: str) -> str:
    # System metrics every 15s — too noisy. We DROP these unless something
    # stands out (CPU > 90, RAM > 90, disk > 90).
    cpu  = payload.get("cpu_pct", 0)
    ram  = payload.get("ram_pct", 0)
    disk = payload.get("disk_pct", 0)
    if cpu > 90 or ram > 90 or disk > 90:
        return f"CPU {cpu}% · RAM {ram}% · disk {disk}% — running hot."
    return ""  # suppressed


def _phrase_lm_progress(payload: dict, sender: str) -> str:
    """G6.4 — surface only the interesting LM phases. Suppress quiet
    progress ticks (the chat UI's strip handles those); narrate when
    something feels slow or notable.
    """
    phase = payload.get("phase", "")
    if phase == "thinking_done":
        sec = float(payload.get("seconds", 0))
        if sec >= 5.0:
            return f"reasoned for {sec:.1f}s — that was a slow one."
        return ""  # quick reasoning, no need to voice
    if phase == "request_complete":
        toks = int(payload.get("n_tokens", 0))
        if toks > 4000:
            return f"that turn used {toks} tokens — getting near the window."
        return ""
    # prompt_processing / thinking_start are noisy — drop
    return ""


# Sender-prefix → handler map. Order matters for prefix matching.
_PHRASE_HANDLERS: list[tuple[str, Any]] = [
    ("thought.",      _phrase_thought_broadcast),
    ("agent.",        _phrase_agent_lifecycle),
    ("autonomy.",     _phrase_autonomy),
    ("goal.",         _phrase_goal),
    ("curiosity.",    _phrase_curiosity),
    ("health.",       _phrase_health_score),
    ("lm.progress",   _phrase_lm_progress),
    ("system.metrics", _phrase_system_metrics),
]


def _saying_for(topic: str, payload: dict, sender: str) -> str:
    for prefix, handler in _PHRASE_HANDLERS:
        if topic.startswith(prefix):
            payload_with_topic = {**payload, "topic": topic}
            return handler(payload_with_topic, sender)
    # Fallback: condense the payload
    fields = {k: v for k, v in payload.items() if k not in ("id", "ts", "topic", "sender")}
    if not fields:
        return topic
    return f"{topic} — " + ", ".join(f"{k}={v}" for k, v in list(fields.items())[:3])


# ── Public API ──────────────────────────────────────────────────────────────

def narrate(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one raw bus message into a narrative dict.

    Returns None for events that should be suppressed (e.g. quiet
    system.metrics ticks).
    """
    topic = raw.get("topic", "")
    sender = raw.get("sender", "?")
    payload = {k: v for k, v in raw.items() if k not in ("topic", "sender")}

    saying = _saying_for(topic, payload, sender)
    if not saying:
        return None

    persona = PERSONAS.get(sender, _DEFAULT_PERSONA)
    kind = _kind_for(topic)

    return {
        "id":           raw.get("id"),
        "ts":           raw.get("ts", time.time()),
        "actor":        persona["name"],
        "voice":        persona["voice"],
        "color":        persona["color"],
        "tone":         persona["tone"],
        "saying":       saying,
        "kind":         kind,
        "raw_topic":    topic,
        "raw_sender":   sender,
    }


def _kind_for(topic: str) -> str:
    if topic.startswith("thought."):                    return "thought"
    if topic.endswith(".report") or topic.endswith(".scan_complete"): return "report"
    if topic.endswith(".finding"):                       return "finding"
    if topic.endswith(".quarantine"):                    return "action"
    if topic.endswith(".spawned") or topic.endswith(".started") \
        or topic.endswith(".completed") or topic.endswith(".failed"): return "handoff"
    if topic.startswith("autonomy."):                    return "status"
    if topic.startswith("system."):                      return "telemetry"
    return "event"


def recent(limit: int = 50, topic_prefix: str = "") -> list[dict[str, Any]]:
    """Recent bus messages, narrated. Newest first. Drops suppressed items."""
    out: list[dict[str, Any]] = []
    for raw in bus.recent(limit * 2, topic_prefix):  # over-fetch since we drop some
        n = narrate(raw)
        if n is not None:
            out.append(n)
        if len(out) >= limit:
            break
    return out
