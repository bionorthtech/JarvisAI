"""
Token compaction — keeps session history from growing unbounded.

When the total token estimate of session.history exceeds TOKEN_THRESHOLD,
the compactor:
  1. Summarizes the oldest turns (everything except the last KEEP_VERBATIM turns)
     into a single compact block via an LLM call.
  2. Replaces those turns with a single synthetic assistant message containing
     the compact summary.
  3. The most recent KEEP_VERBATIM turns are kept exactly as-is.

This preserves continuity while preventing context overflow.

Token estimation: 1 token ≈ 4 chars (conservative). The real cost is the
LLM's context window; we compact well before hitting it.
"""
import logging
from typing import Optional

logger = logging.getLogger("jarvis.compactor")

# Compact when total estimated tokens exceed this
TOKEN_THRESHOLD = 6000

# Always keep the last N turns verbatim (each turn = one {role, content} dict)
KEEP_VERBATIM = 6

_COMPACT_PROMPT = """\
You are summarizing a conversation between a user and JARVIS (an AI agent).
Produce a concise, factual summary that preserves:
- What the user asked for
- What JARVIS did (tool calls executed, files changed, commands run)
- Key results, errors, and decisions
- Any context JARVIS will need to continue helping

Be specific about files, values, and outcomes. Omit pleasantries.
Write in past tense, 150-250 words max.

Conversation to summarize:
{history}"""


def _estimate_tokens(history: list[dict]) -> int:
    """Rough token estimate: 4 chars ≈ 1 token."""
    total_chars = sum(len(str(m.get("content", ""))) for m in history)
    return total_chars // 4


def _format_history_for_summary(turns: list[dict]) -> str:
    lines = []
    for m in turns:
        role = m.get("role", "?").upper()
        content = str(m.get("content", ""))[:800]   # cap per-turn to stay in model window
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


async def compact(
    history: list[dict],
    client,
    model: Optional[str] = None,
    threshold: int = TOKEN_THRESHOLD,
    keep_verbatim: int = KEEP_VERBATIM,
) -> list[dict]:
    """
    Compact history if it exceeds threshold. Returns (possibly shortened) history.
    If compaction fails or is not needed, returns original history unchanged.
    """
    if _estimate_tokens(history) <= threshold:
        return history

    if len(history) <= keep_verbatim:
        # Nothing to compact
        return history

    to_summarize = history[:-keep_verbatim]
    to_keep = history[-keep_verbatim:]

    logger.info(
        "compacting %d turns → summary + %d verbatim (est %d tokens)",
        len(to_summarize), len(to_keep), _estimate_tokens(history),
    )

    summary_text = await _summarize(to_summarize, client, model)

    compacted_block = {
        "role": "assistant",
        "content": f"[COMPACTED HISTORY — earlier conversation summary]\n{summary_text}\n[END COMPACTED HISTORY]",
    }

    new_history = [compacted_block] + to_keep
    logger.info(
        "compaction done: %d turns → %d turns (est %d tokens)",
        len(history), len(new_history), _estimate_tokens(new_history),
    )
    return new_history


async def _summarize(turns: list[dict], client, model: Optional[str]) -> str:
    """Ask the LLM to summarize a list of turns."""
    try:
        formatted = _format_history_for_summary(turns)
        messages = [
            {"role": "system", "content": "You produce concise conversation summaries. Be factual and specific."},
            {"role": "user", "content": _COMPACT_PROMPT.format(history=formatted)},
        ]
        result = await client.complete(messages, model=model)
        text = result.text.strip()
        if text:
            return text
    except Exception as e:
        logger.warning("compaction summary failed: %s", e)

    # Fallback: plain truncation summary
    count = len(turns)
    roles = [t.get("role", "?") for t in turns]
    return f"[Summary unavailable — {count} earlier turns from {roles[0]} to {roles[-1]} were compacted.]"
