"""
D10 — Self-onboarding for new project directories.

When the user switches the active project (TopBar pill / direct API
call), JARVIS checks whether it has seen the directory before. If not,
it returns a small "candidates" list — surface signals about what's
there — so the frontend can offer actionable next steps without
running anything expensive on its own.

The two heavyweight actions (LM directory summary, ChromaDB ingest)
are exposed as separate endpoints the frontend triggers on user
consent. This module never auto-acts on a new project.

State: `~/.jarvis/known_projects.json` — a sorted list of resolved
absolute paths the user has acknowledged. Marking a path "seen" is
explicit (the frontend calls /onboarding/seen when the user dismisses
the offer or accepts an action).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from . import bus

logger = logging.getLogger("jarvis.onboarding")

_STATE_FILE = Path.home() / ".jarvis" / "known_projects.json"

# Files that signal "real project" — used to decide whether the
# candidate is worth surfacing rather than skipping silently.
_PROJECT_MARKERS = (
    ".git", "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "pom.xml", "build.gradle", "Gemfile", "composer.json",
    "CMakeLists.txt", "Makefile", "Dockerfile", "docker-compose.yml",
)
# Readme-shaped files — read up to ~3 KB to seed the convention summary.
_README_NAMES = (
    "README.md", "README.rst", "README.txt", "README",
    "CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md",
)
_README_BYTES = 3000

_MAX_FILE_COUNT_SCAN = 5000   # cap the rglob so a huge dir doesn't hang

# Dirs we never count toward language histograms — they swamp signal.
_SKIP_DIRS = frozenset({
    "node_modules", "dist", "build", "target", "__pycache__",
    "venv", ".venv", "env", ".env", "coverage", "htmlcov",
    ".cache", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".next", ".nuxt", ".turbo", ".parcel-cache", ".gradle",
    "vendor",
})


def _state() -> dict[str, Any]:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text())
    except Exception:
        pass
    return {"known": [], "last_seen_ts": {}}


def _save(state: dict[str, Any]) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.debug("could not save known_projects: %s", e)


def _resolve(path: str) -> Path | None:
    """Expand `~`, resolve to absolute. Returns None if the path doesn't
    exist OR doesn't look like a directory (we don't onboard files)."""
    if not path or not path.strip():
        return None
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        return None
    if not p.is_dir():
        return None
    return p


def is_new(path: str) -> bool:
    """True if `path` resolves to a real dir we haven't seen before."""
    p = _resolve(path)
    if p is None:
        return False
    return str(p) not in set(_state()["known"])


def mark_seen(path: str) -> dict[str, Any]:
    """Record `path` as seen. Idempotent. Returns the updated state."""
    p = _resolve(path)
    if p is None:
        return {"ok": False, "error": f"path not found or not a dir: {path}"}
    state = _state()
    known = set(state["known"])
    known.add(str(p))
    state["known"] = sorted(known)
    state["last_seen_ts"][str(p)] = time.time()
    _save(state)
    bus.publish("onboarding.marked_seen", "onboarding", {"path": str(p)})
    return {"ok": True, "path": str(p), "total_known": len(state["known"])}


