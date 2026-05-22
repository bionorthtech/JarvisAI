"""
C14.2 — Honcho user-model tests.

Covers the new terminology + tone samplers (stat-based, no LM).
preferences distillation is LM-dependent; we just verify the dry path
behaves (returns None) when there's not enough signal.
"""
import unittest
from unittest import mock

from agent.core import personality_traits as pt


class TestTerminology(unittest.TestCase):
    def test_empty_input(self):
        out = pt._sample_terminology([])
        self.assertEqual(len(out), 0)

    def test_filters_singletons(self):
        # 'hello world' appears once — should NOT be in output.
        out = pt._sample_terminology(["hello world is a test phrase"])
        # No bigram repeats here → empty.
        self.assertEqual(len(out), 0)

    def test_repeated_bigrams_surface(self):
        bodies = [
            "I love python typing decorators",
            "python typing is great",
            "python typing again",
        ]
        out = pt._sample_terminology(bodies)
        keys = list(out.keys())
        self.assertIn("python typing", keys)


class TestTone(unittest.TestCase):
    def test_empty_returns_zeros(self):
        t = pt._sample_tone([])
        self.assertEqual(t["messages_seen"], 0)
        self.assertEqual(t["emoji_density"], 0.0)

    def test_emoji_density(self):
        t = pt._sample_tone(["hi 🚀", "yes 🚀🚀", "no emoji"])
        # 3 emoji / 3 messages = 1.0
        self.assertAlmostEqual(t["emoji_density"], 1.0)

    def test_exclaim_density(self):
        t = pt._sample_tone(["wow!!", "yes!", "no"])
        # 3 exclaims / 3 messages = 1.0
        self.assertAlmostEqual(t["exclaim_density"], 1.0)

    def test_caps_pct(self):
        # Two short messages; one with ALL CAPS, one mixed.
        t = pt._sample_tone(["URGENT FIX NOW", "regular sentence"])
        self.assertEqual(t["caps_pct"], 50.0)

    def test_question_pct(self):
        t = pt._sample_tone(["does this work?", "ok then.", "and this?"])
        self.assertAlmostEqual(t["question_pct"], 66.7, places=1)


class TestPreferencesDryPath(unittest.TestCase):
    def test_too_few_bodies_returns_none(self):
        # <8 bodies → skip LM call entirely.
        self.assertIsNone(pt._distill_preferences(["one", "two", "three"]))

    def test_lm_failure_returns_none(self):
        # Patch the LM import path to raise; function must swallow.
        bodies = [f"chat turn {i}" for i in range(12)]
        with mock.patch("agent.core.lm_studio.get_client",
                        side_effect=RuntimeError("LM down")):
            self.assertIsNone(pt._distill_preferences(bodies))


if __name__ == "__main__":
    unittest.main()
