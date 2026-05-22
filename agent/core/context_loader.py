"""
Per-project context loader.
Reads .jarvis/context.md from the project workspace and injects it into
the gateway as Layer 3 context (below skills index, above per-run overrides).

Auto-creates a template context.md on first use for a new project.
"""
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.context_loader")

_TEMPLATE = """# JARVIS Project Context

## What this project is
<!-- Describe the project in 1-3 sentences -->

## Key files
<!-- List the most important files and what they do -->

## Naming conventions
<!-- Any naming patterns JARVIS should follow -->

## Current focus
<!-- What are you working on right now -->

## Notes for JARVIS
<!-- Anything else JARVIS should know: gotchas, patterns to avoid, etc. -->
"""

# How long to cache a context file before re-reading (seconds)
_CACHE_TTL = 30

_cache: dict[str, tuple[float, str]] = {}  # project → (timestamp, content)


def _find_context_file(project_path: str) -> Optional[Path]:
    """Search for .jarvis/context.md starting from project_path, walking up."""
    p = Path(project_path).expanduser().resolve()
    for candidate in [p, *p.parents]:
        ctx = candidate / ".jarvis" / "context.md"
        if ctx.exists():
            return ctx
        if (candidate / ".git").exists():
            # Stop at git root even if no context.md found
            break
    return None


def load(project: str = "default") -> str:
    """
    Return the context block for a project, or empty string if none found.
    project can be a name ("default") or a filesystem path.
    """
    now = time.monotonic()
    cached_ts, cached_content = _cache.get(project, (0.0, ""))
    if now - cached_ts < _CACHE_TTL:
        return cached_content

    content = ""

    # If project looks like a path, try to find context.md there
    p = Path(project).expanduser()
    if p.exists() and p.is_dir():
        ctx_file = _find_context_file(str(p))
        if ctx_file:
            try:
                raw = ctx_file.read_text(encoding="utf-8", errors="replace")
                content = f"[PROJECT CONTEXT: {ctx_file}]\n{raw}\n[END PROJECT CONTEXT]"
                logger.debug("loaded context from %s", ctx_file)
            except OSError as e:
                logger.warning("could not read context.md: %s", e)

    _cache[project] = (now, content)
    return content


def ensure_template(project_path: str) -> str:
    """
    Create a .jarvis/context.md template if one doesn't exist.
    Returns the path to the file.
    """
    p = Path(project_path).expanduser().resolve()
    jarvis_dir = p / ".jarvis"
    ctx_file = jarvis_dir / "context.md"

    if not ctx_file.exists():
        jarvis_dir.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text(_TEMPLATE, encoding="utf-8")
        logger.info("created context template at %s", ctx_file)

    return str(ctx_file)


def invalidate(project: str) -> None:
    """Force a re-read on next load() call."""
    _cache.pop(project, None)
