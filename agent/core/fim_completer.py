"""
C15.1 — Fill-in-the-Middle (FIM) completion.

Asks the active LM Studio model to fill the gap between a `prefix` and
`suffix`. Designed for Qwen 2.5 Coder's FIM token template (which most
recent code models — DeepSeek Coder, CodeLlama, StarCoder — also
understand). On non-FIM models the prefix-only continuation usually
degrades to "keep writing from prefix" which is still useful.

Public entry: `complete(prefix, suffix, max_tokens, model)` → result dict.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("jarvis.fim_completer")


_FIM_TEMPLATE = (
    "<|fim_prefix|>{prefix}<|fim_suffix|>{suffix}<|fim_middle|>"
)
# Some models terminate completions early; trim everything after these
# stop sequences if they surface in the output. (LM Studio doesn't always
# honor the `stop` parameter perfectly, so we double-check after.)
_STOP_SEQS = (
    "<|fim_pad|>", "<|endoftext|>", "<|eot_id|>", "<|im_end|>",
    "<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>",
)
# Prefix-trim: some models echo a leading "Here is the middle:" or a
# newline before the actual code. Drop common boilerplate.
_BOILERPLATE_PREFIXES = (
    "Here is", "Here's", "The middle", "Sure,", "Sure!",
)

# Soft caps so a runaway response doesn't fill the entire editor.
_MAX_TOKENS_FLOOR = 16
_MAX_TOKENS_CEIL = 256
# Hard cap on prefix/suffix bytes sent to the model — keeps the prompt
# bounded and prevents the user from accidentally paying for a 100KB
# file with one cursor click.
_CTX_BYTES = 4000


def _trim(text: str) -> str:
    """Strip stop-sequence tails + common boilerplate prefixes."""
    out = text
    for s in _STOP_SEQS:
        idx = out.find(s)
        if idx >= 0:
            out = out[:idx]
    # Drop a one-line "Here is the middle:" preamble if present.
    first_line, _, rest = out.partition("\n")
    for p in _BOILERPLATE_PREFIXES:
        if first_line.strip().startswith(p):
            out = rest
            break
    return out.rstrip()


async def _acomplete(prompt: str, max_tokens: int, model: str | None) -> dict[str, Any]:
    """Single LM Studio call. Returns {ok, text, model, latency_ms}."""
    from agent.core.lm_studio import get_client
    t0 = time.monotonic()
    try:
        client = get_client()
        result = await client.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
            model=model,
        )
    except Exception as e:                                       # noqa: BLE001
        logger.debug("FIM call failed: %s", e)
        return {
            "ok": False, "error": str(e)[:200],
            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
        }
    return {
        "ok": True,
        "text": result.text or "",
        "model": getattr(result, "model", model or ""),
        "latency_ms": round((time.monotonic() - t0) * 1000, 1),
    }


def complete(
    prefix: str,
    suffix: str = "",
    max_tokens: int = 80,
    model: str | None = None,
) -> dict[str, Any]:
    """Run one FIM completion. Synchronous wrapper for endpoint callers
    that aren't already on an event loop."""
    # Bound the prefix and suffix so an editor-full of context doesn't
    # blow the prompt window. Keep the BACK of the prefix and FRONT of
    # the suffix — that's the cursor-adjacent context that matters.
    prefix = prefix[-_CTX_BYTES:]
    suffix = suffix[:_CTX_BYTES]
    max_tokens = max(_MAX_TOKENS_FLOOR, min(_MAX_TOKENS_CEIL, int(max_tokens)))

    prompt = _FIM_TEMPLATE.format(prefix=prefix, suffix=suffix)
    raw = asyncio.run(_acomplete(prompt, max_tokens, model))
    if not raw.get("ok"):
        return {
            "ok": False, "completion": "", "raw": "",
            "latency_ms": raw.get("latency_ms"),
            "error": raw.get("error"),
        }
    text = raw.get("text", "") or ""
    cleaned = _trim(text)
    return {
        "ok": True,
        "completion": cleaned,
        "raw": text,
        "model": raw.get("model"),
        "latency_ms": raw.get("latency_ms"),
        "prefix_bytes": len(prefix),
        "suffix_bytes": len(suffix),
    }
