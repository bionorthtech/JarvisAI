"""1B.1 — periodic registry tests.

Verifies the registry honors interval gates, backpressure, no-double-
dispatch, and the kill switch.
"""
import asyncio
import os
import time
import unittest
from unittest.mock import patch

from agent.core import autonomy as auto_mod


class TestPeriodicEntryDefaults(unittest.TestCase):
    def test_registry_builds_with_drives_entry(self):
        registry = auto_mod._build_periodic_registry()
        names = {e.name for e in registry}
        self.assertIn("drives.tick", names)
        d = next(e for e in registry if e.name == "drives.tick")
        self.assertEqual(d.bp_class, "io")
        self.assertEqual(d.min_autonomy_level, 0)


class TestOrchestratorKillSwitch(unittest.TestCase):
    def test_default_enabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_ORCHESTRATOR_ENABLED", None)
            self.assertTrue(auto_mod._orchestrator_enabled())

    def test_explicit_disable(self):
        for v in ("0", "false", "no"):
            with patch.dict(os.environ, {"JARVIS_ORCHESTRATOR_ENABLED": v}):
                self.assertFalse(auto_mod._orchestrator_enabled())


class TestRunPeriodicRegistry(unittest.IsolatedAsyncioTestCase):
    """Behavior tests using a fake registry — no live LM, no real drives
    bookkeeping. Validates the gate logic in isolation."""

    def _mk_daemon(self):
        d = auto_mod.AutonomyDaemon()
        # Make sure a fresh daemon starts from blank periodic state
        # regardless of whatever's in ~/.jarvis/autonomy.json.
        d._state["periodic_last_run"] = {}
        d._state["periodic_in_flight"] = {}
        d._state["level"] = 2  # high enough for any min_level entry
        d._in_flight = 0
        return d

    async def test_due_entry_fires(self):
        fired = []

        async def _handler():
            fired.append(time.time())

        d = self._mk_daemon()
        d._periodic_registry = [auto_mod.PeriodicEntry(
            name="t.io", interval_s=0, min_autonomy_level=0,
            handler=_handler, bp_class="io",
        )]
        n = await d._run_periodic_registry()
        # _run_periodic_registry spawns tasks; await them.
        await asyncio.sleep(0.05)
        self.assertEqual(n, 1)
        self.assertEqual(len(fired), 1)

    async def test_not_due_yet_skipped(self):
        fired = []

        async def _handler():
            fired.append(1)

        d = self._mk_daemon()
        d._periodic_registry = [auto_mod.PeriodicEntry(
            name="t.io", interval_s=9999, min_autonomy_level=0,
            handler=_handler, bp_class="io",
        )]
        d._state["periodic_last_run"]["t.io"] = time.time()
        n = await d._run_periodic_registry()
        await asyncio.sleep(0.05)
        self.assertEqual(n, 0)
        self.assertEqual(fired, [])

    async def test_level_gate_blocks(self):
        fired = []

        async def _handler():
            fired.append(1)

        d = self._mk_daemon()
        d._state["level"] = 0
        d._periodic_registry = [auto_mod.PeriodicEntry(
            name="t.gated", interval_s=0, min_autonomy_level=2,
            handler=_handler, bp_class="io",
        )]
        await d._run_periodic_registry()
        await asyncio.sleep(0.05)
        self.assertEqual(fired, [])

    async def test_lm_backpressure_skips_when_in_flight(self):
        fired = []

        async def _handler():
            fired.append(1)

        d = self._mk_daemon()
        d._in_flight = 1   # simulate one autonomous LM task running
        d._periodic_registry = [auto_mod.PeriodicEntry(
            name="t.lm", interval_s=0, min_autonomy_level=0,
            handler=_handler, bp_class="lm",
        )]
        await d._run_periodic_registry()
        await asyncio.sleep(0.05)
        self.assertEqual(fired, [], "lm entry should defer under load")

    async def test_no_double_dispatch_while_in_flight(self):
        gate = asyncio.Event()
        fire_count = 0

        async def _slow_handler():
            nonlocal fire_count
            fire_count += 1
            await gate.wait()

        d = self._mk_daemon()
        d._periodic_registry = [auto_mod.PeriodicEntry(
            name="t.slow", interval_s=0, min_autonomy_level=0,
            handler=_slow_handler, bp_class="io",
        )]
        await d._run_periodic_registry()
        await asyncio.sleep(0.01)
        # Second pass — should NOT fire while first is in flight.
        await d._run_periodic_registry()
        await asyncio.sleep(0.01)
        self.assertEqual(fire_count, 1)
        gate.set()
        await asyncio.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
