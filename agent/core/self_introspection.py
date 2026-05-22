"""
Self-introspection

JARVIS can read its own source tree on request — "what does the
memory gardener do?" → returns the actual source of
agent/bots/memory_gardener.py with a hand-curated module map keyed by
feature item ID, plus an LM-ready prompt-frame so the model
summarizes it in plain English.

Plus weekly auto-introspection (driven by autonomy daemon): pick the
most-recently-modified jarvis source file, emit a "I learned something
about myself this week" beat to the bus + Wants feed.

Endpoints:
  GET  /self/module?id=C6.1        → source + map entry
  GET  /self/recent-changes?days=7 → files touched recently
  POST /self/explain               → {item_id} → LLM summary
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JARVIS_ROOT = Path.home() / "jarvis"


# Hand-curated map: feature item id → the primary file(s) implementing it.
ITEM_TO_FILES: dict[str, list[str]] = {
    "D2.1": ["agent/core/narrator.py", "main.py"],
    "D2.2": ["jarvis-ui/src/App.tsx"],
    "B6.9": ["agent/core/self_introspection.py"],
    "D1.1": ["agent/core/health_score.py"],
    "F2.1": ["agent/core/curiosity.py"],
    "F2.2": ["agent/core/curiosity.py"],
    "F2.3": ["agent/core/curiosity.py"],
    "F2.4": ["agent/core/curiosity.py", "main.py"],
}


def _abs(path_str: str) -> Path:
    """Resolve a path string that may start with ~/."""
    if path_str.startswith("~"):
        return Path(path_str).expanduser()
    return JARVIS_ROOT / path_str


def module_for_item(item_id: str, max_lines: int = 400) -> dict[str, Any]:
    """Return source paths + (truncated) source for a feature item.

    If the file is huge, only the first `max_lines` lines are returned along
    with a `truncated_to` field — callers can request a larger slice.
    """
    files = ITEM_TO_FILES.get(item_id)
    if not files:
        return {"item_id": item_id, "found": False,
                "error": f"no ITEM_TO_FILES entry for {item_id}",
                "available_items": sorted(ITEM_TO_FILES.keys())}
    out_files: list[dict[str, Any]] = []
    for f in files:
        p = _abs(f)
        if not p.exists():
            out_files.append({"path": str(p), "found": False})
            continue
        try:
            text = p.read_text(errors="replace")
            lines = text.splitlines()
            truncated = len(lines) > max_lines
            shown = "\n".join(lines[:max_lines])
            out_files.append({
                "path":          str(p),
                "found":         True,
                "lines":         len(lines),
                "bytes":         p.stat().st_size,
                "source":        shown,
                "truncated_to":  max_lines if truncated else None,
            })
        except (OSError, UnicodeError) as e:
            out_files.append({"path": str(p), "found": False, "error": str(e)})
    return {"item_id": item_id, "found": True, "files": out_files}


@dataclass
class RecentChange:
    path:        str
    last_ts:     float
    last_commit: str
    subject:     str

    def asdict(self) -> dict[str, Any]:
        return {"path": self.path, "last_ts": self.last_ts,
                "last_commit": self.last_commit, "subject": self.subject}


def recent_changes(days: int = 7, max_files: int = 25) -> dict[str, Any]:
    """Return source files modified in the last `days` days, newest-first."""
    since = f"{days}.days.ago"
    try:
        r = subprocess.run(
            ["git", "-C", str(JARVIS_ROOT),
             "log", f"--since={since}",
             "--name-only", "--pretty=format:%H%x09%ct%x09%s"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"ok": False, "error": "git unavailable"}

    seen: dict[str, RecentChange] = {}
    current: tuple[str, float, str] | None = None
    for line in r.stdout.splitlines():
        if "\t" in line:
            parts = line.split("\t", 2)
            if len(parts) == 3:
                current = (parts[0][:7], float(parts[1]), parts[2])
            continue
        if not line.strip() or current is None:
            continue
        path = line.strip()
        if path not in seen:
            seen[path] = RecentChange(
                path=path, last_ts=current[1],
                last_commit=current[0], subject=current[2],
            )
    changes = sorted(seen.values(), key=lambda c: -c.last_ts)[:max_files]
    return {
        "ok":           True,
        "since_days":   days,
        "file_count":   len(changes),
        "files":        [c.asdict() for c in changes],
    }


def explain_prompt(item_id: str) -> dict[str, Any]:
    """Build an LM-ready prompt for explaining what an item does.

    Returns the prompt + the source it's grounded on; caller (gateway or
    endpoint) feeds it to LM Studio.
    """
    m = module_for_item(item_id, max_lines=300)
    if not m.get("found"):
        return {"item_id": item_id, "error": m.get("error", "not found"),
                "available_items": m.get("available_items", [])}
    source_blob = "\n\n".join(
        f"# === {f['path']} ===\n{f.get('source', '(unreadable)')}"
        for f in m["files"] if f.get("found")
    )
    prompt = (
        f"You are JARVIS reading your own source code to explain feature item {item_id}.\n"
        f"Below is the actual code that implements this item.\n"
        f"Write a single concise paragraph (3-5 sentences) covering: what it does, "
        f"how it's wired in, and the most useful thing for the user to know about it. "
        f"Plain language. No marketing tone. No bullet points.\n\n"
        f"--- source ---\n{source_blob}"
    )
    return {"item_id": item_id, "prompt": prompt,
            "source_files": [f["path"] for f in m["files"]],
            "source_bytes": sum(f.get("bytes", 0) for f in m["files"] if f.get("found"))}
