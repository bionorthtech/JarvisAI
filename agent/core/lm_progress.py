"""
G6 — LM Studio server-log tail → bus events.

LM Studio writes a structured server log per day at
`~/.lmstudio/server-logs/<YYYY-MM>/<YYYY-MM-DD>.<n>.log` with detailed
progress markers that the OpenAI-compatible HTTP response never exposes:

  [2026-05-12 00:16:49][INFO][qwen2.5-coder-7b-instruct] Prompt processing progress: 99.9%
  [2026-05-12 00:16:50][INFO][qwen2.5-coder-7b-instruct] Generation started.
  [2026-05-12 00:16:56][INFO][qwen2.5-coder-7b-instruct] Generation complete in 6.13s.
  slot update_slots: id 3 | task 0 | n_tokens = 6020, ...

This tailer follows the most-recent log file, parses these markers, and
publishes them on the bus as `lm.progress` events so the chat pane (and
theater + dashboard widget) can show *what the model is actually doing*.

Lifecycle: started by main.lifespan() as a fire-and-forget task. Tolerates
log rotation (rolls over to the next day's file when the current one
stops updating).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from . import bus


_LOG_ROOT = Path.home() / ".lmstudio" / "server-logs"

# Regex patterns — kept tight; unparsed lines are ignored.
_RE_PROMPT_PROGRESS = re.compile(
    r"\[(?P<model>[^\]]+)\]\s+Prompt processing progress:\s+([\d.]+)%"
)
_RE_THINK_START  = re.compile(r"\[(?P<model>[^\]]+)\]\s+Start thinking\.\.\.")
_RE_THINK_DONE   = re.compile(
    r"\[(?P<model>[^\]]+)\]\s+Done reasoning\.\s+Reasoned for ([\d.]+) seconds"
)
_RE_SLOT_TOKENS  = re.compile(
    r"slot update_slots:.*?n_tokens\s*=\s*(\d+)"
)
_RE_SLOT_RELEASE = re.compile(
    r"slot\s+release:.*?n_tokens\s*=\s*(\d+),?\s*truncated\s*=\s*(\d+)"
)

logger = logging.getLogger("jarvis.lm_progress")

# Throttle prompt-progress events: only publish every N percentage points.
_PROGRESS_PUBLISH_STEP = 5.0


def _latest_log_path() -> Path | None:
    """Find the most-recent .log file under ~/.lmstudio/server-logs/."""
    if not _LOG_ROOT.exists():
        return None
    try:
        candidates = list(_LOG_ROOT.rglob("*.log"))
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _parse_line(line: str) -> dict[str, Any] | None:
    """Return a {phase, ...} event dict, or None if line doesn't match."""
    m = _RE_PROMPT_PROGRESS.search(line)
    if m:
        return {
            "phase":   "prompt_processing",
            "model":   m.group("model"),
            "percent": float(m.group(2)),
        }
    m = _RE_THINK_START.search(line)
    if m:
        return {"phase": "thinking_start", "model": m.group("model")}
    m = _RE_THINK_DONE.search(line)
    if m:
        return {
            "phase":   "thinking_done",
            "model":   m.group("model"),
            "seconds": float(m.group(2)),
        }
    m = _RE_SLOT_RELEASE.search(line)
    if m:
        return {
            "phase":     "request_complete",
            "n_tokens":  int(m.group(1)),
            "truncated": bool(int(m.group(2))),
        }
    return None


class _State:
    """Per-request running state so we can throttle the 5-percent ladder."""
    def __init__(self) -> None:
        self.last_pct_announced: float = -100.0

_state = _State()


def _publish(event: dict[str, Any]) -> None:
    phase = event.get("phase")
    if phase == "prompt_processing":
        pct = float(event["percent"])
        # Throttle: emit at start (0%), every 5%, and at 100%.
        if pct < 100.0 and (pct - _state.last_pct_announced) < _PROGRESS_PUBLISH_STEP:
            return
        _state.last_pct_announced = pct
        if pct >= 99.99:
            _state.last_pct_announced = -100.0  # reset for next request
    elif phase == "thinking_start":
        _state.last_pct_announced = -100.0
    elif phase == "request_complete":
        _state.last_pct_announced = -100.0
    event["ts"] = time.time()
    bus.publish("lm.progress", "lm_progress", event)


async def _follow_file(path: Path) -> None:
    """Tail one log file. Returns when the file appears to be retired
    (i.e. a different file is now the newest)."""
    try:
        f = path.open("r", errors="ignore")
    except OSError as e:
        logger.debug("could not open %s: %s", path, e)
        return
    try:
        f.seek(0, 2)   # start at EOF — only watch new lines
        idle_ticks = 0
        while True:
            line = f.readline()
            if not line:
                idle_ticks += 1
                # Every ~10s of idle, check if a newer log file exists.
                if idle_ticks % 20 == 0:
                    latest = _latest_log_path()
                    if latest and latest != path:
                        return  # rotate
                await asyncio.sleep(0.5)
                continue
            idle_ticks = 0
            event = _parse_line(line)
            if event:
                _publish(event)
    finally:
        f.close()


async def tail_loop() -> None:
    """Top-level loop: pick the latest log, follow it, rotate when retired."""
    bus.publish("lm.progress.tail_started", "lm_progress",
                {"root": str(_LOG_ROOT)})
    while True:
        path = _latest_log_path()
        if path is None:
            # LM Studio may not be installed / running — back off and retry.
            await asyncio.sleep(30)
            continue
        try:
            await _follow_file(path)
        except Exception as e:
            logger.debug("tail loop error on %s: %s", path, e)
            await asyncio.sleep(5)
