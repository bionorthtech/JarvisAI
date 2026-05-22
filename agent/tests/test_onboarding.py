"""
D10 — self-onboarding tests.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.core import onboarding as ob


class TestResolve(unittest.TestCase):
    def test_nonexistent_returns_none(self):
        self.assertIsNone(ob._resolve("/nope/does/not/exist"))

    def test_file_returns_none(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertIsNone(ob._resolve(f.name))

    def test_empty_returns_none(self):
        self.assertIsNone(ob._resolve(""))

    def test_expands_tilde(self):
        # Just verify it doesn't crash + returns an absolute path-ish result
        # if HOME exists.
        out = ob._resolve("~")
        if out is not None:
            self.assertTrue(out.is_absolute())


class TestMarkSeenIsNew(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ob-test-")
        self.state_path = Path(self.tmp) / "known.json"
        self._patch = mock.patch.object(ob, "_STATE_FILE", self.state_path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unseen_dir_is_new(self):
        proj = Path(self.tmp) / "proj"
        proj.mkdir()
        self.assertTrue(ob.is_new(str(proj)))

    def test_mark_seen_flips_is_new(self):
        proj = Path(self.tmp) / "proj"
        proj.mkdir()
        r = ob.mark_seen(str(proj))
        self.assertTrue(r["ok"])
        self.assertFalse(ob.is_new(str(proj)))

    def test_mark_seen_idempotent(self):
        proj = Path(self.tmp) / "proj"
        proj.mkdir()
        ob.mark_seen(str(proj))
        r = ob.mark_seen(str(proj))
        self.assertEqual(r["total_known"], 1)


class TestPropose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ob-propose-")
        self.state_path = Path(self.tmp) / "known.json"
        self._patch = mock.patch.object(ob, "_STATE_FILE", self.state_path)
        self._patch.start()
        # Fake project
        self.proj = Path(self.tmp) / "proj"
        self.proj.mkdir()
        (self.proj / "main.py").write_text("print('hi')")
        (self.proj / "util.py").write_text("x = 1")
        (self.proj / "README.md").write_text("# Demo project\n\nA quick test.")
        (self.proj / ".git").mkdir()
        # Add a node_modules to verify pruning
        (self.proj / "node_modules" / "foo").mkdir(parents=True)
        (self.proj / "node_modules" / "foo" / "index.js").write_text("module.exports={}")

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_propose_basic(self):
        r = ob.propose(str(self.proj))
        self.assertTrue(r["ok"])
        self.assertTrue(r["is_new"])
        self.assertIn(".git", r["markers"])
        # node_modules should be pruned → js NOT in top extensions
        self.assertNotIn("js", r["language_hist"])
        # The two .py files counted
        self.assertEqual(r["language_hist"].get("py"), 2)
        self.assertEqual(r["readme"]["name"], "README.md")
        self.assertIn("Demo project", r["readme"]["preview"])
        # Suggested actions present
        action_ids = [a["id"] for a in r["suggested_actions"]]
        self.assertIn("summarize", action_ids)
        self.assertIn("dismiss", action_ids)
        # skipped_dirs counts the node_modules + .git
        self.assertGreaterEqual(r["skipped_dirs"], 2)

    def test_propose_after_seen(self):
        ob.mark_seen(str(self.proj))
        r = ob.propose(str(self.proj))
        self.assertTrue(r["ok"])
        self.assertFalse(r["is_new"])

    def test_propose_unknown_path(self):
        r = ob.propose("/nope/nope")
        self.assertFalse(r["ok"])


class TestSummarize(unittest.TestCase):
    """Verify the dry-error path — LM unavailable returns ok=False cleanly."""

    def test_lm_unavailable_returns_error(self):
        with mock.patch.object(ob, "_alm", new=lambda *a, **kw: _async_empty()):
            tmp = tempfile.mkdtemp(prefix="ob-sum-")
            try:
                proj = Path(tmp) / "proj"
                proj.mkdir()
                (proj / "x.py").write_text("# x")
                r = ob.summarize(str(proj))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(r["ok"])


async def _async_empty():
    return ""


if __name__ == "__main__":
    unittest.main()
