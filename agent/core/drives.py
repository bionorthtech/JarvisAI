"""
Drive / Interoception system — JARVIS's internal motivational state.

Three drives accumulate at their own rates and fire threshold notifications
when they cross a level. The background tick runs every 15 minutes.

Drives:
  CURIOSITY    — rises when JARVIS hasn't explored/learned anything recently
  MAINTENANCE  — rises with disk usage / stale memory / long session time
  LEARNING     — rises when many tool calls executed without a knowledge save

(VIGILANCE drive removed 2026-05-15 with the security stack.)

State is persisted at ~/.jarvis/drives.json between restarts.
FastAPI surface: GET /drives, POST /drives/reset/{drive}
"""
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger("jarvis.drives")

_STATE_PATH = Path.home() / ".jarvis" / "drives.json"

# How fast each drive accumulates per tick (tick = 15 min)
_RATES: dict[str, float] = {
    "CURIOSITY":   0.06,   # ~3h to full from zero
    "MAINTENANCE": 0.03,   # ~6h
    "LEARNING":    0.08,   # ~2h
}

# Threshold at which the drive fires a notification
_THRESHOLD = 0.75

# Tick interval in seconds
_TICK_SECONDS = 900  # 15 min


@dataclass
class DriveState:
    curiosity:   float = 0.0
    maintenance: float = 0.0
    learning:    float = 0.0
    last_tick:   float = 0.0   # epoch seconds

    def get(self, name: str) -> float:
        return getattr(self, name.lower(), 0.0)

    def set(self, name: str, value: float) -> None:
        setattr(self, name.lower(), max(0.0, min(1.0, value)))

    def accumulate(self, name: str, rate: float) -> None:
        current = self.get(name)
        self.set(name, current + rate)

    def reset(self, name: str) -> None:
        self.set(name, 0.0)

    def to_dict(self) -> dict:
        return {
            "curiosity":   round(self.curiosity, 4),
            "maintenance": round(self.maintenance, 4),
            "learning":    round(self.learning, 4),
            "last_tick":   self.last_tick,
        }


# Module-level state + notification hooks
_state = DriveState()
_notify_hooks: list[Callable[[str, float], None]] = []


def register_notify(fn: Callable[[str, float], None]) -> None:
    """Register a callback: fn(drive_name, level) fired when drive crosses threshold."""
    _notify_hooks.append(fn)


def _fire_notify(drive: str, level: float) -> None:
    for hook in _notify_hooks:
        try:
            hook(drive, level)
        except Exception as e:
            logger.debug("notify hook error: %s", e)


def load_state() -> None:
    """Load drive state from disk. Silently ignores missing/corrupt file."""
    global _state
    try:
        if _STATE_PATH.exists():
            data = json.loads(_STATE_PATH.read_text())
            _state = DriveState(
                curiosity=float(data.get("curiosity", 0.0)),
                maintenance=float(data.get("maintenance", 0.0)),
                learning=float(data.get("learning", 0.0)),
                last_tick=float(data.get("last_tick", 0.0)),
            )
            logger.debug("drives loaded from %s", _STATE_PATH)
    except Exception as e:
        logger.warning("could not load drive state: %s", e)


def save_state() -> None:
    """Persist current drive state to disk."""
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(_state.to_dict(), indent=2))
    except Exception as e:
        logger.warning("could not save drive state: %s", e)


def get_state() -> dict:
    """Return current drive levels as a dict."""
    return _state.to_dict()


def reset_drive(name: str) -> bool:
    """Reset a single drive to 0. Returns False if name unknown."""
    name = name.upper()
    if name not in _RATES:
        return False
    _state.reset(name)
    save_state()
    logger.info("drive %s reset to 0", name)
    return True


def bump(name: str, amount: float = 0.15) -> None:
    """Manually bump a drive down (e.g., CURIOSITY after a learning event)."""
    name = name.upper()
    if name in _RATES:
        current = _state.get(name)
        _state.set(name, current - amount)
        save_state()


def _tick_once() -> list[str]:
    """Run one accumulation tick. Returns list of drives that crossed threshold."""
    fired = []
    for drive, rate in _RATES.items():
        _state.accumulate(drive, rate)
        level = _state.get(drive)
        if level >= _THRESHOLD:
            fired.append(drive)
            logger.info("drive %s at %.2f — threshold crossed", drive, level)
            _fire_notify(drive, level)
    _state.last_tick = time.time()
    save_state()
    return fired


# tick_loop removed in 4B — drives are driven by the autonomy periodic
# registry (`drives.tick` entry in agent/core/autonomy._build_periodic_registry).
# `_tick_once()` above is the public entry point the registry calls.


def status_summary() -> str:
    """Return a short human-readable status line for injection into prompts."""
    d = _state
    parts = []
    for name in ("curiosity", "maintenance", "learning"):
        val = getattr(d, name)
        bar = "▓" * int(val * 10) + "░" * (10 - int(val * 10))
        flag = " ⚡" if val >= _THRESHOLD else ""
        parts.append(f"{name.upper()[:4]} [{bar}] {val:.0%}{flag}")
    return " | ".join(parts)