def propose(path: str) -> dict[str, Any]:
    """Surface signals for `path`. Cheap — walks at most _MAX_FILE_COUNT_SCAN
    entries, never reads file bodies past the readme. Returns:

        {ok, is_new, path, file_count, markers[], language_hist{},
         readme: {name, preview}?, suggested_actions[]}
    """
    p = _resolve(path)
    if p is None:
        return {"ok": False, "error": f"path not found or not a dir: {path}"}

    new = str(p) not in set(_state()["known"])

    # Marker detection — quick stat() of well-known files.
    markers = [m for m in _PROJECT_MARKERS if (p / m).exists()]

    # File count + language histogram (extension-based, cheap).
    # Walk the dir explicitly so we can prune _SKIP_DIRS — rglob can't
    # short-circuit subtrees the way os.walk can.
    file_count = 0
    skipped_dirs = 0
    by_ext: dict[str, int] = {}
    capped = False
    try:
        import os
        for root, dirs, files in os.walk(p):
            # Prune in-place — applies to subsequent iterations
            keep = []
            for d in dirs:
                if d in _SKIP_DIRS or d.startswith("."):
                    skipped_dirs += 1
                    continue
                keep.append(d)
            dirs[:] = keep
            for f in files:
                file_count += 1
                if file_count > _MAX_FILE_COUNT_SCAN:
                    capped = True
                    break
                if f.startswith("."):
                    continue
                ext = Path(f).suffix.lower().lstrip(".")
                if ext and len(ext) <= 8:
                    by_ext[ext] = by_ext.get(ext, 0) + 1
            if capped:
                break
    except (OSError, PermissionError):
        pass

    top_langs = sorted(by_ext.items(), key=lambda kv: -kv[1])[:8]

    # Readme preview — first one that exists.
    readme: dict[str, Any] | None = None
    for name in _README_NAMES:
        rp = p / name
        if rp.exists() and rp.is_file():
            try:
                txt = rp.read_text(errors="replace")[:_README_BYTES]
                readme = {"name": name, "preview": txt}
                break
            except OSError:
                continue

    # Suggested actions surface to the UI as buttons. Order matters: the
    # frontend lists them top-down.
    actions = []
    if readme:
        actions.append({
            "id": "summarize",
            "label": "Summarize project",
            "detail": f"Use the {readme['name']} + file tree to write a 1-paragraph orientation",
        })
    else:
        actions.append({
            "id": "summarize",
            "label": "Summarize from file tree",
            "detail": "No README — JARVIS will infer from extensions + structure",
        })
    if file_count > 5:
        actions.append({
            "id": "ingest",
            "label": "Ingest into memory",
            "detail": f"Add ≤{min(file_count, _MAX_FILE_COUNT_SCAN)} files to ChromaDB for RAG",
        })
    actions.append({
        "id": "dismiss",
        "label": "Don't ask again",
        "detail": "Mark as seen without acting",
    })

    return {
        "ok": True,
        "is_new": new,
        "path": str(p),
        "file_count": file_count,
        "capped_count": capped,
        "skipped_dirs": skipped_dirs,
        "markers": markers,
        "language_hist": dict(top_langs),
        "readme": readme,
        "suggested_actions": actions,
    }


async def _alm(prompt: str, max_tokens: int = 400) -> str:
    """One LM Studio call. Returns "" on failure (best-effort)."""
    try:
        from agent.core.lm_studio import get_client
        result = await get_client().complete(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens, temperature=0.3,
        )
        return (result.text or "").strip()
    except Exception as e:
        logger.debug("onboarding summary LM call failed: %s", e)
        return ""


def summarize(path: str) -> dict[str, Any]:
    """LM-write a 1-paragraph orientation for the project at `path`.
    Best-effort — returns ok=False with `error` if LM is unreachable."""
    info = propose(path)
    if not info.get("ok"):
        return info

    bits = [f"Project root: {info['path']}"]
    if info.get("markers"):
        bits.append(f"Markers present: {', '.join(info['markers'])}")
    if info.get("language_hist"):
        bits.append("Top extensions: " + ", ".join(
            f"{ext}({n})" for ext, n in info["language_hist"].items()
        ))
    if info.get("readme"):
        bits.append(f"--- {info['readme']['name']} ---\n{info['readme']['preview']}")
    blob = "\n".join(bits)

    prompt = (
        "You are JARVIS reading a project directory for the first time. "
        "Write ONE 60-90 word paragraph orienting yourself: what the "
        "project appears to be, the primary language/framework, and the "
        "two or three subdirectories or files that look most worth "
        "reading first. First person. No filler.\n\n"
        f"Signals:\n{blob[:6000]}"
    )
    text = asyncio.run(_alm(prompt))
    if not text:
        return {"ok": False, "error": "LM unavailable", "info": info}
    bus.publish("onboarding.summarized", "onboarding",
                {"path": info["path"], "len": len(text)})
    return {"ok": True, "path": info["path"], "summary": text, "info": info}
