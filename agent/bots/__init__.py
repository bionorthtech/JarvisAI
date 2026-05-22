"""Autonomous agent blueprints."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPORTS_DIR = Path.home() / "jarvis" / "reports"
KEEP_PER_PREFIX = 10


def write_report(prefix: str, report: dict[str, Any]) -> Path | None:
    """Persist a bot run to `~/jarvis/reports/<prefix>_<ISO>.json` and
    retain only the most recent `KEEP_PER_PREFIX` files for this prefix.

    Returns the file path on success, or None on failure (silent — bot
    runs must not fail because the disk is full).
    """
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.fromtimestamp(report.get("ts", time.time())).strftime("%Y-%m-%d_%H%M%S")
        path = REPORTS_DIR / f"{prefix}_{ts}.json"
        path.write_text(json.dumps(report, default=str))
        _rotate(prefix)
        return path
    except Exception:
        return None


def _rotate(prefix: str) -> None:
    """Keep only the newest `KEEP_PER_PREFIX` files matching `<prefix>_*.json`."""
    try:
        files = sorted(
            REPORTS_DIR.glob(f"{prefix}_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[KEEP_PER_PREFIX:]:
            try:
                stale.unlink()
            except Exception:
                pass
    except Exception:
        pass
