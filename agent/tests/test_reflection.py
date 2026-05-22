"""1A — reflection module tests.

Parse robustness, kill switch, timeout, store rotation.
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent.core import reflection


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.iscoroutine(coro) else asyncio.new_event_loop().run_until_complete(coro)


class _FakeCompletion:
    def __init__(self, text: str):
        self.text = text


class TestParse(unittest.TestCase):
    def test_happy_path(self):
        text = (
            "SCORE: 0.85\n"
            "VERDICT: solved\n"
            "LESSON: Next time, search before scanning the whole tree."
        )
        score, verdict, lesson = reflection._parse(text)
        self.assertAlmostEqual(score, 0.85)
        self.assertEqual(verdict, "solved")
        self.assertIn("Next time", lesson)

    def test_clamps_overshoot_score(self):
        """LM occasionally returns scores above 1.0 — clamp to 1.0."""
        text = "SCORE: 1.5\nVERDICT: solved\nLESSON: x"
        score, _, _ = reflection._parse(text)
        self.assertEqual(score, 1.0)

    def test_negative_score_rejected(self):
        """Negative score is malformed output; treat as parse failure."""
        text = "SCORE: -0.4\nVERDICT: solved\nLESSON: x"
        out = reflection._parse(text)
        self.assertEqual(out, (None, None, None))

    def test_lowercase_verdict_accepted(self):
        text = "SCORE: 0.5\nverdict: PARTIAL\nLESSON: x"
        _, verdict, _ = reflection._parse(text)
        self.assertEqual(verdict, "partial")

    def test_bad_verdict_rejected(self):
        text = "SCORE: 0.5\nVERDICT: meh\nLESSON: x"
        out = reflection._parse(text)
        self.assertEqual(out, (None, None, None))

    def test_missing_field_rejected(self):
        for text in (
            "SCORE: 0.5\nVERDICT: solved",
            "SCORE: 0.5\nLESSON: x",
            "VERDICT: solved\nLESSON: x",
            "",
            "completely unrelated output",
        ):
            out = reflection._parse(text)
            self.assertEqual(out, (None, None, None), f"should reject: {text!r}")

    def test_strips_quotes_and_truncates_lesson(self):
        text = f'SCORE: 0.5\nVERDICT: partial\nLESSON: "{"a" * 400}"'
        _, _, lesson = reflection._parse(text)
        self.assertEqual(len(lesson), 200)
        self.assertFalse(lesson.startswith('"'))


class TestEnabled(unittest.TestCase):
    def test_default_enabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_REFLECTION_ENABLED", None)
            self.assertTrue(reflection.enabled())

    def test_explicit_disable(self):
        for v in ("0", "false", "no"):
            with patch.dict(os.environ, {"JARVIS_REFLECTION_ENABLED": v}):
                self.assertFalse(reflection.enabled(), f"value {v!r} should disable")


class TestReflectKillSwitch(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_returns_none_without_lm_call(self):
        client = AsyncMock()
        with patch.dict(os.environ, {"JARVIS_REFLECTION_ENABLED": "0"}):
            r = await reflection.reflect(
                "do thing", "did thing",
                client=client, model="x",
            )
        self.assertIsNone(r)
        client.complete.assert_not_called()


class TestReflectTimeout(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_returns_none(self):
        async def slow_complete(*a, **kw):
            await asyncio.sleep(2.0)
            return _FakeCompletion("SCORE: 0.5\nVERDICT: partial\nLESSON: x")

        client = AsyncMock()
        client.complete = slow_complete

        with patch.dict(os.environ, {"JARVIS_REFLECTION_TIMEOUT_S": "0.1"}):
            r = await reflection.reflect(
                "p", "r",
                client=client,
            )
        self.assertIsNone(r)


class TestCuriosityTagExtraction(unittest.TestCase):
    """1A.2 — verify the [CURIOSITY:<id>] tag round-trips through the
    reflection module so curiosity.apply_reflection can correlate."""

    def test_tag_extracted_from_prompt_header(self):
        client = AsyncMock()
        client.complete = AsyncMock(return_value=_FakeCompletion(
            "SCORE: 0.85\nVERDICT: solved\nLESSON: Next time check the cache."
        ))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reflections.jsonl"
            with patch.object(reflection, "_STORE_PATH", path):
                r = asyncio.new_event_loop().run_until_complete(
                    reflection.reflect(
                        "[CURIOSITY:cand-abc-123]\nresearch tcp slow start",
                        "explained tcp slow start in detail",
                        client=client,
                    )
                )
            self.assertIsNotNone(r)
            self.assertEqual(r.curiosity_id, "cand-abc-123")

    def test_no_tag_means_curiosity_id_is_none(self):
        client = AsyncMock()
        client.complete = AsyncMock(return_value=_FakeCompletion(
            "SCORE: 0.85\nVERDICT: solved\nLESSON: x"
        ))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reflections.jsonl"
            with patch.object(reflection, "_STORE_PATH", path):
                r = asyncio.new_event_loop().run_until_complete(
                    reflection.reflect("just a normal prompt", "response",
                                       client=client)
                )
            self.assertIsNotNone(r)
            self.assertIsNone(r.curiosity_id)


class TestReflectSuccessAndStore(unittest.IsolatedAsyncioTestCase):
    async def test_success_writes_jsonl_and_publishes_bus(self):
        client = AsyncMock()
        client.complete = AsyncMock(return_value=_FakeCompletion(
            "SCORE: 0.92\nVERDICT: solved\nLESSON: Next time check the cache first."
        ))

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reflections.jsonl"
            with patch.object(reflection, "_STORE_PATH", path):
                # We verify _append wrote the JSONL and reflect returned a
                # Reflection. The bus.publish side effect is asserted in
                # test_bus_taxonomy (reflection.recorded is documented).
                r = await reflection.reflect(
                    "build a thing", "built the thing",
                    tool_summary={"read_file": 2, "write_file": 1},
                    client=client,
                )

                self.assertIsNotNone(r)
                self.assertEqual(r.verdict, "solved")
                self.assertEqual(r.tool_count, 3)
                self.assertTrue(path.exists())
                content = path.read_text().strip().splitlines()
                self.assertEqual(len(content), 1)


if __name__ == "__main__":
    unittest.main()
