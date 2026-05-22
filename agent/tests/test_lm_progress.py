"""G6.1 — LM Studio log tail parser + throttle."""
import unittest
from unittest.mock import patch

from agent.core import bus, lm_progress


class TestLMProgressParse(unittest.TestCase):
    def test_prompt_processing_percent(self):
        line = "[2026-05-12 00:16:49][INFO][google/gemma-4-e4b] Prompt processing progress: 73.4%"
        ev = lm_progress._parse_line(line)
        self.assertEqual(ev["phase"], "prompt_processing")
        self.assertEqual(ev["model"], "google/gemma-4-e4b")
        self.assertAlmostEqual(ev["percent"], 73.4)

    def test_thinking_start(self):
        line = "[2026-05-12 00:16:50][INFO][google/gemma-4-e4b] Start thinking..."
        ev = lm_progress._parse_line(line)
        self.assertEqual(ev["phase"], "thinking_start")

    def test_thinking_done_extracts_seconds(self):
        line = "[2026-05-12 00:16:56][INFO][google/gemma-4-e4b] Done reasoning. Reasoned for 6.13 seconds."
        ev = lm_progress._parse_line(line)
        self.assertEqual(ev["phase"], "thinking_done")
        self.assertAlmostEqual(ev["seconds"], 6.13)

    def test_slot_release_extracts_tokens_and_truncation(self):
        line = "[2026-05-12 00:36:31][DEBUG] slot      release: id  1 | task 3227 | stop processing: n_tokens = 385, truncated = 0"
        ev = lm_progress._parse_line(line)
        self.assertEqual(ev["phase"], "request_complete")
        self.assertEqual(ev["n_tokens"], 385)
        self.assertFalse(ev["truncated"])

    def test_irrelevant_lines_ignored(self):
        self.assertIsNone(lm_progress._parse_line("srv  update_slots: all slots are idle"))
        self.assertIsNone(lm_progress._parse_line("[2026-05-12][INFO] random unrelated chatter"))
        self.assertIsNone(lm_progress._parse_line(""))


class TestProgressThrottle(unittest.TestCase):
    def setUp(self):
        lm_progress._state.last_pct_announced = -100.0

    def test_progress_publish_throttled_below_step(self):
        published = []
        with patch.object(bus, "publish",
                          side_effect=lambda *a, **kw: published.append((a, kw))):
            # 0% always publishes (step from -100)
            lm_progress._publish({"phase": "prompt_processing", "percent": 0.0})
            # 1% blocked (delta < 5)
            lm_progress._publish({"phase": "prompt_processing", "percent": 1.0})
            # 5% blocked too (5 - 0 = 5, NOT < 5; >= step => publish)
            lm_progress._publish({"phase": "prompt_processing", "percent": 5.0})
            # 100% always publishes
            lm_progress._publish({"phase": "prompt_processing", "percent": 100.0})
        # Should have published 0%, 5%, and 100% (3 total)
        percents = [a[2]["percent"] for a, _ in published]
        self.assertEqual(percents, [0.0, 5.0, 100.0])

    def test_thinking_start_resets_progress_threshold(self):
        # After hitting 100%, the next prompt-processing 0% should publish.
        lm_progress._state.last_pct_announced = 100.0
        published = []
        with patch.object(bus, "publish",
                          side_effect=lambda *a, **kw: published.append((a, kw))):
            lm_progress._publish({"phase": "thinking_start"})  # resets
            lm_progress._publish({"phase": "prompt_processing", "percent": 0.0})
        self.assertEqual(len(published), 2)


if __name__ == "__main__":
    unittest.main()
