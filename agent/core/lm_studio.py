"""
JARVIS — LM Studio Client
agent/core/lm_studio.py
=====================================================================
All communication with LM Studio goes through this module — nothing
else in the codebase touches the OpenAI client directly.

Responsibilities:
  - Open and reuse a single AsyncOpenAI client per process.
  - Test connectivity and surface clear errors.
  - Send chat completions (streaming and non-streaming).
  - Wrap LM Studio's tool-call format into our internal ToolCall type.
  - Enforce token budgets and enforce the local-only URL constraint.
  - Provide a synchronous wrapper for callers that don't use asyncio yet.

What this module does NOT do:
  - Decide what to put in the messages (that's gateway.py).
  - Route between models (that's router.py).
  - Parse or execute tool call arguments (that's the tool registry).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, Any

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
)

from agent.core.config import config, LMStudioConfig


# Single source of truth for the reasoning_effort default. qwen2.5-coder
# (current primary) ignores it; reasoning-capable models like gemma-3-
# thinking use it. Override per process with JARVIS_REASONING_EFFORT.
DEFAULT_REASONING_EFFORT = "none"


# ─────────────────────────────────────────────────────────────────────────────
# Internal types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """
    A tool call requested by the model, ready for the tool registry.

    id       — LM Studio's unique ID for this call (needed for tool_result messages).
    name     — The function name to invoke, e.g. "read_file".
    args     — Parsed arguments dict, e.g. {"path": "/home/user/notes.md"}.
    raw_args — The original JSON string from the model (for audit logging).
    """
    id: str
    name: str
    args: dict[str, Any]
    raw_args: str


@dataclass
class CompletionResult:
    """
    The outcome of a single LM Studio API call.

    Exactly one of `text` or `tool_calls` will be populated — never both,
    never neither (if the API returns something unexpected we raise, not return).

    Fields:
      text        — The model's text reply (when it chose to respond in prose).
      tool_calls  — List of tool calls (when it chose to invoke tools).
      model       — Which model actually answered (may differ from requested).
      usage_tokens — Total tokens consumed (prompt + completion).
      latency_ms  — Wall-clock time for the API call in milliseconds.
    """
    text: Optional[str]                 = None
    tool_calls: list[ToolCall]          = field(default_factory=list)
    model: str                          = ""
    usage_tokens: int                   = 0
    latency_ms: float                   = 0.0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def has_text(self) -> bool:
        return self.text is not None and self.text.strip() != ""


@dataclass
class ConnectionStatus:
    """Result of a connectivity probe to LM Studio."""
    reachable: bool
    models: list[str]           = field(default_factory=list)
    primary_loaded: bool        = False
    error: Optional[str]        = None
    latency_ms: float           = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

class LMStudioClient:
    """
    Async client for LM Studio's OpenAI-compatible API.

    Use the module-level `get_client()` to get the singleton instance.
    Direct instantiation is only needed for testing with a custom config.

    Usage:
        from agent.core.lm_studio import get_client
        client = get_client()
        status = await client.check_connection()
        result = await client.complete(messages=[...])
    """

    def __init__(self, cfg: Optional[LMStudioConfig] = None) -> None:
        self._cfg = cfg or config.lm_studio
        self._validate_url()
        self._client = AsyncOpenAI(
            base_url=self._cfg.base_url,
            api_key="lm-studio",          # LM Studio ignores the key; required by openai lib
            timeout=self._cfg.timeout_seconds,
            max_retries=0,                 # We handle retries ourselves for better control
        )

    def _validate_url(self) -> None:
        """
        Refuse to connect to non-localhost URLs unless internet_access is True.

        This is a hard guard — the config security setting cannot be bypassed
        by pointing base_url at a remote host when internet is disabled.
        """
        url = self._cfg.base_url.lower()
        is_local = any(h in url for h in ("localhost", "127.0.0.1", "::1"))
        if not is_local and not config.security.internet_access:
            raise ValueError(
                f"LM Studio base_url '{self._cfg.base_url}' is not localhost, "
                "but security.internet_access = false. "
                "Either use a localhost URL or set internet_access = true in jarvis.toml."
            )

    # ── Connectivity ──────────────────────────────────────────────────────────

    async def check_connection(self) -> ConnectionStatus:
        """
        Probe LM Studio and return a ConnectionStatus.

        Never raises — returns ConnectionStatus(reachable=False, error=...) on failure.
        Safe to call at startup, on reconnect, or from the UI status bar.
        """
        t0 = time.monotonic()
        try:
            models_page = await self._client.models.list()
            latency = (time.monotonic() - t0) * 1000
            model_ids = [m.id for m in models_page.data]
            primary_loaded = self._cfg.primary_model in model_ids
            return ConnectionStatus(
                reachable=True,
                models=model_ids,
                primary_loaded=primary_loaded,
                latency_ms=latency,
            )
        except APIConnectionError as e:
            return ConnectionStatus(
                reachable=False,
                error=(
                    f"Cannot reach LM Studio at {self._cfg.base_url}. "
                    f"Is LM Studio running with 'Start Server' enabled? ({e})"
                ),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            return ConnectionStatus(
                reachable=False,
                error=f"Unexpected error probing LM Studio: {e}",
                latency_ms=(time.monotonic() - t0) * 1000,
            )

    # ── Core completion ───────────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
        reasoning_effort: Optional[str] = None,
    ) -> CompletionResult:
        """
        Send a chat completion request and return a CompletionResult.

        Args:
            messages:    Full conversation history in OpenAI message format.
            model:       Model ID override. Defaults to primary_model from config.
            tools:       Tool definitions in OpenAI function-calling format.
                         Passed only if the model supports tool calling.
            max_tokens:  Response token limit. Defaults to config.max_output_tokens.
            temperature: Sampling temperature. Low (0.1-0.3) for agent tasks.

        Returns:
            CompletionResult with either .text or .tool_calls populated.

        Raises:
            LMStudioConnectionError: If LM Studio is unreachable.
            LMStudioModelError:      If the model returns an API error.
            LMStudioTimeoutError:    If the request times out.
        """
        use_model = model or self._cfg.primary_model
        use_max_tokens = max_tokens or self._cfg.max_output_tokens

        # G6.6 — reasoning_effort: env-var-driven (low|medium|high|none).
        # qwen2.5-coder-7b-instruct (current primary) doesn't expose internal
        # reasoning, so default is "none". For reasoning-capable models like
        # gemma-3-thinking variants, set JARVIS_REASONING_EFFORT=low to enable
        # — drops per-turn reasoning_content cost while keeping JARVIS's own
        # thinker/memory/tool pipeline intact.
        use_effort = reasoning_effort or os.environ.get(
            "JARVIS_REASONING_EFFORT", DEFAULT_REASONING_EFFORT,
        )

        kwargs: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "max_tokens": use_max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if use_effort and use_effort.lower() != "none":
            # OpenAI-compatible reasoning_effort parameter. LM Studio passes
            # this through to llama.cpp for reasoning-capable models; older
            # builds may ignore unknown kwargs harmlessly via extra_body.
            kwargs["extra_body"] = {"reasoning_effort": use_effort.lower()}

        t0 = time.monotonic()
        for attempt in range(self._cfg.max_retries + 1):
            try:
                response: ChatCompletion = await self._client.chat.completions.create(**kwargs)
                latency = (time.monotonic() - t0) * 1000
                # G3.3 — feed the performance watchdog so /bots/performance-watchdog
                # actually has data. Best-effort: never break the request path.
                try:
                    from agent.bots.performance_watchdog import watchdog
                    watchdog.record_lm_latency(latency)
                except Exception:
                    pass
                return self._parse_completion(response, latency)

            except APITimeoutError as e:
                raise LMStudioTimeoutError(
                    f"LM Studio timed out after {self._cfg.timeout_seconds}s. "
                    "The model may still be loading — try again in a moment."
                ) from e

            except APIConnectionError as e:
                if attempt == self._cfg.max_retries:
                    raise LMStudioConnectionError(
                        f"LM Studio unreachable after {attempt + 1} attempts: {e}"
                    ) from e
                await asyncio.sleep(0.5 * (attempt + 1))

            except APIStatusError as e:
                raise LMStudioModelError(
                    f"LM Studio API error {e.status_code}: {e.message}"
                ) from e

        # Should be unreachable (loop always raises or returns) — defensive
        raise LMStudioConnectionError("Completion failed: exhausted retries.")

    # ── Streaming completion ──────────────────────────────────────────────────

    async def complete_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """
        Stream a chat completion, yielding text chunks as they arrive.

        Yields individual text delta strings. Does not yield tool calls —
        if tool calls appear in the stream, they are buffered and returned
        as a single CompletionResult at the end (yielded as a special JSON token).

        Usage:
            async for chunk in client.complete_stream(messages):
                print(chunk, end="", flush=True)
        """
        use_model = model or self._cfg.primary_model
        use_max_tokens = max_tokens or self._cfg.max_output_tokens

        kwargs: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "max_tokens": use_max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                chunk: ChatCompletionChunk
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except APITimeoutError as e:
            raise LMStudioTimeoutError(str(e)) from e
        except APIConnectionError as e:
            raise LMStudioConnectionError(str(e)) from e
        except APIStatusError as e:
            raise LMStudioModelError(f"{e.status_code}: {e.message}") from e

    # ── Tool-aware streaming completion (G1.5) ────────────────────────────────

    async def complete_events(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
        reasoning_effort: Optional[str] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Stream a chat completion as events, yielding text deltas as they
        arrive AND a final {"type":"result", "result": CompletionResult}.

        Used by gateway.ask_events() for first-token-fast chat UX while
        preserving full tool-call semantics. Tool call deltas are buffered
        and assembled into ToolCall objects on the final event.

        Event shapes:
            {"type":"delta", "content": "...chunk..."}
            {"type":"result", "result": CompletionResult}
        """
        use_model = model or self._cfg.primary_model
        use_max_tokens = max_tokens or self._cfg.max_output_tokens
        use_effort = reasoning_effort or os.environ.get(
            "JARVIS_REASONING_EFFORT", DEFAULT_REASONING_EFFORT,
        )

        kwargs: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "max_tokens": use_max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if use_effort and use_effort.lower() != "none":
            kwargs["extra_body"] = {"reasoning_effort": use_effort.lower()}

        full_text = ""
        tool_buf: dict[int, dict[str, Any]] = {}
        t0 = time.monotonic()

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                chunk: ChatCompletionChunk
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    full_text += delta.content
                    yield {"type": "delta", "content": delta.content}
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        slot = tool_buf.setdefault(
                            idx, {"id": "", "name": "", "args": ""}
                        )
                        if tc_delta.id:
                            slot["id"] = tc_delta.id
                        fn = tc_delta.function
                        if fn:
                            if fn.name:
                                slot["name"] = fn.name
                            if fn.arguments:
                                slot["args"] += fn.arguments
        except APITimeoutError as e:
            raise LMStudioTimeoutError(str(e)) from e
        except APIConnectionError as e:
            raise LMStudioConnectionError(str(e)) from e
        except APIStatusError as e:
            raise LMStudioModelError(f"{e.status_code}: {e.message}") from e

        latency = (time.monotonic() - t0) * 1000
        try:
            from agent.bots.performance_watchdog import watchdog
            watchdog.record_lm_latency(latency)
        except Exception:
            pass

        tool_calls: list[ToolCall] = []
        for idx in sorted(tool_buf.keys()):
            s = tool_buf[idx]
            if not s["name"]:
                continue
            try:
                args = json.loads(s["args"]) if s["args"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(
                id=s["id"] or f"call_{idx}",
                name=s["name"],
                args=args,
                raw_args=s["args"],
            ))

        result = CompletionResult(
            text=full_text if full_text else None,
            tool_calls=tool_calls,
            model=use_model,
            latency_ms=latency,
        )
        yield {"type": "result", "result": result}

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse_completion(
        self, response: ChatCompletion, latency_ms: float
    ) -> CompletionResult:
        """
        Convert a raw ChatCompletion object into our CompletionResult.

        Handles three response shapes:
          1. Plain text reply (most common).
          2. Native tool calls (model supports function calling).
          3. Text that looks like a ReAct tool call (fallback for models
             that don't support native function calling — we parse the
             text format and surface it as a ToolCall).
        """
        choice = response.choices[0]
        message = choice.message
        usage = response.usage.total_tokens if response.usage else 0

        # Case 1: native tool calls
        if message.tool_calls:
            tool_calls = [
                self._parse_tool_call(tc) for tc in message.tool_calls
            ]
            return CompletionResult(
                tool_calls=tool_calls,
                model=response.model,
                usage_tokens=usage,
                latency_ms=latency_ms,
            )

        content = (message.content or "").strip()

        # Case 2: ReAct-style tool call embedded in text
        # Format: ACTION: tool_name\nARGS: {"key": "value"}
        react_tool = _parse_react_tool_call(content)
        if react_tool:
            return CompletionResult(
                tool_calls=[react_tool],
                model=response.model,
                usage_tokens=usage,
                latency_ms=latency_ms,
            )

        # Case 3: plain text
        return CompletionResult(
            text=content,
            model=response.model,
            usage_tokens=usage,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _parse_tool_call(tc: ChatCompletionMessageToolCall) -> ToolCall:
        """Convert an OpenAI tool call object into our ToolCall dataclass."""
        raw_args = tc.function.arguments
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            # Model produced invalid JSON for args — use empty dict and log raw
            args = {}
        return ToolCall(
            id=tc.id,
            name=tc.function.name,
            args=args,
            raw_args=raw_args,
        )


# ─────────────────────────────────────────────────────────────────────────────
# ReAct parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_react_tool_call(text: str) -> Optional[ToolCall]:
    """
    Parse a ReAct-style tool call from plain model text.

    This is our fallback for models that don't support native function calling.
    The model is prompted to output tool calls in this format:

        THOUGHT: I need to read the file to understand its contents.
        ACTION: read_file
        ARGS: {"path": "/home/user/project/main.py"}

    We extract ACTION and ARGS, parse the JSON, and return a ToolCall.
    Returns None if the text doesn't match the pattern.

    Security: we do NOT eval() the args. JSON parsing only.
    """
    import re

    action_match = re.search(
        r"ACTION:\s*([a-z_][a-z0-9_]*)", text, re.IGNORECASE
    )
    if not action_match:
        return None

    name = action_match.group(1).strip().lower()

    args_match = re.search(
        r"ARGS:\s*(\{.*?\})", text, re.IGNORECASE | re.DOTALL
    )
    raw_args = "{}"
    args: dict[str, Any] = {}

    if args_match:
        raw_args = args_match.group(1).strip()
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}

    return ToolCall(
        id=f"react_{name}_{int(time.monotonic() * 1000)}",
        name=name,
        args=args,
        raw_args=raw_args,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class LMStudioError(Exception):
    """Base class for all LM Studio client errors."""


class LMStudioConnectionError(LMStudioError):
    """LM Studio is not reachable on the configured port."""


class LMStudioTimeoutError(LMStudioError):
    """Request to LM Studio timed out."""


class LMStudioModelError(LMStudioError):
    """LM Studio returned an API-level error (bad request, model error, etc.)."""


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_client: Optional[LMStudioClient] = None


def get_client(cfg: Optional[LMStudioConfig] = None) -> LMStudioClient:
    """
    Return the module-level LMStudioClient singleton.

    Pass cfg only in tests — production code always uses the config singleton.
    """
    global _client
    if cfg is not None:
        return LMStudioClient(cfg)
    if _client is None:
        _client = LMStudioClient()
    return _client


def reset_client() -> None:
    """Force the singleton to be recreated on next get_client() call. Tests only."""
    global _client
    _client = None


# ─────────────────────────────────────────────────────────────────────────────
# Sync wrapper for non-async callers
# ─────────────────────────────────────────────────────────────────────────────

def check_connection_sync() -> ConnectionStatus:
    """Synchronous wrapper around check_connection(). Blocks until complete."""
    return asyncio.run(get_client().check_connection())


def complete_sync(
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> CompletionResult:
    """Synchronous wrapper around complete(). Blocks until complete."""
    return asyncio.run(get_client().complete(messages, **kwargs))
