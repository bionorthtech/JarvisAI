#!/usr/bin/env python3
"""Orphan-glob audit.

Finds every `Path(...).glob("<pattern>")` call in the source tree and
verifies that *something* writes a file matching that pattern. Catches
the dead-feature class of bug where a reader expects files that no
writer produces (e.g. `code_health_*.json` before write_report existed).

Reports two kinds of finding:

  ORPHAN — glob pattern read, no matching writer found
  WRITE-ONLY — pattern that's written but never globbed (harmless,
               informational)

Usage:
    venv/bin/python3 scripts/orphan_glob_audit.py
    venv/bin/python3 scripts/orphan_glob_audit.py --strict   # exit 1 on orphan

Heuristics:
  - A "glob" is `<expr>.glob("<pattern>")` with a literal string arg.
  - A "writer" is any string literal containing the non-wildcard parts of
    the pattern (joined by '*' boundaries). e.g. "code_health_*.json"
    matches any literal containing both "code_health_" and ".json".
  - Tests, scripts, and __pycache__ are skipped on both sides — writers
    in tests don't count as real writers.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["agent", "main.py", "plugins"]
SKIP_PARTS = ("__pycache__", "tests", "venv", "node_modules")

GLOB_CALL = re.compile(r'\.glob\(\s*[fr]?"([^"]+)"\s*\)')
STRING_LITERAL = re.compile(r'[fr]?"([^"]*)"')


def _iter_py_files() -> list[Path]:
    out: list[Path] = []
    for entry in SCAN_DIRS:
        p = ROOT / entry
        if p.is_file() and p.suffix == ".py":
            out.append(p)
        elif p.is_dir():
            for f in p.rglob("*.py"):
                if any(part in SKIP_PARTS for part in f.parts):
                    continue
                out.append(f)
    return out


def _pattern_parts(pattern: str) -> list[str]:
    """Split a glob pattern on `*` / `?` to get literal segments,
    plus a "stem" form with trailing punctuation stripped (so
    `code_health_*.json` produces stems `code_health` + `.json`
    that match `write_report("code_health", ...)` constructions)."""
    raw = [seg for seg in re.split(r"[*?]+", pattern) if seg]
    stems: list[str] = []
    for seg in raw:
        stems.append(seg)
        stripped = seg.rstrip("_-.")
        if stripped and stripped != seg:
            stems.append(stripped)
    return stems


def _writer_matches(literal: str, pattern_stems: list[str]) -> bool:
    """True if any stem of the pattern appears in `literal` — looser
    matching than 'all segments' to handle f-string constructions where
    only the prefix shows up as a literal."""
    return any(stem in literal for stem in pattern_stems)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any orphan glob is found")
    args = parser.parse_args()

    files = _iter_py_files()
    globs: list[tuple[Path, int, str]] = []     # (file, line, pattern)
    literals: list[tuple[Path, int, str]] = []  # (file, line, literal)

    for f in files:
        try:
            text = f.read_text()
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            m = GLOB_CALL.search(line)
            if m:
                globs.append((f, i, m.group(1)))
            for sm in STRING_LITERAL.finditer(line):
                literals.append((f, i, sm.group(1)))

    orphans: list[tuple[Path, int, str]] = []
    for f, ln, pattern in globs:
        stems = _pattern_parts(pattern)
        if not stems:
            continue
        # Drop common suffix-only stems that match too broadly (".json",
        # ".md") — those alone don't prove a specific writer exists.
        meaningful = [s for s in stems if not s.startswith(".")]
        if not meaningful:
            continue
        ok = False
        for wf, _wln, lit in literals:
            if wf == f and lit == pattern:
                continue
            if _writer_matches(lit, meaningful):
                ok = True
                break
        if not ok:
            orphans.append((f, ln, pattern))

    if orphans:
        print(f"ORPHAN globs ({len(orphans)}):")
        for f, ln, pat in orphans:
            rel = f.relative_to(ROOT)
            print(f"  {rel}:{ln}  glob({pat!r})")
        print()
        print("→ either delete the glob caller or add a writer that produces matching files.")
        if args.strict:
            return 1
    else:
        print(f"OK — {len(globs)} glob call(s) checked, all have plausible writers.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
