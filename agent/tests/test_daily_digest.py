"""F6 — daily digest: composition, idempotence, time-gating."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime

from agent.core import bus, daily_digest

# Pre-mock the LM-paragraph call so digest tests don't hit live LM Studio
# (each call adds ~20s once LM is up).
_lm_mock = patch.object(daily_digest, "_lm_compose_paragraph",
                        return_value="Mocked digest paragraph for tests.")
_lm_mock.start()


class TestDailyDigest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name) / "daily"
        self._patch = patch.object(daily_digest, "_DIGEST_ROOT", self._root)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_compose_writes_file_with_no_events(self):
        result = daily_digest.compose()
        self.assertTrue(result["ok"])
        path = Path(result["path"])
        self.assertTrue(path.exists())
        body = path.read_text()
        self.assertIn("# Daily digest", body)
        self.assertIn("Event counts", body)

    def test_compose_is_idempotent_without_force(self):
        first = daily_digest.compose()
        second = daily_digest.compose()
        self.assertEqual(second.get("skipped"), "already_exists")
        self.assertEqual(first["path"], second["path"])

    def test_compose_force_overwrites(self):
        daily_digest.compose()
        path = Path(daily_digest._digest_path())
        mtime1 = path.stat().st_mtime
        time.sleep(0.05)
        result = daily_digest.compose(force=True)
        self.assertTrue(result["ok"])
        self.assertNotEqual(result.get("skipped"), "already_exists")
        mtime2 = path.stat().st_mtime
        self.assertGreater(mtime2, mtime1)

    def test_compose_if_due_before_target_hour_skips(self):
        # Mock datetime to 08:00 — before target 19:00.
        class FakeDT:
            @classmethod
            def now(cls):
                return datetime(2026, 5, 12, 8, 0, 0)
        with patch.object(daily_digest, "datetime", FakeDT):
            result = daily_digest.compose_if_due()
        self.assertIn("before target hour", result.get("skipped", ""))

    def test_compose_if_due_after_target_hour_composes(self):
        class FakeDT:
            @classmethod
            def now(cls):
                return datetime(2026, 5, 12, 20, 0, 0)
        with patch.object(daily_digest, "datetime", FakeDT):
            result = daily_digest.compose_if_due()
        self.assertTrue(result.get("ok"))
        self.assertTrue(Path(result["path"]).exists())

    def test_highlights_extract_from_real_event_payloads(self):
        # Seed a learning.completed event into the bus.
        bus.publish("learning.completed", "test", {
            "track_id": "python_advanced",
            "topic_name": "asyncio TaskGroup",
            "topics_done": 1, "topics_total": 8,
        })
        events = daily_digest._events_last_24h()
        learning = [e for e in events if e["topic"] == "learning.completed"]
        self.assertTrue(learning)
        highlights = daily_digest._highlight_lines(learning)
        self.assertTrue(any("asyncio TaskGroup" in h for h in highlights))
        self.assertTrue(any("python_advanced" in h for h in highlights))

    def test_today_returns_stub_when_missing(self):
        result = daily_digest.today()
        self.assertFalse(result["exists"])
        self.assertIsNone(result["body"])

    def test_digest_composed_event_published(self):
        bus.publish("agent.completed", "test", {
            "agent_type": "ResearchAgent", "task_desc": "research asyncio"
        })
        daily_digest.compose(force=True)
        events = bus.recent(20, "digest.composed")
        self.assertTrue(events)


if __name__ == "__main__":
    unittest.main()
