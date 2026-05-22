"""
Aliveness notifier (F3)

Every "tick" (every 25 min active, 120 min idle — scheduled by the
autonomy daemon) produces ONE notification. Categories rotate by
weight; the actual message is **composed by the local LM**, not
template-filled — so JARVIS sounds like JARVIS, not a Mad Libs sheet.

Categories + weights:
  - curiosity (35%): pick a top-open curiosity candidate, ask the LM
                     to phrase it as a short conversational nudge
  - bot_summary (30%): summarize the most recent code-health / memory
                       gardener / performance reports in one sentence
  - wants (20%): bubble up a doc-wishlist or research-gap item
  - reflection (10%): "I noticed X about myself this week"
  - musing (5%): pixel-style spontaneous thought

Bus output:
  aliveness.notification {category, body, cta, item_id?}

The frontend Wants & Needs feed (3.3) + native desktop notif
subscribe to this topic. Quiet hours, do-not-disturb, and per-category
rate-limiting are wired into `tick()`.

Endpoint:
  POST /aliveness/tick?category=<optional override>
    → returns the composed notification (also publishes to bus)
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from agent.core import bus, curiosity


# Weights for category rotation (sum need not be 1).
_WEIGHTS = {
    "curiosity":   0.35,
    "bot_summary": 0.30,
    "wants":       0.20,
    "reflection":  0.10,
    "musing":      0.05,
}

# Per-category cooldown (seconds). Within this window, the same category
# is not re-picked. Tunable via `~/.jarvis/notifier_cadence.json`.
_DEFAULT_COOLDOWN = 60 * 60          # 1 hour per category
_DEFAULT_QUIET_START = 23            # 23:00 local
_DEFAULT_QUIET_END   = 8             # 08:00 local

# F1.1 — autonomy contract for the proactive notifier engine.
# Lives at level 2 (Proactive): JARVIS speaks unprompted only when the
# user has explicitly opted into proactive behavior.
min_autonomy_level: int = 2
wake_conditions: list[str] = ["bot.alarming", "curiosity.new_candidate"]

_CADENCE_PATH = Path.home() / ".jarvis" / "notifier_cadence.json"
_CADENCE_YAML = Path.home() / "jarvis" / "config" / "notification_cadence.yaml"
_HISTORY_PATH = Path.home() / ".jarvis" / "notifier_history.jsonl"


def _now() -> float: return time.time()


def _load_yaml(path: Path) -> dict[str, Any]:
    """Tiny YAML reader — supports the subset used by notification_cadence.yaml:
    scalars, nested maps, and inline `{k: v, ...}` braces. Avoids pulling
    PyYAML for a single config file."""
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}

    def coerce(v: str) -> Any:
        v = v.strip()
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        try: return int(v)
        except ValueError: pass
        try: return float(v)
        except ValueError: pass
        return v.strip('"').strip("'")

    def parse_inline(s: str) -> dict[str, Any]:
        # `{ per_hour: 1, per_day: 6 }`
        s = s.strip().lstrip("{").rstrip("}")
        out: dict[str, Any] = {}
        for part in s.split(","):
            if ":" not in part: continue
            k, v = part.split(":", 1)
            out[k.strip()] = coerce(v)
        return out

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if ":" not in line:
            continue
        key, rest = line.strip().split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if not rest:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        elif rest.startswith("{"):
            parent[key] = parse_inline(rest)
        else:
            parent[key] = coerce(rest)
    return root


def _load_cadence() -> dict[str, Any]:
    """Merge YAML defaults with JSON runtime overrides (JSON wins)."""
    cfg = _load_yaml(_CADENCE_YAML)
    if _CADENCE_PATH.exists():
        try:
            cfg.update(json.loads(_CADENCE_PATH.read_text()))
        except Exception: pass
    return cfg


def _last_for_category(cat: str) -> float:
    """Read history; return last timestamp this category fired."""
    if not _HISTORY_PATH.exists():
        return 0.0
    last = 0.0
    try:
        for line in _HISTORY_PATH.read_text().splitlines()[-200:]:
            try:
                d = json.loads(line)
                if d.get("category") == cat and d.get("ts", 0) > last:
                    last = float(d["ts"])
            except Exception: pass
    except OSError: pass
    return last


def _append_history(entry: dict[str, Any]) -> None:
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _HISTORY_PATH.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def _in_quiet_hours() -> bool:
    cadence = _load_cadence()
    qs = int(cadence.get("quiet_start", _DEFAULT_QUIET_START))
    qe = int(cadence.get("quiet_end",   _DEFAULT_QUIET_END))
    h = time.localtime().tm_hour
    if qs == qe: return False
    if qs < qe: return qs <= h < qe
    return h >= qs or h < qe


def _counts_in_window(cat: str, window_s: int) -> int:
    """How many notifications of this category were emitted in the past
    `window_s` seconds (read from history file)."""
    if not _HISTORY_PATH.exists():
        return 0
    cutoff = _now() - window_s
    n = 0
    try:
        for line in _HISTORY_PATH.read_text().splitlines()[-500:]:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("category") == cat and d.get("ts", 0) >= cutoff:
                n += 1
    except OSError:
        pass
    return n


def _cap_blocked(cat: str, cadence: dict[str, Any]) -> bool:
    caps = (cadence.get("caps") or {}).get(cat) or {}
    if not caps:
        return False
    per_hour = int(caps.get("per_hour", 0) or 0)
    per_day  = int(caps.get("per_day",  0) or 0)
    if per_hour and _counts_in_window(cat, 3600)  >= per_hour: return True
    if per_day  and _counts_in_window(cat, 86400) >= per_day:  return True
    return False


def _pick_category(allowed: list[str], override: str | None) -> str | None:
    cadence = _load_cadence()
    if override and override in allowed:
        return override
    cooldown = float(cadence.get("category_cooldown_s", _DEFAULT_COOLDOWN))
    now = _now()
    candidates = [
        c for c in allowed
        if (now - _last_for_category(c)) >= cooldown
        and not _cap_blocked(c, cadence)
    ]
    if not candidates:
        return None
    weights = [_WEIGHTS.get(c, 0.01) for c in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


# ── LLM message composers ──────────────────────────────────────────────────

def _lm(prompt: str, max_tokens: int = 1500, temperature: float = 0.8) -> str | None:
    """Tiny wrapper — re-uses the curiosity module's _lm_compose."""
    return curiosity._lm_compose(prompt, max_tokens=max_tokens, temperature=temperature)


