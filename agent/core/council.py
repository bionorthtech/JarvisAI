"""
Council Review — multi-model safety check for DANGER/CRITICAL tool calls.

Before a DANGER or CRITICAL action reaches the user confirmation dialog,
two lightweight reviewer calls independently evaluate it.
A chairman call makes the final APPROVE / BLOCK / MODIFY verdict.

This prevents the main model from reasoning its way into something dangerous.
The council is fast (~3 LLM calls at low token count) and only fires for
DANGER/CRITICAL tier actions.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("jarvis.council")

_REVIEWER_PROMPT = """\
You are a security reviewer for an AI agent. Evaluate this proposed action.
Reply with JSON only: {{"safe": true|false, "reason": "one sentence"}}

Tool: {tool}
Args: {args}
Context: {context}

Is this action safe and appropriate given the context?"""

_CHAIRMAN_PROMPT = """\
You are the safety chairman for an AI agent. Two reviewers evaluated a \
proposed action. Make the final call.

Tool: {tool}
Args: {args}
Reviewer 1: {review1}
Reviewer 2: {review2}

Reply with JSON only:
{{"verdict": "APPROVE"|"BLOCK"|"MODIFY", "reason": "one sentence", "modified_args": null}}

APPROVE  = action is safe, proceed to user confirmation
BLOCK    = action is dangerous/unnecessary, do not proceed
MODIFY   = action needs adjustment (provide modified_args)"""


@dataclass
class CouncilVerdict:
    verdict: str        # "APPROVE" | "BLOCK" | "MODIFY"
    reason: str
    modified_args: Optional[dict] = None

    @property
    def approved(self) -> bool:
        return self.verdict == "APPROVE"


async def _reviewer_call(client, model: Optional[str], tool: str, args: dict, context: str) -> dict:
    """Single reviewer evaluation. Returns dict with safe + reason."""
    import json, re
    try:
        messages = [
            {"role": "system", "content": "You are a security reviewer. Reply with JSON only."},
            {"role": "user", "content": _REVIEWER_PROMPT.format(
                tool=tool,
                args=json.dumps(args)[:300],
                context=context[:200],
            )},
        ]
        result = await client.complete(messages, model=model)
        text = result.text.strip()
        m = re.search(r'\{.*?\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.debug("reviewer call failed: %s", e)
    return {"safe": True, "reason": "reviewer unavailable (defaulting safe)"}


async def review(
    tool_name: str,
    args: dict,
    tier: str,
    context: str = "",
    client=None,
    model: Optional[str] = None,
) -> CouncilVerdict:
    """
    Run council review for a DANGER/CRITICAL action.
    Returns CouncilVerdict. If client is None or review fails, defaults to APPROVE
    (the existing confirmation dialog still fires — council is an extra layer).
    """
    if tier not in ("DANGER", "CRITICAL"):
        return CouncilVerdict(verdict="APPROVE", reason="below council threshold")

    if client is None:
        return CouncilVerdict(verdict="APPROVE", reason="council unavailable (no client)")

    try:
        import json, re

        # Run two reviewers in parallel
        review1, review2 = await asyncio.gather(
            _reviewer_call(client, model, tool_name, args, context),
            _reviewer_call(client, model, tool_name, args, context),
        )

        logger.info(
            "council reviews: r1=%s r2=%s | tool=%s",
            review1.get("safe"), review2.get("safe"), tool_name,
        )

        # If both reviewers flag unsafe, go straight to BLOCK without chairman
        if not review1.get("safe", True) and not review2.get("safe", True):
            reason = review1.get("reason", "both reviewers flagged unsafe")
            logger.warning("council BLOCK (unanimous): %s %s | %s", tool_name, args, reason)
            return CouncilVerdict(verdict="BLOCK", reason=reason)

        # Chairman makes final call
        messages = [
            {"role": "system", "content": "You are a safety chairman. Reply with JSON only."},
            {"role": "user", "content": _CHAIRMAN_PROMPT.format(
                tool=tool_name,
                args=json.dumps(args)[:300],
                review1=json.dumps(review1),
                review2=json.dumps(review2),
            )},
        ]
        result = await client.complete(messages, model=model)
        text = result.text.strip()
        m = re.search(r'\{.*?\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group())
            verdict = data.get("verdict", "APPROVE").upper()
            if verdict not in ("APPROVE", "BLOCK", "MODIFY"):
                verdict = "APPROVE"
            modified = data.get("modified_args") or None
            reason = data.get("reason", "")
            logger.info("council chairman verdict: %s | %s", verdict, reason)
            return CouncilVerdict(verdict=verdict, reason=reason, modified_args=modified)

    except Exception as e:
        logger.warning("council review failed: %s — defaulting APPROVE", e)

    return CouncilVerdict(verdict="APPROVE", reason="council error (defaulting approve)")
