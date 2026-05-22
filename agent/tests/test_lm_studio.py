"""
Tests for agent/core/lm_studio.py
stdlib unittest + unittest.mock — zero external dependencies.
Run: python -m unittest agent.tests.test_lm_studio -v

Approach:
  All network calls are mocked. We test:
    1. URL validation (security — refuse non-localhost without internet_access)
    2. Connection probe logic (reachable / unreachable / error paths)
    3. Completion parsing (text / native tool calls / ReAct fallback)
    4. Retry logic (connection errors retried, timeout/status errors are not)
    5. Streaming (yields chunks correctly)
    6. ReAct parser (all valid formats, all invalid formats)
    7. Singleton management (get_client, reset_client)
    8. Sync wrappers (thin wrappers — just verify they return correct types)

We do NOT test live LM Studio connectivity here — that is the job of the
manual smoke test at the bottom (`python lm_studio.py --smoke-test`).
"""

import sys
import asyncio
import json
import dataclasses
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.core.config import LMStudioConfig
from agent.core.lm_studio import (
    LMStudioClient,
    CompletionResult,
    ConnectionStatus,
    ToolCall,
    LMStudioConnectionError,
    LMStudioTimeoutError,
    LMStudioModelError,
    _parse_react_tool_call,
    get_client,
    reset_client,
)
from openai import APIConnectionError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(**overrides) -> LMStudioConfig:
    return dataclasses.replace(LMStudioConfig(), **overrides)


def _make_completion_response(
    content: str = "",
    tool_calls=None,
    model: str = "gemma-4-e4b",
    total_tokens: int = 100,
) -> MagicMock:
    """Build a mock ChatCompletion response object."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []

    choice = MagicMock()
    choice.message = msg

    usage = MagicMock()
    usage.total_tokens = total_tokens

    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage = usage
    return resp


def _make_tool_call_mock(id: str, name: str, args: dict) -> MagicMock:
    """Build a mock ChatCompletionMessageToolCall."""
    fn = MagicMock()
    fn.name = name
    fn.arguments = json.dumps(args)

    tc = MagicMock()
    tc.id = id
    tc.function = fn
    return tc


def run(coro):
    """Run an async coroutine in tests using a fresh event loop per call."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1. URL validation
# ─────────────────────────────────────────────────────────────────────────────

