"""1A.2 — curiosity.apply_reflection closed-loop test.

Verifies that when a reflection.recorded event arrives with a
curiosity_id, the matching open candidate gets its `outcome`
populated and state flipped to "acted".
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core import curiosity as cur


class TestApplyReflection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "curiosity.jsonl"
        # Redirect the persistence file.
        self._patch = patch.object(cur, "_STORE", self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def _seed_candidate(self, cand_id: str = "cand-test-1"):
        """Direct-write a single open candidate. _load_all reads from
        the JSONL store."""
        import json, time
        line = json.dumps({
            "id": cand_id,
            "topic": "tcp.slow_start",
            "category": "Network",
            "why_now": "test",
            "source": "test",
            "difficulty": "quick",
            "proposed_action": "test",
            "created_ts": time.time(),
            "state": "open",
            "acted_ts": None,
            "outcome": None,
        })
        self.path.write_text(line + "\n")

    def test_apply_reflection_populates_outcome_and_flips_state(self):
        self._seed_candidate("cand-test-1")
        ok = cur.apply_reflection({
            "curiosity_id": "cand-test-1",
            "verdict": "solved",
            "lesson": "Next time, just read the RFC first.",
        })
        self.assertTrue(ok)
        cands = cur._load_all()
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual(c.state, "acted")
        self.assertIn("solved", c.outcome or "")
        self.assertIn("RFC", c.outcome or "")

    def test_no_curiosity_id_returns_false(self):
        self._seed_candidate("cand-test-1")
        ok = cur.apply_reflection({"verdict": "solved", "lesson": "x"})
        self.assertFalse(ok)

    def test_unknown_id_returns_false_without_mutation(self):
        self._seed_candidate("cand-test-1")
        ok = cur.apply_reflection({
            "curiosity_id": "cand-not-real",
            "verdict": "solved", "lesson": "x",
        })
        self.assertFalse(ok)
        cands = cur._load_all()
        self.assertEqual(cands[0].state, "open")
        self.assertIsNone(cands[0].outcome)

    def test_already_acted_candidate_not_touched(self):
        self._seed_candidate("cand-test-1")
        # Pre-act it.
        cur.act("cand-test-1", outcome="manual")
        ok = cur.apply_reflection({
            "curiosity_id": "cand-test-1",
            "verdict": "solved", "lesson": "x",
        })
        # Already in acted state — apply_reflection only matches open.
        self.assertFalse(ok)
        cands = cur._load_all()
        self.assertEqual(cands[0].outcome, "manual")  # untouched

    def test_tag_prompt_prepends_header(self):
        out = cur.tag_prompt("research tcp slow start", "cand-xyz")
        self.assertTrue(out.startswith("[CURIOSITY:cand-xyz]\n"))
        self.assertIn("research tcp slow start", out)

    def test_tag_prompt_empty_id_returns_prompt_unchanged(self):
        out = cur.tag_prompt("hello", "")
        self.assertEqual(out, "hello")


if __name__ == "__main__":
    unittest.main()
