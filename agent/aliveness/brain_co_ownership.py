"""Brain co-ownership scanner.

JARVIS doesn't just respond to brain queries — it watches the vault and
the conversation for material that *should* be captured, linked, or
followed up on, and surfaces suggestions on the bus.

This module is read-only over the vault and bus. It never writes a note
without a user action (the suggestions ship with one-click CTAs the
frontend wires up).

Detectors:
- recurring-theme    — a phrase seen ≥3 times in chat this week that
                       isn't yet a brain note → "capture it?"
- orphan-note        — a note in the vault with zero backlinks AND no
                       wikilinks out → suggest 1-click "find related"
- missing-backlinks  — a note that mentions [[X]] but X has no incoming
                       link to it from this note's stem → suggest add
- untagged-note      — a substantial note (≥80 chars body) with no `tags`
                       frontmatter or empty list → "suggest tags?"
- stale-daily        — today's daily note untouched past 14:00 local →
                       suggest a quick recap

Each detector returns a list of `{kind, body, cta_topic, payload}`. The
maintenance cycle calls `scan()` and publishes whatever comes back.
"""
from __future__ import annotations

import re
import time
from collections import Counter
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

from agent.core import bus

_VAULT = Path.home() / ".jarvis" / "brain"
_MIN_THEME_HITS = 3
_THEME_WINDOW_HOURS = 7 * 24
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
# Words we don't treat as themes — stopwords + common chat fillers.
_STOP = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "day", "get", "has", "him", "his",
    "how", "now", "see", "two", "way", "who", "i'm", "i've", "i'll", "don't",
    "won't", "this", "that", "with", "from", "your", "what", "when", "where",
    "have", "they", "them", "want", "like", "just", "make", "made", "more",
    "some", "into", "than", "then", "well", "good", "okay", "right", "thing",
    "think", "would", "could", "should", "about", "after", "before", "going",
    "needs", "need", "really", "thanks", "thank", "yeah", "sure", "stuff",
}


def _recent_chat_text(hours: int = _THEME_WINDOW_HOURS) -> list[str]:
    """Pull chat-side bus events over the last N hours so we can scan
    their bodies for recurring vocabulary. Cheap — bus.recent is indexed."""
    cutoff = time.time() - hours * 3600
    out: list[str] = []
    for evt in bus.recent(500):
        if evt.get("ts", 0) < cutoff:
            continue
        topic = evt.get("topic", "")
        if not (topic.startswith("thought.") or topic.startswith("agent.")
                or topic == "director.result"):
            continue
        for key in ("thought", "result", "summary", "task_desc"):
            v = evt.get(key)
            if isinstance(v, str):
                out.append(v)
    return out


def _vault_notes() -> list[Path]:
    if not _VAULT.exists():
        return []
    return list(_VAULT.rglob("*.md"))


def _note_stems() -> set[str]:
    return {p.stem.lower() for p in _vault_notes()}


# ── Detectors ────────────────────────────────────────────────────────────────

def detect_recurring_themes() -> list[dict[str, Any]]:
    """Words appearing 3+ times across chat this week that aren't already
    a brain note. Single suggestion with the top miss."""
    text = " ".join(_recent_chat_text()).lower()
    words = re.findall(r"\b[a-z][a-z0-9_\-]{4,}\b", text)
    counts = Counter(w for w in words if w not in _STOP)
    stems = _note_stems()
    miss = [(w, n) for w, n in counts.most_common(20)
            if n >= _MIN_THEME_HITS and w not in stems]
    if not miss:
        return []
    word, n = miss[0]
    return [{
        "kind": "recurring_theme",
        "body": f"\"{word}\" has come up {n} times this week — want me to start a brain note for it?",
        "cta_topic": "brain.capture_suggestion",
        "payload": {"title": word, "occurrences": n},
    }]


def detect_orphan_notes() -> list[dict[str, Any]]:
    """Notes with zero incoming wikilinks and zero outgoing — the
    classic 'I wrote it once and forgot' case. One suggestion with the
    oldest orphan."""
    notes = _vault_notes()
    if not notes:
        return []
    text_cache: dict[Path, str] = {}
    for p in notes:
        try:
            text_cache[p] = p.read_text(errors="replace")
        except Exception:
            text_cache[p] = ""

    # Build {stem: set of stems linking to it}
    incoming: dict[str, set[str]] = {p.stem.lower(): set() for p in notes}
    for p, t in text_cache.items():
        for m in _WIKILINK_RE.finditer(t):
            target = m.group(1).split("/")[-1].lower()
            if target in incoming:
                incoming[target].add(p.stem.lower())

    orphans = []
    for p, t in text_cache.items():
        if incoming.get(p.stem.lower()):
            continue
        if _WIKILINK_RE.search(t):
            continue
        orphans.append((p.stat().st_mtime, p))

    if not orphans:
        return []
    orphans.sort()  # oldest first
    _, p = orphans[0]
    rel = p.relative_to(_VAULT)
    return [{
        "kind": "orphan_note",
        "body": f"'{rel}' has no links in or out. Want me to suggest connections?",
        "cta_topic": "brain.suggest_links",
        "payload": {"name": str(rel).replace(".md", "")},
    }]


