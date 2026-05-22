"""
Sequential Thinking — pre-pass reasoning for complex requests.

Before the main ReAct loop, JARVIS runs a lightweight planning pass:
  1. Classify complexity (SIMPLE / MEDIUM / COMPLEX)
  2. For COMPLEX: generate a numbered step plan
  3. Inject the plan as a system-level thinking block

This dramatically reduces hallucinations on multi-step tasks by forcing
the model to reason before acting — same principle as chain-of-thought.

Enable: JARVIS_SEQUENTIAL_THINKING=1 (or always-on for COMPLEX tier)
"""
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("jarvis.thinker")

_ENABLED = os.environ.get("JARVIS_SEQUENTIAL_THINKING", "1").strip() == "1"

# Keywords that signal complexity — same set used by model router but extended
_COMPLEX_SIGNALS = {
    "refactor", "architect", "design", "migrate", "upgrade", "integrate",
    "implement", "build", "create a", "write a", "set up", "configure",
    "debug", "fix", "trace", "why is", "how do i", "explain", "analyse",
    "analyze", "optimize", "review", "audit", "plan",
}

_CLASSIFY_PROMPT = """\
Classify this user request by complexity. Reply with JSON only, no prose.

Request: {prompt}

Reply format:
{{"complexity": "SIMPLE"|"MEDIUM"|"COMPLEX", "reason": "one sentence"}}

SIMPLE  = single lookup, factual, one-step action
MEDIUM  = 2-4 steps, some reasoning needed
COMPLEX = multi-file changes, multi-step plan, debugging, architecture"""

_THINK_PROMPT = """\
You are JARVIS's reasoning engine. Before the main agent acts, produce a \
concise numbered plan for this request. Be specific about files and steps. \
Do NOT execute — only plan.

Request: {prompt}

Produce a plan with 3-7 numbered steps. Each step: one line, action verb first."""


def _word_signals(prompt: str) -> bool:
    words = set(re.findall(r"\b\w+\b", prompt.lower()))
    return bool(words & _COMPLEX_SIGNALS)


async def classify(prompt: str, client, model: Optional[str] = None) -> str:
    """Return 'SIMPLE', 'MEDIUM', or 'COMPLEX'. Falls back to heuristic on error."""
    if not _ENABLED:
        return "SIMPLE"

    # Fast heuristic first to skip the LLM call for obvious cases
    if len(prompt.split()) < 6 and not _word_signals(prompt):
        return "SIMPLE"

    try:
        from agent.core.lm_studio import CompletionResult
        messages = [
            {"role": "system", "content": "You are a complexity classifier. Reply with JSON only."},
            {"role": "user", "content": _CLASSIFY_PROMPT.format(prompt=prompt[:400])},
        ]
        result: CompletionResult = await client.complete(messages, model=model)
        text = result.text.strip()
        # Extract JSON even if model adds surrounding text
        m = re.search(r'\{.*?\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group())
            level = data.get("complexity", "MEDIUM").upper()
            if level in ("SIMPLE", "MEDIUM", "COMPLEX"):
                logger.debug("complexity=%s reason=%s", level, data.get("reason"))
                return level
    except Exception as e:
        logger.debug("classify failed (%s), using heuristic", e)

    # Heuristic fallback
    if _word_signals(prompt) or len(prompt.split()) > 30:
        return "COMPLEX"
    return "MEDIUM"


async def think(prompt: str, client, model: Optional[str] = None) -> str:
    """
    Generate a step-by-step plan for a complex request.
    Returns the plan as a formatted string to inject into system context.
    Returns empty string if thinking is disabled or request is simple.
    """
    if not _ENABLED:
        return ""

    complexity = await classify(prompt, client, model)
    if complexity != "COMPLEX":
        return ""

    try:
        messages = [
            {"role": "system", "content": "You are a planning assistant. Produce a concise numbered plan, nothing else."},
            {"role": "user", "content": _THINK_PROMPT.format(prompt=prompt[:600])},
        ]
        from agent.core.lm_studio import CompletionResult
        result: CompletionResult = await client.complete(messages, model=model)
        plan = result.text.strip()
        if plan:
            logger.debug("sequential thinking produced plan (%d chars)", len(plan))
            return f"[JARVIS SEQUENTIAL THINKING — plan before acting]\n{plan}\n[END THINKING]"
    except Exception as e:
        logger.debug("thinker failed: %s", e)

    return ""