class TestURLValidation(unittest.TestCase):

    def _make_client(self, url: str, internet: bool = False) -> LMStudioClient:
        cfg = _cfg(base_url=url)
        # Patch the security config internet_access flag
        import agent.core.lm_studio as m
        orig = m.config
        try:
            new_security = dataclasses.replace(
                orig.security, internet_access=internet
            )
            m.config = dataclasses.replace(orig, security=new_security)
            return LMStudioClient(cfg)
        finally:
            m.config = orig

    def test_localhost_always_accepted(self):
        # Should not raise
        LMStudioClient(_cfg(base_url="http://localhost:1234/v1"))

    def test_127_0_0_1_accepted(self):
        LMStudioClient(_cfg(base_url="http://127.0.0.1:1234/v1"))

    def test_remote_url_rejected_when_internet_off(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_client("http://192.168.1.100:1234/v1", internet=False)
        self.assertIn("internet_access", str(ctx.exception))

    def test_remote_url_accepted_when_internet_on(self):
        # Should not raise
        self._make_client("http://192.168.1.100:1234/v1", internet=True)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Connection probe
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckConnection(unittest.TestCase):

    def setUp(self):
        self.client = LMStudioClient(_cfg())

    def _mock_models(self, model_ids: list[str]):
        """Return an async mock that simulates models.list()."""
        model_objects = [MagicMock(id=m) for m in model_ids]
        page = MagicMock()
        page.data = model_objects
        self.client._client.models = MagicMock()
        self.client._client.models.list = AsyncMock(return_value=page)

    def test_reachable_returns_true(self):
        self._mock_models(["gemma-4-e4b"])
        status = run(self.client.check_connection())
        self.assertTrue(status.reachable)

    def test_lists_models(self):
        self._mock_models(["gemma-4-e4b", "mistral-7b"])
        status = run(self.client.check_connection())
        self.assertIn("gemma-4-e4b", status.models)
        self.assertIn("mistral-7b", status.models)

    def test_primary_loaded_true_when_present(self):
        self._mock_models(["qwen2.5-coder-7b-instruct"])
        status = run(self.client.check_connection())
        self.assertTrue(status.primary_loaded)

    def test_primary_loaded_false_when_absent(self):
        self._mock_models(["mistral-7b"])
        status = run(self.client.check_connection())
        self.assertFalse(status.primary_loaded)

    def test_connection_error_returns_reachable_false(self):
        self.client._client.models = MagicMock()
        self.client._client.models.list = AsyncMock(
            side_effect=APIConnectionError.__new__(APIConnectionError)
        )
        status = run(self.client.check_connection())
        self.assertFalse(status.reachable)
        self.assertIsNotNone(status.error)
        self.assertIn("LM Studio", status.error)

    def test_unexpected_error_returns_reachable_false(self):
        self.client._client.models = MagicMock()
        self.client._client.models.list = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )
        status = run(self.client.check_connection())
        self.assertFalse(status.reachable)

    def test_never_raises(self):
        """check_connection must NEVER raise — always returns ConnectionStatus."""
        self.client._client.models = MagicMock()
        self.client._client.models.list = AsyncMock(
            side_effect=Exception("totally unexpected")
        )
        try:
            run(self.client.check_connection())
        except Exception as e:
            self.fail(f"check_connection raised unexpectedly: {e}")

    def test_latency_is_positive(self):
        self._mock_models(["gemma-4-e4b"])
        status = run(self.client.check_connection())
        self.assertGreaterEqual(status.latency_ms, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Completion parsing — text response
# ─────────────────────────────────────────────────────────────────────────────

class TestCompletionText(unittest.TestCase):

    def setUp(self):
        self.client = LMStudioClient(_cfg())

    def _mock_complete(self, response_mock):
        self.client._client.chat = MagicMock()
        self.client._client.chat.completions = MagicMock()
        self.client._client.chat.completions.create = AsyncMock(
            return_value=response_mock
        )

    def test_text_reply_returns_text(self):
        resp = _make_completion_response(content="Hello, how can I help?")
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "hi"}]))
        self.assertTrue(result.has_text)
        self.assertFalse(result.has_tool_calls)
        self.assertEqual(result.text, "Hello, how can I help?")

    def test_model_name_captured(self):
        resp = _make_completion_response(content="ok", model="gemma-4-e4b")
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "hi"}]))
        self.assertEqual(result.model, "gemma-4-e4b")

    def test_token_usage_captured(self):
        resp = _make_completion_response(content="ok", total_tokens=42)
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "hi"}]))
        self.assertEqual(result.usage_tokens, 42)

    def test_latency_captured(self):
        resp = _make_completion_response(content="ok")
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "hi"}]))
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_whitespace_only_content_is_text(self):
        resp = _make_completion_response(content="   ")
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "hi"}]))
        # has_text checks for non-empty stripped content
        self.assertFalse(result.has_text)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Completion parsing — native tool calls
# ─────────────────────────────────────────────────────────────────────────────

class TestCompletionNativeToolCalls(unittest.TestCase):

    def setUp(self):
        self.client = LMStudioClient(_cfg())

    def _mock_complete(self, response_mock):
        self.client._client.chat = MagicMock()
        self.client._client.chat.completions = MagicMock()
        self.client._client.chat.completions.create = AsyncMock(
            return_value=response_mock
        )

    def test_tool_call_returned(self):
        tc = _make_tool_call_mock("call_1", "read_file", {"path": "/tmp/test.py"})
        resp = _make_completion_response(tool_calls=[tc])
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "read it"}]))
        self.assertTrue(result.has_tool_calls)
        self.assertFalse(result.has_text)

    def test_tool_call_id_preserved(self):
        tc = _make_tool_call_mock("call_abc", "run_shell", {"cmd": "ls"})
        resp = _make_completion_response(tool_calls=[tc])
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "x"}]))
        self.assertEqual(result.tool_calls[0].id, "call_abc")

    def test_tool_call_name_preserved(self):
        tc = _make_tool_call_mock("id1", "write_file", {"path": "/tmp/x", "content": "y"})
        resp = _make_completion_response(tool_calls=[tc])
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "x"}]))
        self.assertEqual(result.tool_calls[0].name, "write_file")

    def test_tool_call_args_parsed(self):
        tc = _make_tool_call_mock("id1", "read_file", {"path": "/etc/hosts"})
        resp = _make_completion_response(tool_calls=[tc])
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "x"}]))
        self.assertEqual(result.tool_calls[0].args, {"path": "/etc/hosts"})

    def test_invalid_json_args_become_empty_dict(self):
        fn = MagicMock()
        fn.name = "bad_tool"
        fn.arguments = "not json {"
        tc = MagicMock()
        tc.id = "id1"
        tc.function = fn
        resp = _make_completion_response(tool_calls=[tc])
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "x"}]))
        self.assertEqual(result.tool_calls[0].args, {})

    def test_multiple_tool_calls(self):
        tc1 = _make_tool_call_mock("c1", "read_file", {"path": "/a"})
        tc2 = _make_tool_call_mock("c2", "read_file", {"path": "/b"})
        resp = _make_completion_response(tool_calls=[tc1, tc2])
        self._mock_complete(resp)
        result = run(self.client.complete([{"role": "user", "content": "x"}]))
        self.assertEqual(len(result.tool_calls), 2)


