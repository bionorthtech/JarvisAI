"""1A — Agentic reflection layer.

After gateway.ask_events() or swarm.SubAgent.run() finishes a turn,
we run one short LM call that scores "did I actually solve it?" and
extracts a single-sentence lesson. The reflection lands in a JSONL
store, fires a `reflection.recorded` bus event, and feeds two
existing loops that today are open-ended:

  - skill_distiller (gate distillation on score >= 0.7)
  - curiosity (populate c.outcome when the turn was tagged
    [CURIOSITY:<id>] in the prompt header)

Reflection is observation, not retry. We do not loop back into the
gateway. It runs as a fire-and-forget task after final_text has
already been yielded to the user, so it never adds to user-visible
latency. Gated behind `JARVIS_REFLECTION_ENABLED` (env, default on).

Schema is tight on purpose — qwen2.5-coder-7b returns ~60 tokens for
the 3-line format reliably; gemma-3-thinking returns the same on
"none" reasoning effort. Bad parses are silently dropped.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import bus

_STORE_PATH = Path.home() / ".jarvis" / "reflections.jsonl"
_MAX_ENTRIES = 2000

_SCORE_RX = re.compile(r"SCORE\s*:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_VERDICT_RX = re.compile(
    r"VERDICT\s*:\s*(solved|partial|off-target|tool-blocked)",
    re.IGNORECASE,
)
_LESSON_RX = re.compile(r"LESSON\s*:\s*(.+)", re.IGNORECASE)

_VALID_VERDICTS = {"solved", "partial", "off-target", "tool-blocked"}


_CURIOSITY_TAG_RX = re.compile(r"\[CURIOSITY:([a-zA-Z0-9_\-]+)\]")


@dataclass
class Reflection:
    id: str
    ts: float
    prompt_hash: str            # sha256[:16] of the user prompt
    score: float                # 0.0-1.0 "did I solve it?"
    verdict: str                # one of _VALID_VERDICTS
    lesson: str                 # ≤140 chars, imperative
    tool_count: int             # tools invoked in this turn
    hit_max_steps: bool
    source: str                 # "gateway" | "swarm" | "subagent"
    parent_id: str | None       # task_id for swarm reflections
    curiosity_id: str | None    # candidate id when prompt was [CURIOSITY:<id>]


def enabled() -> bool:
    return os.environ.get("JARVIS_REFLECTION_ENABLED", "1") not in ("0", "false", "no")


def timeout_s() -> float:
    try:
        return float(os.environ.get("JARVIS_REFLECTION_TIMEOUT_S", "6"))
    except ValueError:
        return 6.0


_PROMPT_TEMPLATE = (
    "You are reviewing one JARVIS turn. Score how well the response solved "
    "the user's request.\n\n"
    "USER REQUEST:\n{prompt}\n\n"
    "JARVIS RESPONSE:\n{response}\n\n"
    "TOOLS USED: {tools}\n"
    "MAX_STEPS_HIT: {max_steps}\n\n"
    "Output exactly three lines, no preamble:\n"
    "SCORE: <number from 0.00 to 1.00>\n"
    "VERDICT: <solved|partial|off-target|tool-blocked>\n"
    "LESSON: <one imperative sentence starting with \"Next time\", max 20 words>\n"
)


def _parse(text: str) -> tuple[float | None, str | None, str | None]:
    """Pull the three fields out of the LM output. Returns Nones on bad
    parse — caller treats that as a drop."""
    score_m = _SCORE_RX.search(text)
    verdict_m = _VERDICT_RX.search(text)
    lesson_m = _LESSON_RX.search(text)
    if not (score_m and verdict_m and lesson_m):
        return None, None, None
    try:
        score = max(0.0, min(1.0, float(score_m.group(1))))
    except ValueError:
        return None, None, None
    verdict = verdict_m.group(1).lower()
    if verdict not in _VALID_VERDICTS:
        return None, None, None
    lesson = lesson_m.group(1).strip().strip('"')[:200]
    if not lesson:
        return None, None, None
    return score, verdict, lesson


def _append(r: Reflection) -> None:
    """Append-then-rotate JSONL store. Cheap I/O — only rewrites the
    file when we exceed _MAX_ENTRIES + 100 (amortized rotation)."""
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _STORE_PATH.open("a") as fh:
            fh.write(json.dumps(asdict(r)) + "\n")
        # Cheap line count via wc-style read; rotate when we drift past cap.
        try:
            n = sum(1 for _ in _STORE_PATH.open("r"))
        except Exception:
            n = 0
        if n > _MAX_ENTRIES + 100:
            tail = _STORE_PATH.read_text().splitlines()[-_MAX_ENTRIES:]
            _STORE_PATH.write_text("\n".join(tail) + "\n")
    except Exception:
        pass  # store failure must not break a turn


async def reflect(
    prompt: str,
    final_text: str,
    tool_summary: dict[str, int] | None = None,
    hit_max_steps: bool = False,
    *,
    client: Any,
    model: str | None = None,
    source: str = "gateway",
    parent_id: str | None = None,
) -> Reflection | None:
    """Run one reflection LM call. Returns the Reflection on success,
    None on disable / timeout / parse failure.

    Non-blocking by design — callers should wrap in `asyncio.create_task`
    after they've already returned `final_text` to the user."""
    if not enabled():
        return None
    if not prompt.strip() or not final_text.strip():
        return None

    tool_summary = tool_summary or {}
    tools_repr = (
        ", ".join(f"{k}:{v}" for k, v in sorted(tool_summary.items()))
        or "(none)"
    )
    body = _PROMPT_TEMPLATE.format(
        prompt=prompt[:600],
        response=final_text[:800],
        tools=tools_repr,
        max_steps=str(hit_max_steps),
    )

    try:
        completion = await asyncio.wait_for(
            client.complete(
                [{"role": "user", "content": body}],
                model=model,
                temperature=0.2,
                max_tokens=120,
            ),
            timeout=timeout_s(),
        )
    except asyncio.TimeoutError:
        bus.publish("reflection.timeout", "reflection", {
            "source": source, "parent_id": parent_id,
        })
        return None
    except Exception:
        return None

    score, verdict, lesson = _parse(getattr(completion, "text", "") or "")
    if score is None:
        return None

    # Pull a [CURIOSITY:<id>] tag out of the prompt header if present —
    # that's autonomy.py marking the turn as coming from a curiosity
    # dispatch. Lets curiosity.py populate `c.outcome` post-hoc.
    cur_match = _CURIOSITY_TAG_RX.search(prompt[:200])
    curiosity_id = cur_match.group(1) if cur_match else None

    r = Reflection(
        id=uuid.uuid4().hex,
        ts=time.time(),
        prompt_hash=sha256(prompt.encode("utf-8", "replace")).hexdigest()[:16],
        score=score,
        verdict=verdict,                # type: ignore[arg-type]
        lesson=lesson,                  # type: ignore[arg-type]
        tool_count=sum(tool_summary.values()),
        hit_max_steps=hit_max_steps,
        source=source,
        parent_id=parent_id,
        curiosity_id=curiosity_id,
    )
    _append(r)
    bus.publish("reflection.recorded", "reflection", asdict(r))
    return r


def read_recent(limit: int = 50) -> list[dict[str, Any]]:
    """Newest-first read of the JSONL store. Used by /reflections."""
    if not _STORE_PATH.exists():
        return []
    try:
        lines = _STORE_PATH.read_text().splitlines()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