# `\s` matches `\n`, which would let `tags:\ntitle: foo` slurp the next
# line as the tags value. Match only horizontal whitespace before the
# capture so the line-anchored `$` actually ends the value.
_TAGS_LINE_RE = re.compile(r"^tags[ \t]*:[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE)


def _has_real_tags(text: str) -> bool:
    """True if the note's YAML frontmatter declares at least one tag.

    Recognized shapes (inside the leading `---\\n…\\n---` block):
        tags: [a, b, c]
        tags:
          - a
          - b
        tags: a, b, c
    Returns False if frontmatter is absent, missing the tags key, or the
    declared list is empty/whitespace only.
    """
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 4)
    if end < 0:
        return False
    fm = text[4:end]
    m = _TAGS_LINE_RE.search(fm)
    if not m:
        return False
    inline = m.group(1).strip()
    # Inline list `tags: [a, b]` or `tags: a, b`
    if inline:
        stripped = inline.strip("[] ")
        return bool([t for t in stripped.split(",") if t.strip()])
    # Block list — look for `- something` lines after the tags: header
    tail = fm[m.end():]
    for line in tail.splitlines():
        if line.strip().startswith("-") and line.strip().strip("- ").strip():
            return True
        # First non-list line breaks out of the tags block
        if line.strip() and not line.lstrip().startswith("-"):
            break
    return False


def detect_untagged_notes() -> list[dict[str, Any]]:
    """Pick the most-recently-modified note without tags. Limits to one
    suggestion per scan and skips short notes (likely incomplete drafts)."""
    notes = _vault_notes()
    if not notes:
        return []
    candidates: list[tuple[float, Path]] = []
    for p in notes:
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        # Skip tiny drafts and daily notes (Daily/ folder has its own habits).
        if len(text.strip()) < 80:
            continue
        if "/Daily/" in str(p) or p.parent.name == "Daily":
            continue
        if _has_real_tags(text):
            continue
        candidates.append((p.stat().st_mtime, p))
    if not candidates:
        return []
    candidates.sort(reverse=True)   # most-recent first
    _, p = candidates[0]
    rel = p.relative_to(_VAULT)
    name = str(rel).replace(".md", "")
    return [{
        "kind": "untagged_note",
        "body": f"'{rel}' has no tags. Want me to suggest a few?",
        "cta_topic": "brain.suggest_tags",
        "payload": {"name": name},
    }]


def detect_stale_daily() -> list[dict[str, Any]]:
    """Today's daily note untouched past 14:00 local — gentle nudge."""
    daily_dir = _VAULT / "Daily"
    if not daily_dir.exists():
        return []
    today_path = daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    if not today_path.exists():
        return []
    now = datetime.now().time()
    if now < dtime(14, 0):
        return []
    age_h = (time.time() - today_path.stat().st_mtime) / 3600
    if age_h < 4:
        return []
    return [{
        "kind": "stale_daily",
        "body": "Today's daily note has been quiet since this morning — drop a quick recap?",
        "cta_topic": "brain.today_append",
        "payload": {"path": str(today_path)},
    }]


# ── Public entry point ───────────────────────────────────────────────────────

def scan() -> list[dict[str, Any]]:
    """Run every detector and return the combined suggestion list.

    Best-effort: any individual detector that raises is skipped so a
    broken detector can't take out the whole co-ownership pass."""
    out: list[dict[str, Any]] = []
    for fn in (detect_recurring_themes, detect_orphan_notes,
               detect_untagged_notes, detect_stale_daily):
        try:
            out.extend(fn())
        except Exception as e:                                  # noqa: BLE001
            bus.publish("autonomy.brain_co_error", "brain_co", {
                "detector": fn.__name__, "error": str(e)[:200],
            })
    return out


def scan_and_publish() -> int:
    """Maintenance-cycle hook: run scan() and publish each suggestion as
    an `aliveness.notification` event the frontend already subscribes to.
    Returns the count of suggestions published."""
    sugs = scan()
    for s in sugs:
        bus.publish("aliveness.notification", "brain_co", {
            "category": "brain_co_ownership",
            "body":     s["body"],
            "cta":      s["cta_topic"],
            "kind":     s["kind"],
            "payload":  s["payload"],
            "ts":       time.time(),
        })
    return len(sugs)
