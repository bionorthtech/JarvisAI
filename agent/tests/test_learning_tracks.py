"""F4 — learning_tracks module: list, complete, pause, due, drive bump."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core import bus, drives, learning_tracks


class TestLearningTracks(unittest.TestCase):
    def setUp(self):
        # Redirect state path to a tempdir for isolation.
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmp.name) / "state.json"
        self._notes_root = Path(self._tmp.name) / "notes"
        self._patches = [
            patch.object(learning_tracks, "_STATE_PATH", self._state_path),
            patch.object(learning_tracks, "_NOTES_ROOT", self._notes_root),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_list_tracks_loads_yaml(self):
        tracks = learning_tracks.list_tracks()
        self.assertGreaterEqual(len(tracks), 1)
        ids = {t["id"] for t in tracks}
        self.assertIn("python_advanced", ids)
        py = next(t for t in tracks if t["id"] == "python_advanced")
        self.assertGreater(py["topics_total"], 5)
        self.assertEqual(py["topics_done"], 0)
        self.assertEqual(py["status"], "active")

    def test_complete_topic_advances_cursor_and_emits_event(self):
        before = learning_tracks.get_track("python_advanced")
        first_topic = before["current_topic"]
        result = learning_tracks.complete_topic("python_advanced")
        self.assertTrue(result["ok"])
        self.assertEqual(result["topic"], first_topic)
        self.assertEqual(result["topics_done"], 1)
        # Cursor advanced
        after = learning_tracks.get_track("python_advanced")
        self.assertNotEqual(after["current_topic"], first_topic)
        self.assertEqual(after["topics_done"], 1)
        # learning.completed emitted
        events = bus.recent(limit=10, topic_prefix="learning.completed")
        self.assertTrue(events)
        self.assertEqual(events[0]["track_id"], "python_advanced")
        # Note stub written
        self.assertTrue(Path(result["note_path"]).exists())

    def test_complete_is_idempotent_for_same_topic(self):
        learning_tracks.complete_topic("python_advanced")
        # Find what just got completed
        state = json.loads(self._state_path.read_text())
        done_topic = state["python_advanced"]["completed"][0]
        # Try to re-complete the same one explicitly
        result = learning_tracks.complete_topic("python_advanced", topic=done_topic)
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("skipped"), "already complete")

    def test_drive_bumps_down_on_complete(self):
        # Force LEARNING to known value
        drives._state.set("LEARNING", 1.0)
        drives.save_state()
        learning_tracks.complete_topic("python_advanced")
        new = drives._state.get("LEARNING")
        self.assertLess(new, 1.0)

    def test_set_status_pause_and_drop(self):
        r = learning_tracks.set_status("rust_basics", "paused")
        self.assertTrue(r["ok"])
        self.assertEqual(r["new"], "paused")
        t = learning_tracks.get_track("rust_basics")
        self.assertEqual(t["status"], "paused")
        r2 = learning_tracks.set_status("rust_basics", "dropped")
        self.assertEqual(r2["new"], "dropped")
        r3 = learning_tracks.set_status("rust_basics", "bogus")
        self.assertFalse(r3["ok"])

    def test_due_tracks_respects_status_and_cadence(self):
        # Fresh state → all active tracks are due (never advanced).
        due = learning_tracks.due_tracks()
        self.assertIn("python_advanced", due)
        # Pause one — should drop out of due.
        learning_tracks.set_status("python_advanced", "paused")
        due2 = learning_tracks.due_tracks()
        self.assertNotIn("python_advanced", due2)
        # Complete one on tauri_2 — last_advance_ts becomes now, so not due.
        learning_tracks.complete_topic("tauri_2")
        due3 = learning_tracks.due_tracks()
        self.assertNotIn("tauri_2", due3)


if __name__ == "__main__":
    unittest.main()
