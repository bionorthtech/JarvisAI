"""Aliveness tests — emotion model, self-goals, inactivity, brain
co-ownership, personality traits."""
import time
import unittest
from unittest.mock import patch

from agent.core import autonomy as auto_mod
from agent.core.autonomy import InternalState, autonomy


# ── B6.1 emotion model ─────────────────────────────────────────────────────

class TestEmotionDecay(unittest.TestCase):
    def test_frustration_decays_slower_than_satisfaction(self):
        s = InternalState()
        s._v["FRUSTRATION"]  = 0.8
        s._v["SATISFACTION"] = 0.8
        for _ in range(20):
            s.decay()
        # After 20 ticks, satisfaction should be much closer to baseline
        # than frustration because of the 4x decay-rate difference.
        self.assertGreater(s._v["FRUSTRATION"], s._v["SATISFACTION"])

    def test_nudge_clamped_to_unit_interval(self):
        s = InternalState()
        s.nudge("CURIOSITY", +5.0)   # would exceed 1.0
        self.assertEqual(s._v["CURIOSITY"], 1.0)
        s.nudge("CURIOSITY", -5.0)   # would dip below 0.0
        self.assertEqual(s._v["CURIOSITY"], 0.0)

    def test_unknown_dim_no_op(self):
        s = InternalState()
        before = dict(s._v)
        s.nudge("NOT_A_REAL_DIM", +0.5, "should be ignored")
        self.assertEqual(s._v, before)


class TestCompoundMoods(unittest.TestCase):
    def test_flow_fires_when_focus_plus_satisfaction(self):
        s = InternalState()
        s._v["FOCUS"] = 0.7
        s._v["SATISFACTION"] = 0.7
        self.assertIn("flow", s.compound_moods())

    def test_no_compound_below_threshold(self):
        s = InternalState()
        s._v["FOCUS"] = 0.5
        s._v["SATISFACTION"] = 0.7
        self.assertEqual(s.compound_moods(), [])


class TestEventTriggers(unittest.TestCase):
    def test_agent_failed_raises_frustration(self):
        s = InternalState()
        before = s._v["FRUSTRATION"]
        applied = s.apply_event("agent.failed", {})
        self.assertEqual(applied, 1)
        self.assertGreater(s._v["FRUSTRATION"], before)

    # Sentinel-payload-gating tests removed 2026-05-15 with the security
    # stack. apply_event no longer special-cases sentinel/dependency
    # payloads.


class TestTransparency(unittest.TestCase):
    def test_transparency_returns_resettable_dims(self):
        s = InternalState()
        t = s.transparency()
        self.assertIn("dominant", t)
        self.assertIn("compound_moods", t)
        self.assertIn("top_triggers", t)
        self.assertIn("resettable", t)
        self.assertEqual(set(t["resettable"]),
                         {"CURIOSITY", "FOCUS", "FRUSTRATION", "SATISFACTION", "BOREDOM"})

    def test_reset_clears_dimension(self):
        s = InternalState()
        s._v["FRUSTRATION"] = 0.9
        s.reset("FRUSTRATION", reason="test")
        self.assertAlmostEqual(s._v["FRUSTRATION"], 0.15, places=4)


# ── B6.4 self-initiated goals ──────────────────────────────────────────────

class TestSelfGoals(unittest.TestCase):
    def setUp(self):
        # Clean slate per test so cooldowns + existing goals don't leak.
        autonomy._state["standing_goals"] = []
        autonomy._state["self_goal_last"] = {}
        autonomy._state["goal_meta"] = {}

    def test_high_boredom_generates_a_goal(self):
        # Force BOREDOM above threshold; everything else low.
        auto_mod._internal_state._v = {
            "BOREDOM": 0.85, "CURIOSITY": 0.1, "FOCUS": 0.1,
            "FRUSTRATION": 0.1, "SATISFACTION": 0.1,
        }
        from agent.core import drives
        with patch.object(drives, "get_state", return_value=None) as fake_get_state:
            fake_get_state.return_value = {
                "CURIOSITY": 0.1, "MAINTENANCE": 0.1, "LEARNING": 0.1,
            }
            generated = autonomy._generate_self_goals()
        self.assertEqual(generated, 1)
        self.assertEqual(len(autonomy._state["standing_goals"]), 1)
        # Origin tag must land in goal_meta
        goal = autonomy._state["standing_goals"][0]
        self.assertEqual(autonomy._state["goal_meta"][goal]["origin"], "drive_auto")
        self.assertEqual(autonomy._state["goal_meta"][goal]["source_dim"], "BOREDOM")

    def test_cooldown_blocks_repeat(self):
        auto_mod._internal_state._v["BOREDOM"] = 0.85
        from agent.core import drives
        with patch.object(drives, "get_state", return_value=None) as fake_get_state:
            fake_get_state.return_value = {
                "CURIOSITY": 0.0, "MAINTENANCE": 0.0, "LEARNING": 0.0,
            }
            autonomy._generate_self_goals()
            second = autonomy._generate_self_goals()
        # Same dim, same tick → cooldown blocks second generation.
        self.assertEqual(second, 0)

    def test_low_signals_generate_nothing(self):
        auto_mod._internal_state._v = {e: 0.1 for e in (
            "BOREDOM", "CURIOSITY", "FOCUS", "FRUSTRATION", "SATISFACTION")}
        from agent.core import drives
        with patch.object(drives, "get_state", return_value=None) as fake_get_state:
            fake_get_state.return_value = {
                "CURIOSITY": 0.1, "MAINTENANCE": 0.1, "LEARNING": 0.1,
            }
            self.assertEqual(autonomy._generate_self_goals(), 0)


# ── B6.7 inactivity awareness ──────────────────────────────────────────────

class TestInactivityNudge(unittest.TestCase):
    def setUp(self):
        autonomy._state["idle_nudge_last"] = 0.0

    def test_short_idle_no_nudge(self):
        autonomy._last_user_activity = time.time() - 30  # 30s — well under 20min
        fired = autonomy._inactivity_nudge()
        self.assertEqual(fired, 0)

    def test_long_idle_fires_once(self):
        autonomy._last_user_activity = time.time() - 30 * 60   # 30 min
        first = autonomy._inactivity_nudge()
        second = autonomy._inactivity_nudge()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)   # cooldown

    def test_record_user_activity_resets_clock(self):
        autonomy.record_user_activity("test")
        idle = time.time() - autonomy._last_user_activity
        self.assertLess(idle, 1.0)


# ── B6.6 personality traits ────────────────────────────────────────────────

class TestPersonalityTraits(unittest.TestCase):
    def test_snapshot_returns_expected_shape(self):
        from agent.core import personality_traits as pt
        snap = pt.snapshot()
        for k in ("topic_affinity", "working_hours", "tool_preferences",
                  "comm_style", "top_topics"):
            self.assertIn(k, snap)

    def test_refresh_idempotent_under_rate_limit(self):
        from agent.core import personality_traits as pt
        # C14.2: _distill_preferences calls live LM Studio. Mock it so
        # tests stay fast + deterministic regardless of LM availability.
        with patch.object(pt, "_distill_preferences", return_value=None):
            first = pt.refresh(force=True)
            ts1 = first["last_refresh_ts"]
            time.sleep(0.05)
            # Non-forced refresh inside the rate-limit window must NOT update ts.
            second = pt.refresh()
        self.assertEqual(second["last_refresh_ts"], ts1)


if __name__ == "__main__":
    unittest.main()
