"""
F1.3 — Autonomy kill-switch test (B5 prereq).

Flipping autonomy level to 0 must provably halt the daemon loop and
publish an `autonomy.killed` bus event within 5 seconds.
"""
import asyncio
import time
import unittest

from agent.core import bus
from agent.core.autonomy import AutonomyDaemon


class TestAutonomyKillswitch(unittest.TestCase):
    def test_set_level_zero_halts_loop_and_emits_killed(self):
        async def scenario():
            d = AutonomyDaemon()
            # Drop the cycle to a short interval so the loop is sleeping
            # not running a real cycle when we kill it.
            d._state["cycle_interval"] = 30
            d._state["level"] = 2

            await d.start()
            self.assertTrue(d._running)
            self.assertIsNotNone(d._task)
            self.assertFalse(d._task.done())

            t0 = time.monotonic()
            d.set_level(0)
            # Give asyncio one tick to propagate CancelledError.
            try:
                await asyncio.wait_for(d._task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            elapsed = time.monotonic() - t0

            self.assertLess(elapsed, 5.0, "Kill-switch did not halt loop within 5s")
            self.assertFalse(d._running)
            self.assertEqual(d.level, 0)

            # autonomy.killed must have been published. bus.recent spreads
            # the JSON payload into the top-level dict alongside id/ts/topic.
            events = bus.recent(limit=20, topic_prefix="autonomy.killed")
            self.assertTrue(events, "no autonomy.killed event emitted")
            top = events[0]
            self.assertEqual(top.get("topic"), "autonomy.killed")
            self.assertEqual(top.get("previous_level"), 2)
            self.assertIn("killed_at", top)

        asyncio.run(scenario())

    def test_set_level_zero_when_not_started_still_emits(self):
        """Even with no live task, going to 0 should emit autonomy.killed."""
        async def scenario():
            d = AutonomyDaemon()
            d._state["level"] = 1
            d.set_level(0)
            events = bus.recent(limit=20, topic_prefix="autonomy.killed")
            self.assertTrue(events)
            self.assertEqual(d.level, 0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
