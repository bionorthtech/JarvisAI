"""
C15.1 — FIM completer tests.

Only covers the pure-Python paths (trim + context bounding); the
actual LM call is exercised live, not mocked here.
"""
import unittest

from agent.core import fim_completer as fim


class TestTrim(unittest.TestCase):
    def test_strips_stop_sequence(self):
        self.assertEqual(
            fim._trim("def foo():\n    return 1<|fim_pad|>extra"),
            "def foo():\n    return 1",
        )

    def test_strips_boilerplate_preamble(self):
        self.assertEqual(
            fim._trim("Here is the middle:\nactual = 1"),
            "actual = 1",
        )

    def test_no_op_clean_output(self):
        self.assertEqual(fim._trim("clean code"), "clean code")

    def test_strips_im_end(self):
        self.assertEqual(fim._trim("body<|im_end|>"), "body")


class TestComplete(unittest.TestCase):
    def test_context_bounded_to_4k(self):
        # Patch the async call to capture the prompt without hitting the LM.
        captured: dict = {}
        async def fake(prompt, max_tokens, model):
            captured["prompt"] = prompt
            captured["max_tokens"] = max_tokens
            return {"ok": True, "text": "X", "model": "fake", "latency_ms": 1}
        from unittest import mock
        with mock.patch.object(fim, "_acomplete", fake):
            r = fim.complete(prefix="A" * 6000, suffix="B" * 6000, max_tokens=80)
        self.assertTrue(r["ok"])
        # Prefix kept the tail 4 KB; suffix kept the head 4 KB.
        self.assertEqual(r["prefix_bytes"], 4000)
        self.assertEqual(r["suffix_bytes"], 4000)

    def test_max_tokens_clamped(self):
        captured: dict = {}
        async def fake(prompt, max_tokens, model):
            captured["max_tokens"] = max_tokens
            return {"ok": True, "text": "x", "model": "fake", "latency_ms": 1}
        from unittest import mock
        with mock.patch.object(fim, "_acomplete", fake):
            fim.complete(prefix="x", suffix="y", max_tokens=9999)
            self.assertEqual(captured["max_tokens"], 256)
            fim.complete(prefix="x", suffix="y", max_tokens=2)
            self.assertEqual(captured["max_tokens"], 16)

    def test_lm_failure_returns_ok_false(self):
        async def fake(prompt, max_tokens, model):
            return {"ok": False, "error": "boom", "latency_ms": 10}
        from unittest import mock
        with mock.patch.object(fim, "_acomplete", fake):
            r = fim.complete(prefix="x", suffix="y")
        self.assertFalse(r["ok"])
        self.assertEqual(r["completion"], "")
        self.assertIn("boom", r["error"])


if __name__ == "__main__":
    unittest.main()
