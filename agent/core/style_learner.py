"""1C — Closed-loop response-style learner.

Listens for cheap user-reaction signals (retry within 30s, stop
button, copied output, dismissed notification) and once a day
distills them into prompt-affecting `adjustments` on the
personality_traits snapshot. `response_style.compute()` reads those
adjustments and appends additional suffix lines so JARVIS sounds
slightly more like the user over time.

Design constraints:
  - The LM model itself never changes; only the system prompt
    prefix/suffix changes.
  - Adjustments decay over 14 days without reinforcement (sub-0.2
    confidence → dropped).
  - Hard cap of 12 active adjustments. Lowest-confidence evicted.
  - Anchor invariant: base response_style suffix is never overridden,
    only extended. A bad adjustment can nudge but can't break.
  - Reset is one endpoint POST away.

Ring buffer for raw signals lives at `~/.jarvis/style_signals.jsonl`
(capped 1000). Adjustments live inside `personality_traits.json` so
the existing transparency surface (`/personality/snapshot`) shows
them.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.style_learner")

_SIGNALS_PATH = Path.home() / ".jarvis" / "style_signals.jsonl"
_SIGNALS_CAP = 1000
_ADJUSTMENTS_CAP = 12
_DECAY_DAYS = 14
_MIN_CONFIDENCE = 0.2

# Signal weights — how much each reaction-type nudges confidence per
# evidence point. Positive nudges support the *current* style snapshot;
# negative nudges erode it. Tuned conservatively — adjustments should
# grow slowly so a single bad day doesn't poison the prompt.
_SIGNAL_WEIGHTS: dict[str, float] = {
    "retry":     -0.15,   # user resent a similar prompt → style was off
    "stop":      -0.20,   # user hit stop mid-stream
    "copied":    +0.20,   # user copied a code block → presentation worked
    "continue":  +0.05,   # user followed up with a short ack
    "dismissed": -0.05,   # user dismissed a proactive notification
}

_VALID_SIGNAL_KINDS = set(_SIGNAL_WEIGHTS.keys())


# ─────────────────────────────────────────────────────────────────────────
# Retry detection (server-side, no UI signal needed)
# ─────────────────────────────────────────────────────────────────────────

_TOKEN_RX = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")


def _tokens(text: str) -> set[str]:
    """Coarse alphanumeric tokenization for Jaccard similarity."""
    return set(t.lower() for t in _TOKEN_RX.findall(text or "")[:200])


def _looks_like_paste(text: str) -> bool:
    """Heuristic: stack traces, logs, large code dumps have lots of
    newlines and/or many non-word chars. Two pastes of the same trace
    would trivially exceed any Jaccard threshold and produce a
    phantom retry signal. Treat anything 'pasted-looking' as not-a-
    retry-candidate (critic 1C.1-#4).
    """
    if len(text) > 1500:
        return True
    nl = text.count("\n")
    if nl >= 6:
        return True
    return False


def is_substantive_retry(prev_prompt: str, prev_ts: float,
                         new_prompt: str, now_ts: float,
                         *, within_s: float = 30.0,
                         min_jaccard: float = 0.6) -> bool:
    """1C — "retry within 30s" detector. Returns True when the new
    prompt is substantively similar to a recent one (Jaccard token
    overlap ≥ min_jaccard) AND landed within the time window. Both
    conditions must hold — rephrasing a question 90s later is not
    a retry, and a quick unrelated follow-up isn't either.

    Pasted content (long, multi-line) is excluded — pasting the same
    stack trace twice should not be read as dissatisfaction with the
    previous response.
    """
    if now_ts - prev_ts > within_s:
        return False
    if _looks_like_paste(prev_prompt) or _looks_like_paste(new_prompt):
        return False
    a, b = _tokens(prev_prompt), _tokens(new_prompt)
    if not a or not b:
        return False
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return False
    return (inter / union) >= min_jaccard


# ─────────────────────────────────────────────────────────────────────────
# Signal ring buffer
# ─────────────────────────────────────────────────────────────────────────

def record_signal(turn_id: str, kind: str, *,
                  style_snapshot: dict[str, Any] | None = None) -> bool:
    """Append a signal to the ring buffer. Idempotent on duplicate
    (turn_id, kind) — only the first instance counts. Returns True on
    write, False on invalid kind or dedup.
    """
    if kind not in _VALID_SIGNAL_KINDS:
        return False
    try:
        # Dedup scan: cheap because we cap the file.
        if _SIGNALS_PATH.exists():
            for line in _SIGNALS_PATH.read_text().splitlines():
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("turn_id") == turn_id and d.get("kind") == kind:
                    return False
        _SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "turn_id": turn_id,
            "ts": time.time(),
            "kind": kind,
            "snapshot": style_snapshot or {},
        }
        with _SIGNALS_PATH.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        _rotate_signals()
        return True
    except Exception as e:
        logger.warning("record_signal failed: %s", e)
        return False


def _rotate_signals() -> None:
    """Trim the ring buffer to _SIGNALS_CAP newest. Cheap — only runs
    after writes."""
    try:
        lines = _SIGNALS_PATH.read_text().splitlines()
    except Exception:
        return
    if len(lines) <= _SIGNALS_CAP + 100:
        return
    _SIGNALS_PATH.write_text("\n".join(lines[-_SIGNALS_CAP:]) + "\n")


def _read_signals(since_ts: float | None = None) -> list[dict[str, Any]]:
    if not _SIGNALS_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in _SIGNALS_PATH.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_ts is not None and d.get("ts", 0) < since_ts:
                continue
            out.append(d)
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────
# Daily distill — collapse signals into personality_traits adjustments
# ─────────────────────────────────────────────────────────────────────────

def _axis_from_snapshot(snap: dict[str, Any]) -> str:
    """Coarse classification: what style axis was active for this
    turn? Today only "terseness" is real (we track terse vs verbose);
    future iterations could split by hour-of-day, code-first, etc."""
    comm = (snap.get("comm_style") or {}) if snap else {}
    terse = float(comm.get("terse_pct", 0.0) or 0.0)
    verbose = float(comm.get("verbose_pct", 0.0) or 0.0)
    if terse >= 0.4:
        return "terseness"
    if verbose >= 0.4:
        return "verbosity"
    return "default"


def distill_daily(now_ts: float | None = None) -> dict[str, Any]:
    """Walk the signal ring buffer and update personality_traits
    `adjustments`. Returns a summary dict for the bus event."""
    now = now_ts if now_ts is not None else time.time()
    horizon = now - 24 * 3600
    signals = _read_signals(since_ts=horizon)

    from . import personality_traits as pt
    state = pt._load()
    adjustments: list[dict[str, Any]] = list(state.get("adjustments", []))

    # Decay first — drop sub-_MIN_CONFIDENCE after 14 days inactivity.
    decay_cutoff = now - _DECAY_DAYS * 86400
    surviving: list[dict[str, Any]] = []
    for a in adjustments:
        last = float(a.get("last_updated", 0) or 0)
        conf = float(a.get("confidence", 0) or 0)
        if last < decay_cutoff:
            # Linearly decay confidence by elapsed days past the cutoff.
            extra_days = (decay_cutoff - last) / 86400
            conf = max(0.0, conf - 0.05 * extra_days)
        if conf >= _MIN_CONFIDENCE:
            a["confidence"] = round(conf, 3)
            surviving.append(a)

    # Group today's signals by axis and apply weights.
    by_axis: dict[str, dict[str, Any]] = {}
    for s in signals:
        axis = _axis_from_snapshot(s.get("snapshot") or {})
        bucket = by_axis.setdefault(axis, {"delta": 0.0, "count": 0})
        bucket["delta"] += _SIGNAL_WEIGHTS.get(s.get("kind", ""), 0.0)
        bucket["count"] += 1

    # Merge per-axis deltas into adjustments. Same axis: bump confidence
    # + evidence_count; new axis: append.
    by_name = {a["axis"]: a for a in surviving}
    for axis, b in by_axis.items():
        if axis == "default" or b["count"] == 0:
            continue
        # Confidence rises with consistent signal sign; flips erode it.
        delta_avg = b["delta"] / b["count"]
        if axis in by_name:
            entry = by_name[axis]
            old_dir = (entry.get("delta", 0) or 0) >= 0
            new_dir = delta_avg >= 0
            if old_dir == new_dir:
                entry["confidence"] = round(
                    min(1.0, (entry.get("confidence", 0.3) or 0.3) + 0.05 * b["count"]),
                    3,
                )
            else:
                entry["confidence"] = round(
                    max(0.0, (entry.get("confidence", 0.3) or 0.3) - 0.1 * b["count"]),
                    3,
                )
            entry["delta"] = round((entry.get("delta", 0) or 0) * 0.7 + delta_avg * 0.3, 3)
            entry["evidence_count"] = int(entry.get("evidence_count", 0) or 0) + b["count"]
            entry["last_updated"] = now
            entry["hint"] = _hint_for(axis, entry["delta"])
        else:
            surviving.append({
                "axis": axis,
                "delta": round(delta_avg, 3),
                "confidence": round(0.3 + 0.05 * b["count"], 3),
                "evidence_count": b["count"],
                "last_updated": now,
                "hint": _hint_for(axis, delta_avg),
            })

    # Enforce cap: keep the top _ADJUSTMENTS_CAP by confidence * evidence.
    surviving.sort(
        key=lambda a: (a.get("confidence", 0) or 0) * max(1, a.get("evidence_count", 0) or 0),
        reverse=True,
    )
    surviving = surviving[:_ADJUSTMENTS_CAP]

    state["adjustments"] = surviving
    pt._save(state)

    return {
        "ok": True,
        "active_count": len(surviving),
        "signals_seen": len(signals),
        "axes_updated": list(by_axis.keys()),
    }


def _hint_for(axis: str, delta: float) -> str:
    """Render a one-line prompt nudge from the (axis, delta) state.
    Hints are conservative — they suggest, never override the base
    response_style suffix."""
    if axis == "terseness":
        if delta < -0.05:
            return "Lean slightly more terse than the base preference suggests."
        if delta > 0.05:
            return "The user has tolerated more verbose replies recently."
    if axis == "verbosity":
        if delta < -0.05:
            return "Tighten responses — the user has been signalling 'too long' lately."
        if delta > 0.05:
            return "Detail is welcome; stay structured but full."
    return ""


def reset_adjustments() -> dict[str, Any]:
    """One-button reset — clears `adjustments` back to empty so a
    drifted prompt suffix can be undone."""
    from . import personality_traits as pt
    state = pt._load()
    state["adjustments"] = []
    pt._save(state)
    return {"ok": True}
