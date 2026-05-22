"""
F7 — Sign-off criteria for Part F autonomy.

Each test simulates the real-world condition without needing the
overnight wallclock wait. Run with `pytest -q agent/tests/test_f7_signoff.py`
to get a one-shot "does Part F pass sign-off?" verdict.
"""
import asyncio
import unittest
from unittest.mock import patch, MagicMock

from agent.aliveness import notifier
from agent.core import bus
from agent.core.autonomy import AutonomyDaemon


class TestF7Signoff(unittest.TestCase):

    def test_f7_1_autonomy_2_produces_curiosity_notification_with_cta(self):
        """At autonomy=2 (proactive), a curiosity-category tick must yield a
        notification body AND a non-None CTA (B0 actionable-button mandate)."""
        # Force override to curiosity, bypass cooldown by using fresh history path
        with patch.object(notifier, "_HISTORY_PATH",
                          notifier._HISTORY_PATH.parent / "nonexistent.jsonl"), \
             patch.object(notifier, "_in_quiet_hours", return_value=False), \
             patch.object(notifier, "_lm", return_value=None):
            # _lm returns None -> fallback static body fires, still valid
            result = notifier.tick(category_override="curiosity",
                                   ignore_quiet_hours=True)
        self.assertNotIn("skipped", result, f"curiosity tick skipped: {result}")
        self.assertTrue(result.get("body"), "no body produced")
        self.assertIsNotNone(result.get("cta"),
                             "F3.4 mandate: every notification needs a CTA")

    def test_f7_2_autonomy_3_drive_dispatches_within_one_cycle(self):
        """Flipping autonomy=3 with MAINTENANCE drive at 0.95 dispatches the
        memory gardener in a single _drive_dispatch() — fires on the next
        cycle, well under the 5-minute sign-off window. (Was VIGILANCE→
        Sentinel before 2026-05-15 security cut.)"""
        async def scenario():
            d = AutonomyDaemon()
            d._state["level"] = 3
            d._state["drive_last_dispatch"] = {}
            fake_drives = {"CURIOSITY": 0.1, "MAINTENANCE": 0.95, "LEARNING": 0.1}
            gardener_mock = MagicMock()
            gardener_mock.run = MagicMock(return_value={"ok": True})

            with patch("agent.core.drives.get_state", return_value=fake_drives), \
                 patch("agent.core.drives.bump"), \
                 patch("agent.bots.memory_gardener.gardener", gardener_mock):
                actions = await d._drive_dispatch()

            self.assertGreaterEqual(actions, 1)
            gardener_mock.run.assert_called_once()

        asyncio.run(scenario())

    def test_f7_3_autonomy_zero_halts_within_five_seconds(self):
        """Direct restatement of F1.3 in the F7 sign-off suite."""
        async def scenario():
            import time
            d = AutonomyDaemon()
            d._state["cycle_interval"] = 30
            d._state["level"] = 2
            await d.start()
            t0 = time.monotonic()
            d.set_level(0)
            try:
                await asyncio.wait_for(d._task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 5.0)
            self.assertEqual(d.level, 0)
            events = bus.recent(20, "autonomy.killed")
            self.assertTrue(events)
        asyncio.run(scenario())

    def test_f7_4_quiet_hours_suppress_non_critical(self):
        """At 02:00 local, notifier.tick must skip with reason='quiet_hours'
        unless explicitly told to ignore."""
        # Force _in_quiet_hours to True (simulating overnight)
        with patch.object(notifier, "_in_quiet_hours", return_value=True), \
             patch.object(notifier, "_load_cadence", return_value={}):
            result = notifier.tick(category_override="curiosity",
                                   ignore_quiet_hours=False)
        self.assertEqual(result.get("skipped"), "quiet_hours")

        # And ignore_quiet_hours=True bypasses (sanity-check the flag works)
        with patch.object(notifier, "_in_quiet_hours", return_value=True), \
             patch.object(notifier, "_load_cadence", return_value={}), \
             patch.object(notifier, "_lm", return_value=None), \
             patch.object(notifier, "_HISTORY_PATH",
                          notifier._HISTORY_PATH.parent / "nonexistent_f74.jsonl"):
            result = notifier.tick(category_override="musing",
                                   ignore_quiet_hours=True)
        self.assertNotEqual(result.get("skipped"), "quiet_hours")


if __name__ == "__main__":
    unittest.main()
