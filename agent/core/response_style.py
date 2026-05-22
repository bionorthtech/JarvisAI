"""
B6.6 follow-up — ResponseStyle.

Reads the personality_traits vector (sampled on a 10-min cadence by
the autonomy maintenance cycle) and turns it into reply-shaping
parameters the gateway can apply:

  - `system_suffix` — extra lines appended to the system prompt to
    steer terseness / verbosity / code-first ordering.
  - `max_tokens_hint` — soft cap that callers can apply if they
    haven't specified a tighter limit. None = no opinion.
  - `prefer_code_first` — bool. Hints the LM that the user reaches for
    code tools often and so responses with concrete code should lead.

Cheap to call (one personality_traits.snapshot() read; that module
caches its own state). Falls back to a neutral style on any error.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger("jarvis.response_style")


@dataclass
class ResponseStyle:
    system_suffix:     str
    max_tokens_hint:   int | None
    prefer_code_first: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "system_suffix":     self.system_suffix,
            "max_tokens_hint":   self.max_tokens_hint,
            "prefer_code_first": self.prefer_code_first,
        }


# Tunables — picked to be conservative so the style nudge is felt but
# doesn't override an explicit caller-supplied length.
_TERSE_HARD_PCT    = 60.0     # >60% of messages ≤ terse-max → strong terse pref
_TERSE_SOFT_PCT    = 30.0     # >30% → mild terse pref
_VERBOSE_HARD_PCT  = 40.0     # >40% of messages ≥ verbose-min → verbose pref
_CODE_TOOL_HITS    = 5        # invocations of code-leaning tools to flip code-first


# Tools that signal the user thinks in code. Keep this list small and
# real — every entry should map to a registered tool in the running
# instance. Add new ones as plugins ship.
_CODE_LEANING_TOOLS = {
    "run_shell", "read_file", "write_file", "grep_files",
    "git_status", "git_diff", "git_log", "git_commit", "git_branch",
}


def _safe_snapshot() -> dict[str, Any]:
    """One personality_traits read with a hard-empty fallback."""
    try:
        from agent.core import personality_traits as pt
        return pt.snapshot()
    except Exception as e:
        logger.debug("personality_traits snapshot failed: %s", e)
        return {}


def compute() -> ResponseStyle:
    """Build the current ResponseStyle from personality_traits."""
    snap = _safe_snapshot()
    comm = snap.get("comm_style") or {}
    terse_pct   = float(comm.get("terse_pct", 0.0) or 0.0)
    verbose_pct = float(comm.get("verbose_pct", 0.0) or 0.0)

    # Length steering — terse wins ties.
    suffix_lines: list[str] = []
    max_hint: int | None = None
    if terse_pct >= _TERSE_HARD_PCT:
        suffix_lines.append(
            "Reply preference: the user is terse — keep answers under "
            "60 words unless they explicitly ask for detail. No "
            "preamble, no recap of the question."
        )
        max_hint = 600
    elif terse_pct >= _TERSE_SOFT_PCT:
        suffix_lines.append(
            "Reply preference: the user tends terse — favor short, "
            "direct replies and skip the preamble."
        )
        max_hint = 1200
    elif verbose_pct >= _VERBOSE_HARD_PCT:
        suffix_lines.append(
            "Reply preference: the user tends verbose and welcomes "
            "detail; explain reasoning, but stay on topic."
        )
        # No max_hint for verbose preference — let callers run full length.

    # Code-first ordering — based on how often the user invokes coding tools.
    tool_prefs = snap.get("tool_preferences") or {}
    code_invocations = sum(
        int((tool_prefs.get(t) or {}).get("invocations", 0) or 0)
        for t in _CODE_LEANING_TOOLS
    )
    prefer_code_first = code_invocations >= _CODE_TOOL_HITS
    if prefer_code_first:
        suffix_lines.append(
            "Reply preference: code-first. When the answer needs code, "
            "lead with the code block; put context/explanation after, "
            "not before."
        )

    # C14.2 — tone mirroring. Match the user's energy without overdoing
    # it. Single hint when a signal is well above the threshold.
    tone = snap.get("tone") or {}
    if float(tone.get("emoji_density", 0.0) or 0.0) >= 0.5:
        suffix_lines.append(
            "Tone: the user uses emoji freely — match sparingly, never "
            "force them."
        )
    if float(tone.get("exclaim_density", 0.0) or 0.0) >= 1.0:
        suffix_lines.append(
            "Tone: the user is energetic; match the directness without "
            "matching the exclamation count one-for-one."
        )
    if float(tone.get("caps_pct", 0.0) or 0.0) >= 30.0:
        suffix_lines.append(
            "Tone: the user emphasizes via CAPS; use it sparingly in "
            "replies for terms they capitalized."
        )

    # C14.2 — LM-distilled preferences. Inject up to 4 short statements
    # as a "Standing user preferences" section so the LM treats them as
    # persistent context, not chat-turn instructions.
    prefs = snap.get("preferences") or []
    if isinstance(prefs, list) and prefs:
        lines = [f"- {p.strip()[:200]}" for p in prefs[:4]
                 if isinstance(p, str) and p.strip()]
        if lines:
            suffix_lines.append(
                "Standing user preferences (apply to every response unless "
                "the user overrides in this turn):\n" + "\n".join(lines)
            )

    # 1C — closed-loop adjustments. Sorted by confidence × evidence;
    # top 4 appended as additional suffix lines. Anchor invariant: the
    # base suffix above is never replaced, only extended — a bad
    # adjustment can nudge but can't override the user-observed tone
    # and code-first decisions made from raw personality_traits.
    adjustments = snap.get("adjustments") or []
    if isinstance(adjustments, list) and adjustments:
        ranked = sorted(
            (a for a in adjustments if isinstance(a, dict) and a.get("hint")),
            key=lambda a: (float(a.get("confidence", 0) or 0)
                           * max(1, int(a.get("evidence_count", 0) or 0))),
            reverse=True,
        )
        for a in ranked[:4]:
            suffix_lines.append("Learned preference: " + str(a["hint"])[:180])

    suffix = ("\n\n" + "\n".join(suffix_lines)) if suffix_lines else ""
    return ResponseStyle(
        system_suffix=suffix,
        max_tokens_hint=max_hint,
        prefer_code_first=prefer_code_first,
    )
