"""
D7 — Personality card discovery + cache shape tests.

These don't hit the LM — they verify that bot discovery walks
agent/bots/ correctly and that the cache round-trips through the
JSON file.
"""
import time
import unittest
from unittest import mock

from agent.core import personality_cards as pc


class TestPersonalityCards(unittest.TestCase):
    # PROBE_BOT: a non-security bot known to live in agent/bots/. Used as
    # the fixture for cache-staleness tests. Pick a bot whose module won't
    # be deleted by another cut.
    PROBE_BOT = "code_health"

    def test_discover_finds_each_bot_module(self):
        bots = pc._discover_bots()
        ids = {b["id"] for b in bots}
        # Sanity: known non-security bots are present.
        self.assertIn("memory_gardener", ids)
        self.assertIn("code_health", ids)
        self.assertIn("performance_watchdog", ids)
        # Security bots removed 2026-05-15 — must NOT be discovered.
        for ex in ("sentinel", "clamav_scanner", "deep_scanner",
                   "attack_tagger", "egress_audit"):
            self.assertNotIn(ex, ids, f"removed bot {ex} should be gone")
        # No private modules.
        self.assertFalse(any(b["id"].startswith("_") for b in bots))
        # Each has class_name + docstring + module_mtime.
        for b in bots:
            self.assertIn("class_name", b)
            self.assertIn("docstring", b)
            self.assertIn("module_mtime", b)

    def test_all_cards_stub_for_missing(self):
        # Force-empty the cache and confirm every bot still appears as a
        # stale stub rather than disappearing from the list.
        with mock.patch.object(pc, "_load_cache", return_value={}):
            cards = pc.all_cards()
        self.assertGreater(len(cards), 0)
        for c in cards:
            self.assertTrue(c["stale"])
            self.assertIsNone(c["generated_at"])
            self.assertIn("no card yet", c["text"])

    def test_all_cards_marks_stale_after_refresh_window(self):
        bots = pc._discover_bots()
        probe = next(b for b in bots if b["id"] == self.PROBE_BOT)
        old_ts = time.time() - (pc._REFRESH_AFTER_S + 1000)
        fake_cache = {
            self.PROBE_BOT: {
                "text": "I watch.", "generated_at": old_ts,
                "module_mtime": probe["module_mtime"],
                "class_name": probe["class_name"],
            },
        }
        with mock.patch.object(pc, "_load_cache", return_value=fake_cache):
            cards = pc.all_cards()
        c = next(x for x in cards if x["id"] == self.PROBE_BOT)
        self.assertTrue(c["stale"], "Old card should be marked stale")

    def test_all_cards_marks_stale_on_module_change(self):
        bots = pc._discover_bots()
        probe = next(b for b in bots if b["id"] == self.PROBE_BOT)
        # Recorded mtime is older than the bot's actual file mtime →
        # module has changed since the card was made.
        fake_cache = {
            self.PROBE_BOT: {
                "text": "I watch.", "generated_at": time.time(),
                "module_mtime": probe["module_mtime"] - 10_000,
                "class_name": probe["class_name"],
            },
        }
        with mock.patch.object(pc, "_load_cache", return_value=fake_cache):
            cards = pc.all_cards()
        c = next(x for x in cards if x["id"] == self.PROBE_BOT)
        self.assertTrue(c["stale"], "Card should be stale when module mtime is newer")


if __name__ == "__main__":
    unittest.main()
