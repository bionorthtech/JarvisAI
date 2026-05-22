"""
B6.6 follow-up — ResponseStyle tests.

These don't hit the LM. They verify compute() reads personality_traits
correctly and applies the right thresholds.
"""
import unittest
from unittest import mock

from agent.core import response_style as rs


class TestResponseStyle(unittest.TestCase):
    def test_empty_snapshot_is_neutral(self):
        with mock.patch.object(rs, "_safe_snapshot", return_value={}):
            r = rs.compute()
        self.assertEqual(r.system_suffix, "")
        self.assertIsNone(r.max_tokens_hint)
        self.assertFalse(r.prefer_code_first)

    def test_hard_terse_caps_max_tokens(self):
        snap = {"comm_style": {"terse_pct": 85.0, "verbose_pct": 0.0}}
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertEqual(r.max_tokens_hint, 600)
        self.assertIn("terse", r.system_suffix.lower())

    def test_soft_terse_loosens_cap(self):
        snap = {"comm_style": {"terse_pct": 35.0, "verbose_pct": 5.0}}
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertEqual(r.max_tokens_hint, 1200)
        self.assertIn("terse", r.system_suffix.lower())

    def test_verbose_user_no_cap(self):
        snap = {"comm_style": {"terse_pct": 5.0, "verbose_pct": 70.0}}
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertIsNone(r.max_tokens_hint)
        self.assertIn("verbose", r.system_suffix.lower())

    def test_code_first_flips_on_tool_invocations(self):
        snap = {
            "comm_style": {"terse_pct": 10.0, "verbose_pct": 10.0},
            "tool_preferences": {
                "run_shell":   {"invocations": 3, "completions": 3, "cuts": 0},
                "write_file":  {"invocations": 4, "completions": 4, "cuts": 0},
            },
        }
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertTrue(r.prefer_code_first)
        self.assertIn("code-first", r.system_suffix.lower())

    def test_code_first_does_not_fire_on_low_use(self):
        snap = {
            "comm_style": {"terse_pct": 10.0, "verbose_pct": 10.0},
            "tool_preferences": {
                "run_shell": {"invocations": 1, "completions": 1, "cuts": 0},
            },
        }
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertFalse(r.prefer_code_first)

    # ── C14.2 tone + preferences ───────────────────────────────────────────

    def test_emoji_dense_tone_adds_hint(self):
        snap = {"tone": {"emoji_density": 0.8}}
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertIn("emoji", r.system_suffix.lower())

    def test_caps_heavy_tone_adds_hint(self):
        snap = {"tone": {"caps_pct": 45.0}}
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertIn("caps", r.system_suffix.lower())

    def test_preferences_injected_as_standing_section(self):
        snap = {
            "preferences": [
                "Use snake_case for Python identifiers.",
                "Skip the preamble when answering.",
            ],
        }
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertIn("Standing user preferences", r.system_suffix)
        self.assertIn("snake_case", r.system_suffix)

    def test_empty_preferences_no_section(self):
        snap = {"preferences": []}
        with mock.patch.object(rs, "_safe_snapshot", return_value=snap):
            r = rs.compute()
        self.assertNotIn("Standing user preferences", r.system_suffix)


if __name__ == "__main__":
    unittest.main()