# ─────────────────────────────────────────────────────────────────────────────
# 5. ReAct parser
# ─────────────────────────────────────────────────────────────────────────────

class TestReActParser(unittest.TestCase):

    def test_basic_action_no_args(self):
        text = "THOUGHT: I need to list files.\nACTION: list_dir\nARGS: {}"
        result = _parse_react_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "list_dir")
        self.assertEqual(result.args, {})

    def test_action_with_args(self):
        text = 'ACTION: read_file\nARGS: {"path": "/home/user/notes.md"}'
        result = _parse_react_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "read_file")
        self.assertEqual(result.args["path"], "/home/user/notes.md")

    def test_complex_args(self):
        text = 'ACTION: write_file\nARGS: {"path": "/tmp/x.py", "content": "print(1)", "overwrite": true}'
        result = _parse_react_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.args["overwrite"], True)

    def test_case_insensitive_action(self):
        text = "action: read_file\nargs: {}"
        result = _parse_react_tool_call(text)
        self.assertIsNotNone(result)

    def test_no_action_returns_none(self):
        self.assertIsNone(_parse_react_tool_call("Just a normal reply."))

    def test_no_args_block_uses_empty_dict(self):
        text = "ACTION: list_dir"
        result = _parse_react_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.args, {})

    def test_invalid_args_json_uses_empty_dict(self):
        text = "ACTION: bad_tool\nARGS: {not valid json}"
        result = _parse_react_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.args, {})

    def test_react_id_has_tool_name(self):
        text = "ACTION: my_tool\nARGS: {}"
        result = _parse_react_tool_call(text)
        self.assertIn("my_tool", result.id)

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_react_tool_call(""))

    def test_partial_thought_no_action(self):
        self.assertIsNone(_parse_react_tool_call("THOUGHT: I should think more."))

    def test_react_parsed_by_complete(self):
        """CompletionResult is a tool call when model outputs ReAct format."""
        client = LMStudioClient(_cfg())
        react_text = 'THOUGHT: Need to read.\nACTION: read_file\nARGS: {"path": "/tmp/x"}'
        resp = _make_completion_response(content=react_text)
        client._client.chat = MagicMock()
        client._client.chat.completions = MagicMock()
        client._client.chat.completions.create = AsyncMock(return_value=resp)
        result = run(client.complete([{"role": "user", "content": "x"}]))
        self.assertTrue(result.has_tool_calls)
        self.assertEqual(result.tool_calls[0].name, "read_file")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Error handling and retries
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorHandling(unittest.TestCase):

    def setUp(self):
        # Use max_retries=1 for fast tests
        self.client = LMStudioClient(_cfg(max_retries=1))
        self.client._client.chat = MagicMock()
        self.client._client.chat.completions = MagicMock()

    def _set_side_effect(self, exc):
        self.client._client.chat.completions.create = AsyncMock(side_effect=exc)

    def test_connection_error_retries_then_raises(self):
        self._set_side_effect(APIConnectionError.__new__(APIConnectionError))
        with self.assertRaises(LMStudioConnectionError):
            run(self.client.complete([{"role": "user", "content": "x"}]))

    def test_timeout_raises_immediately(self):
        from openai import APITimeoutError
        self._set_side_effect(APITimeoutError.__new__(APITimeoutError))
        with self.assertRaises(LMStudioTimeoutError):
            run(self.client.complete([{"role": "user", "content": "x"}]))

    def test_api_status_error_raises_model_error(self):
        from openai import BadRequestError
        import httpx
        # Build a real enough httpx.Response for BadRequestError.__init__
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.headers = MagicMock()
        mock_response.headers.get = MagicMock(return_value=None)
        err = BadRequestError(message="Bad request", response=mock_response, body=None)
        self.client._client.chat.completions.create = AsyncMock(side_effect=err)
        with self.assertRaises(LMStudioModelError):
            run(self.client.complete([{"role": "user", "content": "x"}]))

    def test_retry_succeeds_on_second_attempt(self):
        good_resp = _make_completion_response(content="ok")
        call_count = 0

        async def flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise APIConnectionError.__new__(APIConnectionError)
            return good_resp

        self.client._client.chat.completions.create = flaky
        result = run(self.client.complete([{"role": "user", "content": "x"}]))
        self.assertEqual(result.text, "ok")
        self.assertEqual(call_count, 2)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Streaming
