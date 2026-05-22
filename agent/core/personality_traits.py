"""Personality evolution.

JARVIS tracks long-running signals about *how the user works* so its responses
can subtly adapt to her style without ever being silent about it. Every
trait is observable, inspectable, and resettable from the Settings panel.

Tracked traits:
- topic_affinity      — Counter of vault tag + chat topic frequencies.
- working_hours       — histogram of chat / activity timestamps by hour.
- tool_preferences    — per-tool {invocations, completions, cuts} from the
                        audit log. "cuts" = user dismissed mid-flight.
- comm_style          — average length of user chat input. Terse/verbose.

State lives at `~/.jarvis/personality_traits.json` and is updated on a
sampling cadence (every 10 minutes) by `refresh()`, called from the
autonomy maintenance cycle. Read on demand by `/personality/traits`.

Every trait surfaced in the UI is backed by real data, never simulated.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.personality_traits")

from agent.core import bus

_STATE_FILE = Path.home() / ".jarvis" / "personality_traits.json"
_VAULT = Path.home() / ".jarvis" / "brain"
_REFRESH_INTERVAL_S = 10 * 60   # 10 min — cheap, sample-based
_PREFS_INTERVAL_S   = 6 * 3600  # 6h — LM-distilled, expensive

# Comm-style thresholds (chars in user message).
_TERSE_MAX = 60
_VERBOSE_MIN = 200

# C14.2 — Honcho-style user model. New fields:
#   terminology — top bigrams/trigrams from chat (stat, cheap)
#   tone        — emoji/exclaim/caps/question densities (stat, cheap)
#   preferences — LM-distilled likes/dislikes, refreshed every 6h.
#                 Each entry is a free-form one-line statement.
_DEFAULT: dict[str, Any] = {
    "topic_affinity":   {},   # {tag/topic: count}
    "working_hours":    [0] * 24,
    "tool_preferences": {},   # {tool_name: {invocations, completions, cuts}}
    "comm_style": {
        "messages_seen": 0, "avg_length": 0.0, "terse_pct": 0.0, "verbose_pct": 0.0,
    },
    "terminology": {},        # {ngram: count}
    "tone": {
        "messages_seen": 0,
        "emoji_density":   0.0,   # emoji per message (avg)
        "exclaim_density": 0.0,   # ! per message
        "caps_pct":        0.0,   # % of messages with ≥30% CAPS chars
        "question_pct":    0.0,   # % of messages ending in ?
    },
    "preferences":         [], # list[str], LM-distilled
    "preferences_last_ts": 0.0,
    "last_refresh_ts":  0.0,
    "first_seen_ts":    0.0,
    # 1C — closed-loop style adjustments. Daily distillation in
    # agent/core/style_learner.py groups reaction signals (retry, stop,
    # copied, dismissed, continue) by axis and writes hints here. The
    # response_style suffix appends them so JARVIS slowly converges on
    # the user's preferred shape. Capped at 12; decays over 14d.
    "adjustments":      [],   # list[{axis, delta, confidence,
                              #       evidence_count, last_updated, hint}]
}


def _load() -> dict[str, Any]:
    if not _STATE_FILE.exists():
        return dict(_DEFAULT)
    try:
        loaded = json.loads(_STATE_FILE.read_text())
        # Merge so new keys added in future versions get defaults.
        return {**_DEFAULT, **loaded}
    except Exception:
        return dict(_DEFAULT)


def _save(state: dict[str, Any]) -> None:
    """Atomic write — tmpfile + rename. Critic 1C.1-#3: refresh()
    (10-min cadence) and style_learner.distill_daily() both
    load→modify→save. Without atomicity they can clobber each
    other's writes. POSIX rename is atomic on the same filesystem.
    """
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(_STATE_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(_STATE_FILE)
    except Exception:
        pass


# ── Samplers ────────────────────────────────────────────────────────────────

def _sample_vault_tags() -> Counter[str]:
    """Frontmatter `tags: [a, b, c]` across every vault note. Cheap regex
    over the first 30 lines per note — frontmatter only."""
    out: Counter[str] = Counter()
    if not _VAULT.exists():
        return out
    for p in _VAULT.rglob("*.md"):
        try:
            head = "".join(p.read_text(errors="replace").splitlines()[:30])
        except Exception:
            continue
        m = re.search(r"tags?:\s*\[([^\]]+)\]", head)
        if not m:
            # Allow `tag:` shorthand single value too
            m2 = re.search(r"^tags?:\s*(\S+)", head, re.MULTILINE)
            if m2:
                out[m2.group(1).strip().strip('"').strip("'").lower()] += 1
            continue
        for tag in m.group(1).split(","):
            t = tag.strip().strip('"').strip("'").lower()
            if t:
                out[t] += 1
    return out


def _sample_chat_topics() -> tuple[Counter[str], dict]:
    """Read the bus for recent user-initiated chat events. Returns (topic
    counter, comm-style stats). Topics come from the leading nouns of
    each message; cheap heuristic, not LM-driven."""
    topics: Counter[str] = Counter()
    lengths: list[int] = []
    stop = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "had", "her", "was", "one", "our", "out", "this", "that", "with",
        "from", "your", "what", "when", "where", "have", "they", "them",
        "want", "like", "just", "make", "made", "more", "some", "into",
        "than", "then", "well", "good", "okay", "right", "thing", "think",
        "would", "could", "should", "about", "after", "before", "going",
    }
    for evt in bus.recent(1000):
        topic = evt.get("topic", "")
        if topic == "thought.broadcast":
            body = evt.get("thought") or evt.get("body") or ""
        elif topic == "director.goal":
            body = evt.get("goal") or ""
        else:
            continue
        if not isinstance(body, str) or not body:
            continue
        lengths.append(len(body))
        for w in re.findall(r"\b[a-z][a-z0-9_\-]{3,}\b", body.lower()):
            if w not in stop:
                topics[w] += 1
    style = {"messages_seen": len(lengths), "avg_length": 0.0,
             "terse_pct": 0.0, "verbose_pct": 0.0}
    if lengths:
        style["avg_length"] = round(sum(lengths) / len(lengths), 1)
        style["terse_pct"]   = round(100 * sum(1 for x in lengths if x <= _TERSE_MAX)   / len(lengths), 1)
        style["verbose_pct"] = round(100 * sum(1 for x in lengths if x >= _VERBOSE_MIN) / len(lengths), 1)
    return topics, style


def _chat_bodies(limit: int = 1000) -> list[str]:
    """Read recent user-side chat bodies once. Used by both topic and
    terminology samplers so we only walk the bus once per refresh."""
    bodies: list[str] = []
    for evt in bus.recent(limit):
        topic = evt.get("topic", "")
        if topic == "thought.broadcast":
            body = evt.get("thought") or evt.get("body") or ""
        elif topic == "director.goal":
            body = evt.get("goal") or ""
        else:
            continue
        if isinstance(body, str) and body:
            bodies.append(body)
    return bodies


_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF✀-➿]"
)


def _sample_terminology(bodies: list[str], top: int = 60) -> Counter[str]:
    """Bigrams + trigrams from recent chat bodies. Stat-based; nothing
    fancy. Useful when the user repeatedly uses a phrase JARVIS should
    recognize ('offline mode', 'pop os', 'lm studio')."""
    stop = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
        "her", "was", "this", "that", "with", "from", "your", "what", "when",
        "where", "have", "they", "them", "want", "like", "just", "make",
        "would", "could", "should", "about", "after", "before", "going", "i",
        "a", "to", "of", "in", "is", "it", "be", "do", "we", "as", "or",
        "if", "on", "at", "an", "so", "no", "my", "me", "by", "go", "up",
    }
    grams: Counter[str] = Counter()
    for body in bodies:
        words = [w for w in re.findall(r"\b[a-z][a-z0-9\-']+\b", body.lower())]
        words = [w for w in words if w not in stop and len(w) > 1]
        for i in range(len(words) - 1):
            grams[" ".join(words[i:i + 2])] += 1
        for i in range(len(words) - 2):
            grams[" ".join(words[i:i + 3])] += 1
    # Trim to ones that occurred at least twice — singletons are noise.
    trimmed: Counter[str] = Counter({g: n for g, n in grams.items() if n >= 2})
    return Counter(dict(trimmed.most_common(top)))


def _sample_tone(bodies: list[str]) -> dict[str, Any]:
    """Compute simple per-message density stats."""
    if not bodies:
        return {
            "messages_seen": 0,
            "emoji_density": 0.0, "exclaim_density": 0.0,
            "caps_pct": 0.0, "question_pct": 0.0,
        }
    emojis = sum(len(_EMOJI_RE.findall(b)) for b in bodies)
    exclaims = sum(b.count("!") for b in bodies)
    caps_msgs = sum(
        1 for b in bodies
        if b and (sum(1 for c in b if c.isupper()) / max(1, sum(1 for c in b if c.isalpha())))
        >= 0.30
    )
    q_msgs = sum(1 for b in bodies if b.rstrip().endswith("?"))
    n = len(bodies)
    return {
        "messages_seen":   n,
        "emoji_density":   round(emojis / n, 3),
        "exclaim_density": round(exclaims / n, 3),
        "caps_pct":        round(100 * caps_msgs / n, 1),
        "question_pct":    round(100 * q_msgs   / n, 1),
    }


def _distill_preferences(bodies: list[str]) -> list[str] | None:
    """LM-distilled user preferences — one call. Returns up to 5 short
    first-person strings the user has expressed, or None on LM failure
    or empty input. Caller is responsible for rate-limiting via
    `preferences_last_ts`.
    """
    if len(bodies) < 8:
        # Not enough signal yet — keep prior preferences.
        return None
    blob = "\n---\n".join(b[:400] for b in bodies[:40])
    prompt = (
        "You are reading recent chat turns from a single user. Extract up "
        "to 5 SHORT first-person preference statements — things the user "
        "consistently likes, dislikes, expects, or rejects. Be specific "
        "(reference tools, workflows, formats). No generic platitudes. "
        "Return ONLY a JSON array of strings, max 5 items.\n\n"
        f"Chat turns:\n\"\"\"\n{blob[:6000]}\n\"\"\""
    )
    try:
        import asyncio as _aio, json as _json
        from agent.core.lm_studio import get_client
        async def _go():
            r = await get_client().complete(
                [{"role": "user", "content": prompt}],
                max_tokens=300, temperature=0.4,
            )
            return (r.text or "").strip()
        text = _aio.run(_go())
        m = re.search(r"\[.*\]", text, re.DOTALL)
        raw = _json.loads(m.group() if m else text)
        prefs = [str(p).strip() for p in raw if isinstance(p, str) and p.strip()]
        # Sanity: drop anything implausibly long; cap to 5.
        prefs = [p for p in prefs if len(p) <= 200][:5]
        return prefs or None
    except Exception as e:                                       # noqa: BLE001
        logger.debug("preference distillation failed: %s", e)
        return None


def _sample_working_hours(state: dict[str, Any]) -> list[int]:
    """Increment the current local hour bucket. We don't replay all bus
    events to avoid double-counting; we just bump the current bucket and
    let it accumulate organically every refresh."""
    h = datetime.now().hour
    hours = list(state.get("working_hours") or [0] * 24)
    while len(hours) < 24:
        hours.append(0)
    hours[h] += 1
    return hours


def _sample_tool_preferences() -> dict[str, dict]:
    """Per-tool counters from the audit log (best-effort). A tool whose
    cuts/invocations ratio is high probably annoys the user."""
    try:
        from agent.core import audit
    except Exception:
        return {}
    out: dict[str, dict] = {}
    try:
        for row in audit.recent(n=500):
            tool = row.get("tool_name")
            if not tool:
                continue
            rec = out.setdefault(tool, {"invocations": 0, "completions": 0, "cuts": 0})
            rec["invocations"] += 1
            action = row.get("action_type", "")
            if action in ("completed", "approved"):
                rec["completions"] += 1
            elif action in ("denied", "cancelled", "interrupted"):
                rec["cuts"] += 1
    except Exception:
        pass
    return out


# ── Public API ──────────────────────────────────────────────────────────────

def refresh(force: bool = False) -> dict[str, Any]:
    """Re-sample every trait. Cheap enough to call on the maintenance
    cycle (every ~5 min) but rate-limited to 10 min via `last_refresh_ts`
    unless force=True. Returns the new state."""
    state = _load()
    now = time.time()
    if not force and (now - state.get("last_refresh_ts", 0)) < _REFRESH_INTERVAL_S:
        return state

    # Sample
    vault_tags = _sample_vault_tags()
    chat_topics, comm_style = _sample_chat_topics()
    affinity: Counter[str] = Counter()
    affinity.update(vault_tags)
    affinity.update(chat_topics)

    bodies = _chat_bodies()
    state["topic_affinity"] = dict(affinity.most_common(50))
    state["working_hours"] = _sample_working_hours(state)
    state["tool_preferences"] = _sample_tool_preferences()
    state["comm_style"] = comm_style
    state["terminology"] = dict(_sample_terminology(bodies))
    state["tone"] = _sample_tone(bodies)

    # C14.2 — LM-distilled preferences, on its own 6h cadence.
    prefs_age = now - state.get("preferences_last_ts", 0)
    if force or prefs_age >= _PREFS_INTERVAL_S:
        prefs = _distill_preferences(bodies)
        if prefs is not None:
            state["preferences"] = prefs
            state["preferences_last_ts"] = now

    if not state.get("first_seen_ts"):
        state["first_seen_ts"] = now
    state["last_refresh_ts"] = now
    _save(state)
    bus.publish("personality.traits_refreshed", "personality_traits", {
        "tags": len(state["topic_affinity"]),
        "comm_messages": comm_style["messages_seen"],
        "terminology": len(state["terminology"]),
        "preferences": len(state["preferences"]),
    })
    return state


def snapshot() -> dict[str, Any]:
    """Read the current state without re-sampling. Used by the
    /personality/traits endpoint."""
    state = _load()
    affinity = state.get("topic_affinity") or {}
    terms = state.get("terminology") or {}
    # Sort + clip to the top 20 for UI rendering.
    top = sorted(affinity.items(), key=lambda kv: -kv[1])[:20]
    top_terms = sorted(terms.items(), key=lambda kv: -kv[1])[:15]
    return {
        **state,
        "top_topics": [{"topic": k, "count": v} for k, v in top],
        "top_terminology": [{"phrase": k, "count": v} for k, v in top_terms],
    }


def reset_trait(trait: str) -> dict[str, Any]:
    """Reset one trait back to defaults. Powers the Settings 'reset
    trait X' knob — every trait must be resettable per the plan."""
    state = _load()
    if trait in _DEFAULT:
        state[trait] = (_DEFAULT[trait].copy() if hasattr(_DEFAULT[trait], "copy")
                        else _DEFAULT[trait])
        _save(state)
        bus.publish("personality.trait_reset", "personality_traits", {"trait": trait})
    return state