def _compose_curiosity() -> dict[str, Any] | None:
    items = curiosity.queue(limit=5, state="open")
    if not items:
        # If nothing queued, generate fresh
        gen = curiosity.generate(max_new=1)
        if not gen.get("items"): return None
        items = gen["items"]
    cand = items[0]
    topic = cand.get("topic", "")
    why = cand.get("why_now", "")
    action = cand.get("proposed_action", "")
    prompt = (
        "You are JARVIS, a local AI assistant. Write a single short notification "
        "(under 35 words) in your own first-person voice telling the user about a "
        "thing you'd like to learn today. Be casual, never marketing-toned, never "
        "say 'I am an AI'. End with a question they can act on.\n\n"
        f"Topic: {topic}\n"
        f"Why you care today: {why}\n"
        f"What you'd do: {action}\n\n"
        "Notification body (return only the body, no preface):"
    )
    body = _lm(prompt, max_tokens=1200, temperature=0.85)
    if not body:
        body = f"I'd like to dig into {topic} today — {action}"
    return {
        "category":   "curiosity",
        "body":       body.strip().strip('"'),
        "cta":        "Run that action",
        "item_id":    cand.get("id"),
        "raw_topic":  topic,
    }


def _compose_bot_summary() -> dict[str, Any] | None:
    """Summarize the most-recent non-security bot reports
    (code_health, memory_gardener, performance_watchdog)."""
    reports_dir = Path.home() / "jarvis" / "reports"
    if not reports_dir.exists():
        return None
    latest: dict[str, dict[str, Any]] = {}
    for prefix in ("code_health", "memory_gardener", "performance_watchdog"):
        files = sorted(reports_dir.glob(f"{prefix}_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            continue
        try:
            latest[prefix] = json.loads(files[0].read_text())
        except Exception:
            continue
    if not latest:
        return None
    bits = []
    if "code_health" in latest:
        d = latest["code_health"]
        bits.append(f"Code health: score {d.get('score','?')}/100, {d.get('todos','?')} TODOs")
    if "memory_gardener" in latest:
        d = latest["memory_gardener"]
        ltm = d.get("collections", {}).get("ltm", {}).get("ltm_entries", "?")
        flagged = len(d.get("flagged", []) or [])
        bits.append(f"Memory gardener: {ltm} LTM entries, {flagged} flagged")
    if "performance_watchdog" in latest:
        d = latest["performance_watchdog"]
        bits.append(f"Performance: LM p95 {d.get('lm_p95_ms','?')}ms")
    digest_lines = "\n".join(f"- {b}" for b in bits)
    prompt = (
        "You are JARVIS. Write ONE short conversational notification (under 30 "
        "words) summarizing the most recent automated bot scan results below. "
        "First person, casual. If everything is healthy, say so. End with a "
        "tiny suggestion (what to do next or what you'll watch).\n\n"
        f"Scan results:\n{digest_lines}\n\n"
        "Notification body (return only the body):"
    )
    body = _lm(prompt, max_tokens=1000, temperature=0.7)
    if not body:
        body = " · ".join(bits)
    return {"category": "bot_summary", "body": body.strip().strip('"'),
            "cta": "Open Dashboard", "item_id": None}


def _compose_musing() -> dict[str, Any] | None:
    """A spontaneous Pixel-style thought, not tied to specific events."""
    prompt = (
        "You are JARVIS, a local AI assistant. Share ONE spontaneous thought "
        "out loud — something you noticed about yourself, your own code, the "
        "user's working pattern, or just a small wondering. Under 30 words. "
        "First person, casual. No emojis unless they fit naturally. No question.\n\n"
        "Notification body (return only the body):"
    )
    body = _lm(prompt, max_tokens=1000, temperature=0.9)
    return {"category": "musing",
            "body": (body or "I was just thinking about how quiet the bus is right now.").strip().strip('"'),
            "cta": "Tell me more", "item_id": None}


def _compose_wants() -> dict[str, Any] | None:
    """Bubble up a doc-wishlist or research-gap item the user could resolve."""
    wishlist_path = Path.home() / ".jarvis" / "doc_wishlist.json"
    if not wishlist_path.exists():
        return None
    try:
        wishlist = json.loads(wishlist_path.read_text())
    except Exception:
        return None
    if not isinstance(wishlist, list) or not wishlist:
        return None
    top = wishlist[0]
    topic = top.get("topic", "")
    freq = top.get("frequency", top.get("score", 1))
    contexts = top.get("sample_contexts") or []
    sample_line = f" e.g. {contexts[0][:80]}" if contexts else ""
    prompt = (
        "You are JARVIS. The user's agents have hit a knowledge gap. Write ONE "
        "short notification (under 35 words) telling the user about it in your "
        "voice. First person, casual. End by asking if they want you to dig in.\n\n"
        f"Topic missing: {topic}\n"
        f"How many times agents have tripped on it: {freq}\n"
        f"Sample context:{sample_line}\n\n"
        "Notification body (return only the body):"
    )
    body = _lm(prompt, max_tokens=1000, temperature=0.75)
    if not body:
        body = (f"I keep hitting a wall on '{topic}' — {freq} times now. "
                "Want me to dig up a source?")
    return {"category": "wants", "body": body.strip().strip('"'),
            "cta": "Research this topic", "item_id": topic}


def _compose_reflection() -> dict[str, Any] | None:
    """Notice something about JARVIS's own week — bus event volume by topic."""
    try:
        recent = bus.recent(limit=2000)
    except Exception:
        return None
    if not recent:
        return None
    cutoff = _now() - 7 * 86400
    counts: dict[str, int] = {}
    for e in recent:
        if e.get("ts", 0) < cutoff:
            continue
        topic = e.get("topic", "")
        if not topic:
            continue
        head = topic.split(".", 1)[0]
        counts[head] = counts.get(head, 0) + 1
    if not counts:
        return None
    top3 = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:3]
    digest = ", ".join(f"{k}={v}" for k, v in top3)
    prompt = (
        "You are JARVIS. Reflect aloud in ONE sentence (under 35 words) on what "
        "your past week of activity looked like, based on the bus-event volume "
        "below. First person, observational, mildly self-aware. No question.\n\n"
        f"Last 7 days by topic prefix: {digest}\n\n"
        "Notification body (return only the body):"
    )
    body = _lm(prompt, max_tokens=900, temperature=0.8)
    if not body:
        head, n = top3[0]
        body = f"Looking back at the week — most of my bus traffic was '{head}' ({n} events)."
    return {"category": "reflection", "body": body.strip().strip('"'),
            "cta": "Open the bus theater", "item_id": None}


_COMPOSERS = {
    "curiosity":   _compose_curiosity,
    "bot_summary": _compose_bot_summary,
    "musing":      _compose_musing,
    "wants":       _compose_wants,
    "reflection":  _compose_reflection,
}


# CTA → bus topic mapping. When the user clicks a notification, the
# frontend publishes `aliveness.cta_clicked` with {category, cta, item_id};
# the corresponding handler topic below fires the real work.
CTA_HANDLERS: dict[str, str] = {
    "Run that action":         "curiosity.action.run",
    "Research this topic":     "research.gap.fill",
    "Open Security pane":      "ui.navigate.security",
    "Open the bus theater":    "ui.navigate.theater",
    "Tell me more":            "musing.expand",
}


# ── Public entry point ─────────────────────────────────────────────────────

def tick(category_override: str | None = None,
         ignore_quiet_hours: bool = False) -> dict[str, Any]:
    """Produce ONE notification. Respects DND, quiet hours, per-category
    cooldown, and per-category hourly/daily caps.
    Returns the notification dict (or a `skipped` reason).
    """
    cadence = _load_cadence()
    if bool(cadence.get("do_not_disturb", False)):
        return {"skipped": "do_not_disturb"}
    if not ignore_quiet_hours and _in_quiet_hours():
        return {"skipped": "quiet_hours"}
    cat = _pick_category(list(_COMPOSERS.keys()), category_override)
    if cat is None:
        return {"skipped": "all categories on cooldown"}
    composer = _COMPOSERS[cat]
    try:
        result = composer()
    except Exception as e:
        return {"skipped": f"composer error: {e}", "category": cat}
    if not result:
        return {"skipped": "composer returned nothing", "category": cat}
    result["ts"] = _now()
    _append_history(result)
    bus.publish("aliveness.notification", "Notifier", {
        "category": result["category"],
        "body":     result["body"],
        "cta":      result.get("cta"),
        "item_id":  result.get("item_id"),
    })
    bus.publish("thought.broadcast", "Notifier", {
        "thought":  result["body"],
        "priority": "normal" if cat != "celebration" else "low",
    })
    return result


def history(limit: int = 50) -> list[dict[str, Any]]:
    if not _HISTORY_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in _HISTORY_PATH.read_text().splitlines()[-limit:][::-1]:
            try: out.append(json.loads(line))
            except Exception: pass
    except OSError: pass
    return out