# ─────────────────────────────────────────────────────────────────────────────

class TestStreaming(unittest.TestCase):

    def setUp(self):
        self.client = LMStudioClient(_cfg())

    def _mock_stream(self, chunks: list[str]):
        """Build an async mock that yields text chunks."""
        async def _aiter():
            for text in chunks:
                delta = MagicMock()
                delta.content = text
                choice = MagicMock()
                choice.delta = delta
                chunk = MagicMock()
                chunk.choices = [choice]
                yield chunk

        mock_stream = _aiter()

        async def create_fn(*args, **kwargs):
            return mock_stream

        self.client._client.chat = MagicMock()
        self.client._client.chat.completions = MagicMock()
        self.client._client.chat.completions.create = create_fn

    def test_yields_text_chunks(self):
        self._mock_stream(["Hello", ", ", "world", "!"])

        async def collect():
            chunks = []
            async for chunk in self.client.complete_stream(
                [{"role": "user", "content": "hi"}]
            ):
                chunks.append(chunk)
            return chunks

        chunks = run(collect())
        self.assertEqual(chunks, ["Hello", ", ", "world", "!"])
        self.assertEqual("".join(chunks), "Hello, world!")

    def test_empty_stream_yields_nothing(self):
        self._mock_stream([])

        async def collect():
            chunks = []
            async for chunk in self.client.complete_stream(
                [{"role": "user", "content": "hi"}]
            ):
                chunks.append(chunk)
            return chunks

        chunks = run(collect())
        self.assertEqual(chunks, [])


# ─────────────────────────────────────────────────────────────────────────────
# 8. Singleton and sync wrappers
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleton(unittest.TestCase):

    def test_get_client_returns_lm_studio_client(self):
        reset_client()
        client = get_client()
        self.assertIsInstance(client, LMStudioClient)

    def test_get_client_same_instance_twice(self):
        reset_client()
        c1 = get_client()
        c2 = get_client()
        self.assertIs(c1, c2)

    def test_reset_client_forces_new_instance(self):
        reset_client()
        c1 = get_client()
        reset_client()
        c2 = get_client()
        self.assertIsNot(c1, c2)

    def test_get_client_with_custom_cfg(self):
        reset_client()
        custom = _cfg(primary_model="custom-model")
        client = get_client(cfg=custom)
        self.assertEqual(client._cfg.primary_model, "custom-model")
        # Singleton not affected
        singleton = get_client()
        self.assertIsNot(client, singleton)


# ─────────────────────────────────────────────────────────────────────────────
# 9. CompletionResult and ConnectionStatus dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────

class TestDataclassInvariants(unittest.TestCase):

    def test_has_text_false_when_none(self):
        r = CompletionResult(text=None)
        self.assertFalse(r.has_text)

    def test_has_text_false_when_whitespace(self):
        r = CompletionResult(text="   ")
        self.assertFalse(r.has_text)

    def test_has_text_true_when_content(self):
        r = CompletionResult(text="Hello")
        self.assertTrue(r.has_text)

    def test_has_tool_calls_false_when_empty(self):
        r = CompletionResult()
        self.assertFalse(r.has_tool_calls)

    def test_has_tool_calls_true_when_populated(self):
        tc = ToolCall(id="x", name="y", args={}, raw_args="{}")
        r = CompletionResult(tool_calls=[tc])
        self.assertTrue(r.has_tool_calls)

    def test_connection_status_default_not_reachable(self):
        s = ConnectionStatus(reachable=False)
        self.assertFalse(s.reachable)
        self.assertEqual(s.models, [])
        self.assertIsNone(s.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
