"""
D3 — Morning briefing ritual.

Once per day (default 08:00 local), JARVIS composes a forward-looking
briefing paragraph: overall health score, what's pulling it down, and
the top 2 gaps the user might want to tackle. Written to
`~/jarvis/reports/morning/<YYYY-MM-DD>.md` and published as a bus
event so the dashboard + notifier surface it.

Companion to `daily_digest.py` which writes the evening recap.
Morning brief is forward-looking (what's open, where to start) while
the digest is backward-looking (what happened today).

Lifecycle: `compose_if_due(target_hour=8)` is called once per
maintenance cycle by `autonomy._maintenance_cycle`. It short-circuits
unless we're past the target hour and today's brief doesn't exist yet.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path
from typing import Any

from ..core import bus, health_score


logger = logging.getLogger("jarvis.morning_briefing")

_MORNING_ROOT = Path.home() / "jarvis" / "reports" / "morning"
_DEFAULT_TARGET_HOUR = 8

_BRIEF_PROMPT = (
    "You are JARVIS opening the user's day. Compose a single morning "
    "briefing paragraph (under 90 words). Style: first-person, present "
    "tense, calm and decisive. Cover:\n"
    "  - Health score and the worst pulled-down component\n"
    "  - Top 1-2 actionable suggestions the user could start with today\n"
    "End with one question offering two options. No bullet lists, no "
    "headers, no greetings beyond 'Good morning.'\n\n"
    "Today: {date}\n"
    "Health: {score}/100 ({mood}) — pulled down by: {pulled}\n"
    "Open suggestions:\n{nudges}"
)


def _today_path() -> Path:
    today = dt.date.today().isoformat()
    return _MORNING_ROOT / f"{today}.md"


def _gather_context() -> dict[str, Any]:
    """Pull the 3-4 inputs the briefing prompt needs."""
    try:
        h = health_score.compute()
    except Exception:
        h = {"score": 0, "mood": "unknown", "components": [], "pulled_down_by": []}
    nudges = [c["nudge"] for c in h.get("components", []) if c.get("nudge")]
    return {
        "score": h.get("score", 0),
        "mood": h.get("mood", "unknown"),
        "pulled": ", ".join(h.get("pulled_down_by", [])[:3]) or "nothing",
        "nudges": "\n".join(f"  - {n}" for n in nudges[:4]) or "  - (none)",
    }


async def _narrate(ctx: dict[str, Any]) -> str | None:
    from ..core.lm_studio import get_client
    prompt = _BRIEF_PROMPT.format(
        date=dt.date.today().isoformat(),
        score=ctx["score"], mood=ctx["mood"],
        pulled=ctx["pulled"], nudges=ctx["nudges"],
    )
    try:
        result = await get_client().complete(
            [{"role": "user", "content": prompt}],
            max_tokens=220,
            temperature=0.7,
        )
        return (result.text or "").strip() or None
    except Exception as e:
        logger.debug("morning brief narration failed: %s", e)
        return None


def _write(text: str, ctx: dict[str, Any]) -> Path:
    _MORNING_ROOT.mkdir(parents=True, exist_ok=True)
    path = _today_path()
    body = (
        f"# Morning briefing · {dt.date.today().isoformat()}\n\n"
        f"_Score: {ctx['score']}/100  ·  mood: {ctx['mood']}_\n\n"
        f"{text.rstrip()}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


async def compose(*, force: bool = False) -> dict[str, Any]:
    """Compose today's morning brief. If `force=False` and today's file
    already exists, skip."""
    path = _today_path()
    if path.exists() and not force:
        return {"ok": True, "skipped": True, "path": str(path)}
    ctx = _gather_context()
    text = await _narrate(ctx)
    if not text:
        return {"ok": False, "error": "LM returned no briefing"}
    out_path = _write(text, ctx)
    bus.publish("morning.composed", "morning_briefing", {
        "ts": time.time(),
        "path": str(out_path),
        "score": ctx["score"],
        "preview": text[:280],
    })
    return {"ok": True, "path": str(out_path), "preview": text}


async def compose_if_due(target_hour: int = _DEFAULT_TARGET_HOUR) -> dict[str, Any]:
    """No-op unless we're past `target_hour` and today's file doesn't
    exist yet. Safe to call every maintenance cycle."""
    now = dt.datetime.now()
    path = _today_path()
    if path.exists():
        return {"skipped": "already_composed", "path": str(path)}
    if now.hour < target_hour:
        return {"skipped": f"before target hour {target_hour}"}
    return await compose()


def read_today() -> str:
    """Return today's brief markdown if it exists."""
    path = _today_path()
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
