"""
JARVIS Health Score (D1)

One number, 0-100. The arc-reactor pulse. Blends signals we collect
about the assistant's own operating shape:
  - Code Health (TODO debt, large files)
  - Memory Garden freshness
  - Drives balance (curiosity / maintenance / learning equilibrium)

The dashboard shows the composite + a fold-out of what's pulling it
down — so when the number drops, JARVIS can proactively suggest the
specific fix.

Endpoint:
  GET /health-score → {score: 0-100, components: [...], pulled_down_by: [...]}

Bus event:
  health.score (published whenever this is computed by autonomy)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import bus


JARVIS_RT = Path.home() / ".jarvis"


@dataclass
class Component:
    name:    str           # human readable
    weight:  float         # 0..1, contribution share
    value:   float         # 0..100 raw component score
    detail:  str           # one-line explanation
    nudge:   str | None    # suggested action if value is low

    def asdict(self) -> dict[str, Any]:
        return {"name": self.name, "weight": self.weight,
                "value": round(self.value, 1), "detail": self.detail,
                "nudge": self.nudge}


def _read_json(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _component_drives() -> Component:
    """Drives balance — heavy imbalance pulls the score down a little."""
    d = _read_json(JARVIS_RT / "drives.json")
    if not d:
        return Component(name="Drives", weight=0.30, value=80.0,
                         detail="no state yet — neutral",
                         nudge=None)
    # Distance from 0.5 average across the three drives = imbalance.
    values = [float(d.get(k, {}).get("level", 0.5)) for k in
              ("CURIOSITY", "MAINTENANCE", "LEARNING")]
    if not values:
        return Component(name="Drives", weight=0.30, value=80.0,
                         detail="empty drives state", nudge=None)
    avg = sum(values) / len(values)
    max_dev = max(abs(v - 0.5) for v in values)
    # 0 deviation → 100; 0.5 deviation → 0
    score = max(0.0, 100.0 - max_dev * 200.0)
    imbalanced = max_dev > 0.3
    return Component(
        name="Drives", weight=0.30, value=score,
        detail=f"avg={avg:.2f}, max deviation={max_dev:.2f}",
        nudge=("One drive is way out of balance — let it satisfy "
               "(run the matching bot)" if imbalanced else None),
    )


def _component_code_health() -> Component:
    """Pull from the latest code-health report if one exists."""
    reports_dir = Path.home() / "jarvis" / "reports"
    files = sorted(reports_dir.glob("code_health_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return Component(name="Code Health", weight=0.40, value=75.0,
                         detail="no code-health run on record",
                         nudge="Run `POST /bots/code-health/run`")
    d = _read_json(files[0]) or {}
    score = float(d.get("score", 75.0))
    todos = d.get("todos", "?")
    return Component(
        name="Code Health", weight=0.40, value=score,
        detail=f"{int(score)}/100 — {todos} TODOs, age {int((time.time() - files[0].stat().st_mtime)/86400)}d",
        nudge=("Run code-health bot — last result is below 60"
               if score < 60 else None),
    )


def _component_memory() -> Component:
    """Memory gardener freshness — how recently it ran."""
    reports_dir = Path.home() / "jarvis" / "reports"
    files = sorted(reports_dir.glob("memory_gardener_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return Component(name="Memory Garden", weight=0.30, value=70.0,
                         detail="never gardened",
                         nudge="Run `POST /bots/memory-gardener/run`")
    age_days = (time.time() - files[0].stat().st_mtime) / 86400
    # 100 if today, 50 at 7d, 0 at 30d
    score = max(0.0, 100.0 - age_days * (50.0 / 7.0))
    return Component(
        name="Memory Garden", weight=0.30, value=score,
        detail=f"last run {age_days:.1f}d ago",
        nudge=("Memory hasn't been gardened in over a week" if age_days > 7 else None),
    )


def compute() -> dict[str, Any]:
    """Build the full health score."""
    components = [
        _component_code_health(),
        _component_memory(),
        _component_drives(),
    ]
    total_weight = sum(c.weight for c in components)
    score = sum(c.weight * c.value for c in components) / total_weight if total_weight else 0.0

    # Sort pulled-down-by: anything below 70 with a nudge
    pulled_down = [c for c in components if c.value < 70 and c.nudge]
    pulled_down.sort(key=lambda c: c.value)  # worst first

    mood = (
        "operational"     if score >= 85 else
        "watchful"        if score >= 70 else
        "concerned"       if score >= 50 else
        "alarmed"
    )

    out = {
        "ts":            time.time(),
        "score":         round(score, 1),
        "mood":          mood,
        "components":    [c.asdict() for c in components],
        "pulled_down_by": [c.asdict() for c in pulled_down],
    }
    bus.publish("health.score", "HealthScore", {"score": out["score"], "mood": mood})
    return out
