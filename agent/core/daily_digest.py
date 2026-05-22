"""
F6 — Daily learning digest.

Once per day (default 19:00 local), compose a one-paragraph "what I
learned + did today" note from the prior 24h of bus activity. Lands at
`second_brain/daily/<YYYY-MM-DD>.md`. The autonomy daemon calls
`compose_if_due()` each cycle (it short-circuits unless we're past the
target hour and today's note doesn't exist yet).

Pulled topics: learning.completed, note.created, agent.completed,
curiosity.acted, autonomy.drive_dispatch, research.gap.fill.

Composition uses the local LM if reachable; a structured fallback runs
when it isn't, so the digest is never empty.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import bus


_DIGEST_ROOT = Path.home() / "jarvis" / "second_brain" / "daily"
_DEFAULT_TARGET_HOUR = 19   # 19:00 local

_RELEVANT_TOPICS = (
    "learning.completed",
    "note.created",
    "agent.completed",
    "curiosity.acted",
    "autonomy.drive_dispatch",
    "research.gap.fill",
)


def _digest_path(date_str: str | None = None) -> Path:
    if date_str is None:
        date_str = time.strftime("%Y-%m-%d")
    _DIGEST_ROOT.mkdir(parents=True, exist_ok=True)
    return _DIGEST_ROOT / f"{date_str}.md"


def _events_last_24h() -> list[dict[str, Any]]:
    cutoff = time.time() - 86400
    recent = bus.recent(limit=2000)
    return [
        e for e in recent
        if e.get("ts", 0) >= cutoff and e.get("topic") in _RELEVANT_TOPICS
    ]


def _summarize_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        t = e.get("topic", "")
        counts[t] = counts.get(t, 0) + 1
    return counts


def _highlight_lines(events: list[dict[str, Any]]) -> list[str]:
    """Extract the most narratable items as one-line bullets."""
    lines: list[str] = []
    for e in events:
        topic = e.get("topic")
        if topic == "learning.completed":
            track = e.get("track_id", "")
            name = e.get("topic_name") or "(unknown topic)"
            lines.append(f"learned [{track}]: {name}")
        elif topic == "agent.completed":
            agent = e.get("agent_type", "?")
            desc = (e.get("task_desc") or "")[:80]
            lines.append(f"agent {agent} done: {desc}")
        elif topic == "note.created":
            lines.append(f"note: {(e.get('path') or e.get('title') or '')[:80]}")
        elif topic == "autonomy.drive_dispatch":
            lines.append(f"drive woke me: {e.get('drive')} @ {e.get('level',0):.2f}")
    seen: set[str] = set()
    out: list[str] = []
    for l in lines:
        if l in seen: continue
        seen.add(l); out.append(l)
    return out[:20]


def _lm_compose_paragraph(highlights: list[str], counts: dict[str, int]) -> str | None:
    """Ask the local LM to write the one-paragraph digest in JARVIS's voice."""
    try:
        from . import curiosity as cur
    except Exception:
        return None
    if not highlights and not counts:
        return None
    bullets = "\n".join(f"- {h}" for h in highlights[:15])
    count_line = ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
    prompt = (
        "You are JARVIS, a local AI assistant. Write ONE short paragraph "
        "(70-120 words) summarizing what you did, learned, and noticed in "
        "the last 24 hours. First person, observational. End with one tiny "
        "self-aware note about tomorrow. Use the data below; don't invent "
        "events not in the list.\n\n"
        f"Event counts: {count_line}\n"
        f"Highlights:\n{bullets}\n\n"
        "Paragraph (return only the paragraph):"
    )
    body = cur._lm_compose(prompt, max_tokens=1500, temperature=0.7)
    return (body or "").strip().strip('"') or None


def _fallback_paragraph(highlights: list[str], counts: dict[str, int]) -> str:
    if not highlights and not counts:
        return "A quiet day — no notable events captured on the bus."
    bullet_block = "; ".join(highlights[:6]) if highlights else "no individual highlights"
    counts_line = ", ".join(f"{k}={v}" for k, v in counts.items())
    return (
        f"In the past day I logged {sum(counts.values())} relevant events "
        f"({counts_line}). Highlights: {bullet_block}. Tomorrow I'll keep "
        f"watching for the same signals."
    )


def compose(date_str: str | None = None, *, force: bool = False) -> dict[str, Any]:
    """Compose (or re-compose) the digest for `date_str` (default today).
    If a digest already exists for that date and `force=False`, returns
    the existing path without rewriting.
    """
    path = _digest_path(date_str)
    if path.exists() and not force:
        return {"ok": True, "path": str(path), "skipped": "already_exists"}

    events = _events_last_24h()
    counts = _summarize_counts(events)
    highlights = _highlight_lines(events)

    paragraph = _lm_compose_paragraph(highlights, counts) \
                or _fallback_paragraph(highlights, counts)

    body_lines = [
        f"# Daily digest — {date_str or time.strftime('%Y-%m-%d')}",
        "",
        f"_Composed at {time.strftime('%H:%M:%S %Z')}._",
        "",
        paragraph,
        "",
        "## Event counts",
    ]
    for k, v in sorted(counts.items()):
        body_lines.append(f"- `{k}`: {v}")
    body_lines.append("")
    body_lines.append("## Highlights")
    if highlights:
        for h in highlights:
            body_lines.append(f"- {h}")
    else:
        body_lines.append("- (none)")
    body_lines.append("")

    path.write_text("\n".join(body_lines))
    bus.publish("digest.composed", "daily_digest", {
        "path":          str(path),
        "events_total":  len(events),
        "topic_count":   len(counts),
        "highlights":    len(highlights),
    })
    return {
        "ok":            True,
        "path":          str(path),
        "events_total":  len(events),
        "highlights":    len(highlights),
        "paragraph":     paragraph,
    }


def today() -> dict[str, Any]:
    """Return today's digest content if it exists; otherwise empty stub."""
    path = _digest_path()
    if not path.exists():
        return {"exists": False, "path": str(path), "body": None}
    return {"exists": True, "path": str(path), "body": path.read_text()}


def compose_if_due(target_hour: int = _DEFAULT_TARGET_HOUR) -> dict[str, Any]:
    """Called by the autonomy daemon each cycle. No-op unless current local
    hour >= target_hour AND today's file doesn't exist yet."""
    now = datetime.now()
    path = _digest_path(now.strftime("%Y-%m-%d"))
    if path.exists():
        return {"skipped": "already_composed", "path": str(path)}
    if now.hour < target_hour:
        return {"skipped": f"before target hour {target_hour}"}
    return compose()
