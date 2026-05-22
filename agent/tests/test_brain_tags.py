"""
B6.5 — brain auto-tagging tests.

Covers:
  - _has_real_tags parser for the three frontmatter shapes
  - detect_untagged_notes detector picks newest unt-agged non-Daily note
  - brain_apply_tags merges idempotently
"""
import time
import unittest
from pathlib import Path
from unittest import mock

from agent.aliveness import brain_co_ownership as bco


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


class TestHasRealTags(unittest.TestCase):
    def test_no_frontmatter(self):
        self.assertFalse(bco._has_real_tags("just a body"))

    def test_frontmatter_no_tags_key(self):
        self.assertFalse(bco._has_real_tags("---\ntitle: foo\n---\nbody"))

    def test_inline_list(self):
        self.assertTrue(bco._has_real_tags("---\ntags: [a, b, c]\n---\nbody"))

    def test_inline_csv(self):
        self.assertTrue(bco._has_real_tags("---\ntags: a, b, c\n---\nbody"))

    def test_empty_inline_list(self):
        self.assertFalse(bco._has_real_tags("---\ntags: []\n---\nbody"))

    def test_empty_after_colon(self):
        self.assertFalse(bco._has_real_tags("---\ntags:\ntitle: foo\n---\nbody"))

    def test_block_list(self):
        text = "---\ntags:\n  - python\n  - asyncio\n---\nbody"
        self.assertTrue(bco._has_real_tags(text))


class TestDetectUntagged(unittest.TestCase):
    def setUp(self):
        # Stand up a fake vault in a tmpdir.
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="brain-test-")
        self.vault = Path(self.tmp)
        self._patch = mock.patch.object(bco, "_VAULT", self.vault)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_notes_returns_empty(self):
        self.assertEqual(bco.detect_untagged_notes(), [])

    def test_skips_short_notes(self):
        _write(self.vault / "tiny.md", "two words")
        self.assertEqual(bco.detect_untagged_notes(), [])

    def test_skips_tagged_notes(self):
        _write(self.vault / "ok.md",
               "---\ntags: [python, asyncio]\n---\n" + "x" * 200)
        self.assertEqual(bco.detect_untagged_notes(), [])

    def test_skips_daily(self):
        _write(self.vault / "Daily" / "2026-05-16.md", "x" * 200)
        self.assertEqual(bco.detect_untagged_notes(), [])

    def test_finds_newest_untagged(self):
        _write(self.vault / "old.md", "x" * 200)
        time.sleep(0.05)
        _write(self.vault / "new.md", "y" * 200)
        out = bco.detect_untagged_notes()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "untagged_note")
        self.assertEqual(out[0]["payload"]["name"], "new")
        self.assertIn("brain.suggest_tags", out[0]["cta_topic"])


class TestApplyTags(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="brain-apply-")
        from plugins.second_brain import plugin as p
        self._orig_vault = p._VAULT
        p._VAULT = Path(self.tmp)

    def tearDown(self):
        from plugins.second_brain import plugin as p
        p._VAULT = self._orig_vault
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_frontmatter_when_absent(self):
        from plugins.second_brain.plugin import brain_apply_tags
        p = Path(self.tmp) / "note.md"
        p.write_text("just body")
        r = brain_apply_tags("note", ["alpha", "beta"])
        self.assertTrue(r["ok"])
        self.assertEqual(sorted(r["tags"]), ["alpha", "beta"])
        text = p.read_text()
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("tags: [alpha, beta]", text)
        self.assertIn("just body", text)

    def test_merges_idempotently(self):
        from plugins.second_brain.plugin import brain_apply_tags
        p = Path(self.tmp) / "note.md"
        p.write_text("---\ntags: [alpha]\n---\nbody")
        r1 = brain_apply_tags("note", ["beta", "alpha"])
        self.assertEqual(sorted(r1["tags"]), ["alpha", "beta"])
        # Second call shouldn't duplicate
        r2 = brain_apply_tags("note", ["alpha"])
        self.assertEqual(sorted(r2["tags"]), ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
