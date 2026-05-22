"""
Feature Completion Audit

Walks every checkbox in feature_audit.md and reports done/pending counts per
section. feature_audit.md is the single source of truth — plan.json files
were retired 2026-05-10.

Checkbox format expected:
    - [ ] **<id>** <title>
    - [x] **<id>** <title>

Sections detected from `## Part X` and `### <Subhead>` headings.

Usage:
    GET /audit/features                  → full report
    GET /audit/features?phase=A          → just items whose id starts with "A"
    GET /audit/features?phase=A10        → just items whose id starts with "A10"
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

JARVIS_ROOT = Path.home() / "jarvis"
PLAN_PATH = JARVIS_ROOT / "feature_audit.md"

# A checkbox line: "- [ ] **A1.1** title" or "- [x] **C11.4** title"
CHECKBOX_RE = re.compile(r"^- \[(?P<box>[ x])\]\s*(?:\*\*(?P<id>[^*]+)\*\*\s*)?(?P<title>.+?)$")

# A heading: "## Part A" / "### A10 — System hardening" / "## Part D — ..."
PART_RE = re.compile(r"^##\s+(?:Part\s+)?(?P<part>[A-Z][A-Za-z0-9.]*)\b")
SECTION_RE = re.compile(r"^###\s+(?P<sec>[^\n]+)$")


def _parse_master_plan() -> dict[str, Any]:
    if not PLAN_PATH.exists():
        return {"error": f"{PLAN_PATH} not found"}

    items: list[dict[str, Any]] = []
    current_part = "?"
    current_section = "?"

    for line in PLAN_PATH.read_text().splitlines():
        m_part = PART_RE.match(line)
        if m_part:
            current_part = m_part.group("part")
            current_section = "?"
            continue
        m_sec = SECTION_RE.match(line)
        if m_sec:
            current_section = m_sec.group("sec").strip()
            continue
        m_box = CHECKBOX_RE.match(line)
        if m_box:
            items.append({
                "id": (m_box.group("id") or "").strip() or None,
                "title": m_box.group("title").strip(),
                "done": m_box.group("box") == "x",
                "part": current_part,
                "section": current_section,
            })

    return {"items": items}


def audit(phase_filter: str | None = None) -> dict[str, Any]:
    t0 = time.time()
    parsed = _parse_master_plan()
    if "error" in parsed:
        return {"error": parsed["error"], "checked": [str(PLAN_PATH)]}

    items = parsed["items"]
    if phase_filter:
        items = [
            it for it in items
            if (it["id"] or "").startswith(phase_filter)
            or it["part"].startswith(phase_filter)
        ]

    # Group by part for the response
    by_part: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_part.setdefault(it["part"], []).append(it)

    parts_out: list[dict[str, Any]] = []
    grand_done = 0
    grand_total = 0
    for part_name, part_items in by_part.items():
        done = sum(1 for it in part_items if it["done"])
        total = len(part_items)
        grand_done += done
        grand_total += total
        parts_out.append({
            "part": part_name,
            "total": total,
            "done": done,
            "pending": total - done,
            "completion_pct": round(100 * done / total, 1) if total else 0.0,
            "items": [
                {
                    "id": it["id"],
                    "title": it["title"][:200],
                    "done": it["done"],
                    "section": it["section"],
                }
                for it in part_items
            ],
        })

    return {
        "ts": t0,
        "duration_s": round(time.time() - t0, 2),
        "source": str(PLAN_PATH),
        "totals": {
            "done": grand_done,
            "pending": grand_total - grand_done,
            "total": grand_total,
        },
        "completion_pct": round(100 * grand_done / grand_total, 1) if grand_total else 0.0,
        "parts": parts_out,
    }
