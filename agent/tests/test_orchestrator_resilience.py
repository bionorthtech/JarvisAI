"""1B.1 follow-up — orchestrator resilience tests.

Covers the critic-found bugs:
  - permanent registry-pin on init failure → retry next cycle
  - in_flight flag persistence → in-memory only, survives restart
"""
import time
import unittest
from unittest.mock import patch

from agent.core import autonomy as auto_mod


class TestRegistryRetryOnInitFailure(unittest.IsolatedAsyncioTestCase):
    def _mk_daemon(self):
        d = auto_mod.AutonomyDaemon()
        d._state["level"] = 2
        d._state["periodic_last_run"] = {}
        d._in_flight = 0
        d._periodic_registry = None
        d._last_registry_build_attempt = 0.0
        d._periodic_in_flight = None
        return d

    async def test_init_failure_does_not_permanently_pin_to_empty(self):
        d = self._mk_daemon()
        # First attempt fails — we expect _periodic_registry to remain
        # None (not pinned to []) so the retry path can run.
        with patch.object(auto_mod, "_build_periodic_registry",
                          side_effect=ImportError("transient")):
            n = await d._run_periodic_registry()
        self.assertEqual(n, 0)
        self.assertIsNone(getattr(d, "_periodic_registry", None))
        # Second attempt — succeeds. We need to fast-forward the
        # cooldown timer; force _last_registry_build_attempt to "long ago".
        d._last_registry_build_attempt = time.time() - 120
        with patch.object(auto_mod, "_build_periodic_registry",
                          return_value=[]):
            await d._run_periodic_registry()
        # On success it lands as []; that's also "no work" but the
        # attribute should not be None anymore.
        self.assertIsNotNone(getattr(d, "_periodic_registry", None))

    async def test_in_flight_map_is_in_memory_only(self):
        """Critic 1B.1-#2: persisting in_flight to disk meant a crash
        mid-handler left the entry permanently flagged."""
        d = self._mk_daemon()
        d._periodic_registry = []
        await d._run_periodic_registry()
        # in_flight initialized to a fresh dict on the instance, NOT
        # in self._state (which is what persists).
        self.assertIsInstance(d._periodic_in_flight, dict)
        self.assertNotIn("periodic_in_flight", d._state)


class TestPersonalityTraitsAtomicSave(unittest.TestCase):
    """Critic 1C.1-#3: _save now writes through a .tmp + rename."""
    def test_atomic_save_writes_via_tmp(self):
        import tempfile, json
        from pathlib import Path
        from agent.core import personality_traits as pt
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "p.json"
            with patch.object(pt, "_STATE_FILE", target):
                pt._save({"x": 1})
                # File exists, content correct.
                self.assertEqual(json.loads(target.read_text())["x"], 1)
                # tmp file should not linger after rename.
                self.assertFalse((target.with_suffix(".json.tmp")).exists())


class TestLastTurnEvictionCap(unittest.TestCase):
    """Critic 1C.1-#5: unbounded _last_turn dict."""
    def test_eviction_caps_at_128(self):
        from agent.core import gateway as gw
        # Snapshot + clear so the test is hermetic.
        snapshot = dict(gw._last_turn)
        gw._last_turn.clear()
        try:
            for i in range(200):
                gw._last_turn[f"s-{i}"] = {"turn_id": "x", "prompt": "y", "ts": 0.0}
                gw._evict_last_turn_overflow()
            self.assertEqual(len(gw._last_turn), gw._LAST_TURN_CAP)
            # Oldest entries (s-0 ... s-71) should be gone; newest survive.
            self.assertNotIn("s-0", gw._last_turn)
            self.assertIn("s-199", gw._last_turn)
        finally:
            gw._last_turn.clear()
            gw._last_turn.update(snapshot)


if __name__ == "__main__":
    unittest.main()
