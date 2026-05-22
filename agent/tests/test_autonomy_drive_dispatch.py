"""
F5 — drive-derived dispatch + in-flight cap + origin=drive_auto audit tag.
"""
import asyncio
import unittest
from unittest.mock import patch

from agent.core import bus
from agent.core.autonomy import (
    AutonomyDaemon,
    _MAX_AUTONOMOUS_IN_FLIGHT,
)


class TestF5SlotMachinery(unittest.TestCase):
    def test_in_flight_cap_blocks_reservation(self):
        d = AutonomyDaemon()
        # Fill to cap
        for _ in range(_MAX_AUTONOMOUS_IN_FLIGHT):
            self.assertTrue(d._reserve_slot())
        self.assertEqual(d._in_flight, _MAX_AUTONOMOUS_IN_FLIGHT)
        # Next one denied
        self.assertFalse(d._reserve_slot())
        # Release one -> can reserve again
        d._release_slot()
        self.assertTrue(d._reserve_slot())

    def test_queue_drain_on_release(self):
        d = AutonomyDaemon()
        for _ in range(_MAX_AUTONOMOUS_IN_FLIGHT):
            d._reserve_slot()
        d._queue_dispatch({"kind": "drive", "drive": "CURIOSITY"})
        self.assertEqual(d.slots()["queued"], 1)
        d._release_slot()
        # Drain happens on release — queue should drop by one.
        self.assertEqual(d.slots()["queued"], 0)


class TestF5DriveDispatch(unittest.TestCase):
    # VIGILANCE-routed-to-sentinel test removed 2026-05-15 with the
    # security stack. Drive dispatch is now tested via CURIOSITY.

    def test_low_drive_does_not_dispatch(self):
        async def scenario():
            d = AutonomyDaemon()
            d._state["level"] = 3
            d._state["drive_last_dispatch"] = {}   # clean cooldown state
            fake = {k: 0.1 for k in ("CURIOSITY", "MAINTENANCE", "LEARNING")}
            with patch("agent.core.drives.get_state", return_value=fake):
                actions = await d._drive_dispatch()
            self.assertEqual(actions, 0)

        asyncio.run(scenario())

    def test_curiosity_drive_emits_action_with_origin(self):
        async def scenario():
            d = AutonomyDaemon()
            d._state["level"] = 3
            d._state["drive_last_dispatch"] = {}   # clean cooldown state
            fake = {"CURIOSITY": 0.95, "MAINTENANCE": 0.1, "LEARNING": 0.1}
            fake_gen = {"items": [{"id": "abc", "topic": "asyncio TaskGroup"}]}

            with patch("agent.core.drives.get_state", return_value=fake), \
                 patch("agent.core.drives.bump"), \
                 patch("agent.core.curiosity.generate", return_value=fake_gen):
                await d._drive_dispatch()

            events = bus.recent(limit=20, topic_prefix="curiosity.action.run")
            self.assertTrue(events)
            self.assertEqual(events[0].get("origin"), "drive_auto")
            self.assertEqual(events[0].get("topic"), "asyncio TaskGroup")

        asyncio.run(scenario())

    def test_handler_respects_min_autonomy_level(self):
        """At level 1, a drive can spike but level-3 handlers (CodeAgent etc)
        must NOT fire. CURIOSITY engine requires level 2 → at level 1, drive
        spike must NOT invoke it."""
        async def scenario():
            d = AutonomyDaemon()
            d._state["level"] = 1  # only level-1 handlers eligible
            fake = {"CURIOSITY": 0.95, "MAINTENANCE": 0.1, "LEARNING": 0.1}
            with patch("agent.core.drives.get_state", return_value=fake), \
                 patch("agent.core.drives.bump"), \
                 patch("agent.core.curiosity.generate") as gen_mock:
                await d._drive_dispatch()
            # curiosity engine requires level 2 — must not have been called
            gen_mock.assert_not_called()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
