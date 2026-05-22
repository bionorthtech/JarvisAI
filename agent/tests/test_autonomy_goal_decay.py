"""
D5 — standing goal decay test.

Adding a goal records metadata; reinforce_goal resets the clock;
goals untouched ≥30 days appear `is_stale` and publish `goal.stale`
events (throttled to one per 24h per goal).
"""
import time
import unittest

from agent.core import bus
from agent.core.autonomy import (
    AutonomyDaemon, _GOAL_STALE_AFTER_S, _GOAL_STALE_ALERT_COOLDOWN_S,
)


class TestGoalDecay(unittest.TestCase):
    def setUp(self):
        self.d = AutonomyDaemon()
        self.d._state["standing_goals"] = []
        self.d._state["goal_meta"] = {}

    def test_add_goal_records_metadata(self):
        self.d.add_goal("g1")
        meta = self.d._state["goal_meta"]
        self.assertIn("g1", meta)
        self.assertAlmostEqual(meta["g1"]["created_at"],
                               meta["g1"]["last_reinforced_at"], places=3)

    def test_goals_with_meta_shape(self):
        self.d.add_goal("g1")
        goals = self.d.goals_with_meta()
        self.assertEqual(len(goals), 1)
        g = goals[0]
        self.assertEqual(g["goal"], "g1")
        self.assertIn("age_days", g)
        self.assertIn("is_stale", g)
        self.assertFalse(g["is_stale"])

    def test_reinforce_resets_clock(self):
        self.d.add_goal("g1")
        # Backdate the goal to 40 days ago
        old_ts = time.time() - 40 * 86400
        self.d._state["goal_meta"]["g1"]["last_reinforced_at"] = old_ts
        self.assertTrue(self.d.goals_with_meta()[0]["is_stale"])

        self.d.reinforce_goal("g1")
        self.assertFalse(self.d.goals_with_meta()[0]["is_stale"])

    def test_stale_goal_publishes_event(self):
        self.d.add_goal("g_stale")
        old_ts = time.time() - (_GOAL_STALE_AFTER_S + 1000)
        self.d._state["goal_meta"]["g_stale"]["last_reinforced_at"] = old_ts
        self.d._state["goal_meta"]["g_stale"]["last_stale_alert"] = 0.0

        before = bus.recent(limit=200, topic_prefix="goal.stale")
        self.d._check_stale_goals()
        after = bus.recent(limit=200, topic_prefix="goal.stale")
        self.assertGreater(len(after), len(before),
                           "goal.stale event should be published")

    def test_stale_alert_throttled(self):
        self.d.add_goal("g_throttle")
        old_ts = time.time() - (_GOAL_STALE_AFTER_S + 1000)
        self.d._state["goal_meta"]["g_throttle"]["last_reinforced_at"] = old_ts
        # Pretend we already alerted 1 hour ago — within the 24h cooldown.
        self.d._state["goal_meta"]["g_throttle"]["last_stale_alert"] = \
            time.time() - (_GOAL_STALE_ALERT_COOLDOWN_S / 24)

        before = bus.recent(limit=200, topic_prefix="goal.stale")
        self.d._check_stale_goals()
        after = bus.recent(limit=200, topic_prefix="goal.stale")
        self.assertEqual(len(after), len(before),
                         "Stale alert should be throttled within 24h cooldown")

    def test_remove_clears_metadata(self):
        self.d.add_goal("g_remove")
        self.assertIn("g_remove", self.d._state["goal_meta"])
        self.d.remove_goal("g_remove")
        self.assertNotIn("g_remove", self.d._state["goal_meta"])


if __name__ == "__main__":
    unittest.main()
