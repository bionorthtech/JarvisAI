"""1C — style_learner tests.

Covers the load-bearing pieces: retry detection (Jaccard + window),
signal idempotency, daily distillation, decay/cap, anchor invariant.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core import style_learner as sl


class TestRetryDetection(unittest.TestCase):
    def test_close_in_time_and_similar_is_retry(self):
        # Near-identical prompts a few seconds apart — classic retry.
        a = "explain the gateway pipeline in detail please"
        b = "explain the gateway pipeline in detail again"
        self.assertTrue(sl.is_substantive_retry(a, 1000.0, b, 1010.0))

    def test_loose_rephrasing_not_a_retry(self):
        """Threshold is conservative on purpose — rephrasing with many
        new words shouldn't trigger a negative signal."""
        a = "explain the gateway pipeline in detail"
        b = "explain the gateway pipeline please"
        self.assertFalse(sl.is_substantive_retry(a, 1000.0, b, 1010.0))

    def test_outside_window_not_retry(self):
        a = "explain the gateway pipeline"
        b = "explain the gateway pipeline"
        self.assertFalse(sl.is_substantive_retry(a, 1000.0, b, 1100.0))

    def test_dissimilar_not_retry(self):
        a = "what is a tcp window"
        b = "list the bots running"
        self.assertFalse(sl.is_substantive_retry(a, 1000.0, b, 1010.0))

    def test_empty_prompts_not_retry(self):
        self.assertFalse(sl.is_substantive_retry("", 1000.0, "x", 1010.0))

    def test_pasted_stack_trace_not_retry(self):
        """Critic 1C.1-#4: pasting the same trace twice would
        previously trigger a phantom retry. Multi-line / long pastes
        excluded from retry detection."""
        trace = (
            "Traceback (most recent call last):\n"
            "  File \"a.py\", line 1, in <module>\n"
            "    x()\n"
            "  File \"a.py\", line 5, in x\n"
            "    raise ValueError\n"
            "ValueError\n"
            "during handling of the above, another exception:\n"
        )
        self.assertFalse(sl.is_substantive_retry(trace, 1000.0, trace, 1005.0))

    def test_very_long_paste_not_retry(self):
        """A long single-line paste also shouldn't trigger retry."""
        blob = "word " * 400
        self.assertFalse(sl.is_substantive_retry(blob, 1000.0, blob, 1005.0))


class TestRecordSignal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "signals.jsonl"
        self._p = patch.object(sl, "_SIGNALS_PATH", self.path)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self.tmp.cleanup()

    def test_writes_known_kind(self):
        ok = sl.record_signal("t-1", "retry")
        self.assertTrue(ok)
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        d = json.loads(lines[0])
        self.assertEqual(d["kind"], "retry")
        self.assertEqual(d["turn_id"], "t-1")

    def test_rejects_unknown_kind(self):
        ok = sl.record_signal("t-1", "frobnicate")
        self.assertFalse(ok)
        self.assertFalse(self.path.exists())

    def test_dedup_same_turn_and_kind(self):
        self.assertTrue(sl.record_signal("t-1", "copied"))
        self.assertFalse(sl.record_signal("t-1", "copied"))
        # But a different kind on the same turn is fine.
        self.assertTrue(sl.record_signal("t-1", "stop"))


class TestDistillDaily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sig_path = Path(self.tmp.name) / "signals.jsonl"
        self.pt_path = Path(self.tmp.name) / "personality.json"
        self._p1 = patch.object(sl, "_SIGNALS_PATH", self.sig_path)
        self._p1.start()
        from agent.core import personality_traits as pt
        self._p2 = patch.object(pt, "_STATE_FILE", self.pt_path)
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        self.tmp.cleanup()

    def _write_signal(self, kind: str, *, terse_pct: float = 0.5,
                      ts: float | None = None):
        entry = {
            "turn_id": f"t-{time.time_ns()}",
            "ts": ts if ts is not None else time.time(),
            "kind": kind,
            "snapshot": {"comm_style": {"terse_pct": terse_pct}},
        }
        with self.sig_path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def test_adds_adjustment_after_repeated_retries(self):
        # 5 retries with terse_pct=0.5 → axis=terseness, negative delta.
        for _ in range(5):
            self._write_signal("retry")
        out = sl.distill_daily()
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["active_count"], 1)

        from agent.core import personality_traits as pt
        adj = pt._load().get("adjustments", [])
        terse = [a for a in adj if a["axis"] == "terseness"]
        self.assertEqual(len(terse), 1)
        self.assertLess(terse[0]["delta"], 0)
        self.assertIn("Lean slightly more terse",
                      terse[0]["hint"])

    def test_cap_enforced_at_12(self):
        # Pre-seed 15 fake adjustments.
        from agent.core import personality_traits as pt
        state = pt._load()
        now = time.time()
        state["adjustments"] = [
            {"axis": f"a{i}", "delta": 0.1, "confidence": 0.5,
             "evidence_count": 2, "last_updated": now, "hint": f"x{i}"}
            for i in range(15)
        ]
        pt._save(state)

        # No new signals — but distill should still enforce the cap.
        sl.distill_daily()
        adj = pt._load().get("adjustments", [])
        self.assertLessEqual(len(adj), 12)

    def test_decay_drops_old_low_confidence(self):
        from agent.core import personality_traits as pt
        state = pt._load()
        old_ts = time.time() - 30 * 86400  # 30 days ago
        state["adjustments"] = [
            {"axis": "ancient", "delta": -0.1, "confidence": 0.3,
             "evidence_count": 1, "last_updated": old_ts, "hint": "x"},
        ]
        pt._save(state)
        sl.distill_daily()
        adj = pt._load().get("adjustments", [])
        # Linear decay since 14d cutoff (16 days past) at 0.05/day → 0.3 - 16*0.05 = -0.5
        # well below _MIN_CONFIDENCE; should be dropped.
        self.assertEqual(adj, [])

    def test_reset_clears_adjustments(self):
        from agent.core import personality_traits as pt
        state = pt._load()
        state["adjustments"] = [
            {"axis": "x", "delta": 0.1, "confidence": 0.9,
             "evidence_count": 5, "last_updated": time.time(), "hint": "x"},
        ]
        pt._save(state)
        out = sl.reset_adjustments()
        self.assertTrue(out["ok"])
        self.assertEqual(pt._load().get("adjustments", []), [])


class TestResponseStyleReadsAdjustments(unittest.TestCase):
    """Anchor invariant: base suffix preserved, adjustments only extend."""

    def test_adjustment_hint_appears_in_suffix(self):
        from agent.core import response_style as rs
        from agent.core import personality_traits as pt

        fake_snap = {
            "comm_style": {"terse_pct": 0.5, "verbose_pct": 0.0,
                            "messages_seen": 50},
            "tool_preferences": {},
            "tone": {},
            "preferences": [],
            "adjustments": [{
                "axis": "terseness", "delta": -0.1, "confidence": 0.8,
                "evidence_count": 10,
                "last_updated": time.time(),
                "hint": "Lean slightly more terse than the base preference suggests.",
            }],
        }
        with patch.object(pt, "snapshot", return_value=fake_snap):
            style = rs.compute()
        self.assertIn("Learned preference: Lean slightly more terse",
                      style.system_suffix)

    def test_empty_adjustments_does_not_break_compute(self):
        from agent.core import response_style as rs
        from agent.core import personality_traits as pt

        with patch.object(pt, "snapshot", return_value={"adjustments": []}):
            style = rs.compute()
        # Should not raise; suffix may be empty.
        self.assertIsInstance(style.system_suffix, str)


if __name__ == "__main__":
    unittest.main()
