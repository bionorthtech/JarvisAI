"""Bus taxonomy guard.

Two-way check between `docs/BUS_EVENTS.md` and the codebase:

1. Every topic documented in BUS_EVENTS.md must have at least one literal
   `bus.publish("topic", ...)` callsite somewhere in `agent/`, `main.py`,
   or `plugins/`. Catches "documented but never emitted" drift.

2. Every literal topic emitted via `bus.publish("topic", ...)` in code must
   appear in BUS_EVENTS.md. Catches "emitted but never documented" drift.

Dynamic-topic emitters (e.g. `bus.publish(f"agent.{event}", ...)`) are
exempted via `_DYNAMIC_PREFIXES`.

Test `test_loaded_plugins_metadata_shape` exercises the plugin loader's
`_LOADED_PLUGINS` registry that the heartbeat loop reads — without this,
a refactor of the registry shape could silently break the dashboard.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOC_PATH  = REPO_ROOT / "docs" / "BUS_EVENTS.md"

# Topics whose first segment matches any of these are considered
# dynamically-named — exempt from the bidirectional doc check. Each pattern
# must also be referenced in BUS_EVENTS.md "Dynamic-topic exceptions".
_DYNAMIC_PREFIXES = ("agent.",)

_PUBLISH_LITERAL = re.compile(r'bus\.publish\(\s*"([a-zA-Z0-9_.]+)"')
_PUBLISH_DYNAMIC = re.compile(r'bus\.publish\(\s*f"([a-zA-Z0-9_.{}]+)"')

# Backtick-quoted topic in BUS_EVENTS.md table cells: | `topic.name` | ...
_DOC_TOPIC = re.compile(r'`([a-z][a-z0-9_]*\.[a-z][a-zA-Z0-9_.]*)`')


def _scan_publishes() -> tuple[set[str], set[str]]:
    """Walk agent/, main.py, plugins/ and return (literal_topics, dynamic_topics_seen)."""
    literal: set[str] = set()
    dynamic: set[str] = set()
    targets = [
        REPO_ROOT / "agent",
        REPO_ROOT / "plugins",
    ]
    files = [REPO_ROOT / "main.py"]
    for t in targets:
        if t.exists():
            files.extend(t.rglob("*.py"))
    for f in files:
        # Skip the test directory itself — tests publish synthetic events.
        if "tests" in f.parts:
            continue
        try:
            text = f.read_text()
        except Exception:
            continue
        literal.update(_PUBLISH_LITERAL.findall(text))
        for raw in _PUBLISH_DYNAMIC.findall(text):
            # Take everything before the first "{" as the literal prefix.
            prefix = raw.split("{", 1)[0]
            dynamic.add(prefix)
    return literal, dynamic


def _scan_doc_topics() -> set[str]:
    """Pull every `topic.name` mentioned in BUS_EVENTS.md.

    Only topics that look like an event topic (first segment lowercase,
    contains a dot, not a file path with .py / .md extension)."""
    if not DOC_PATH.exists():
        return set()
    text = DOC_PATH.read_text()
    candidates = set(_DOC_TOPIC.findall(text))
    # Filter out things that look like file paths or module names.
    return {
        c for c in candidates
        if not c.endswith((".py", ".md", ".json", ".jsonl", ".txt", ".sh"))
        and not c.startswith(("agent.core", "agent.bots", "agent.aliveness",
                              "agent.tests", "main."))
    }


class TestBusTaxonomyDocSyncedWithCode(unittest.TestCase):

    def setUp(self):
        self.literal_emitted, self.dynamic_emitted = _scan_publishes()
        self.documented = _scan_doc_topics()

    def test_doc_file_exists(self):
        self.assertTrue(
            DOC_PATH.exists(),
            f"docs/BUS_EVENTS.md is missing — every B3 topic must be documented "
            f"({DOC_PATH})",
        )

    def test_every_literal_emit_is_documented(self):
        """Each `bus.publish("topic", ...)` literal must appear in the doc."""
        undocumented = sorted(self.literal_emitted - self.documented)
        self.assertEqual(
            undocumented, [],
            "These topics are emitted in code but not documented in "
            "docs/BUS_EVENTS.md — add them or remove the publish:\n  "
            + "\n  ".join(undocumented),
        )

    def test_every_documented_topic_is_emitted_or_dynamic(self):
        """Every documented topic must be emitted somewhere, or covered by a
        dynamic prefix exception declared in `_DYNAMIC_PREFIXES`."""
        unused: list[str] = []
        for topic in sorted(self.documented):
            if topic in self.literal_emitted:
                continue
            if any(topic.startswith(p) for p in _DYNAMIC_PREFIXES):
                continue
            # Heartbeats, plugin.loaded, plugin.failed, config.changed, and
            # bot.heartbeat are emitted from helpers added in B3 — they show
            # up as literals in plugin_loader.py / autonomy.py respectively
            # and will pass test_every_literal_emit_is_documented above.
            unused.append(topic)
        self.assertEqual(
            unused, [],
            "These topics are documented in docs/BUS_EVENTS.md but never "
            "emitted in code — implement the publish or remove the doc row:\n  "
            + "\n  ".join(unused),
        )

    def test_dynamic_emitters_have_doc_exception(self):
        """Every f-string topic prefix seen in code must be listed in the
        `_DYNAMIC_PREFIXES` set. If you add a new dynamic emitter, append
        its prefix here AND describe the pattern in BUS_EVENTS.md."""
        unhandled = sorted(
            p for p in self.dynamic_emitted
            if not any(p.startswith(known) for known in _DYNAMIC_PREFIXES)
        )
        self.assertEqual(
            unhandled, [],
            "These dynamic topic prefixes have no exception entry in "
            "_DYNAMIC_PREFIXES (and presumably no doc):\n  "
            + "\n  ".join(unhandled),
        )


class TestPluginLoaderRegistryShape(unittest.TestCase):
    """Heartbeat loop reads `_LOADED_PLUGINS` — pin its shape."""

    def test_loaded_plugins_returns_list_of_dicts(self):
        from agent.core import plugin_loader
        snapshot = plugin_loader.loaded_plugins()
        self.assertIsInstance(snapshot, list)
        for entry in snapshot:
            self.assertIn("plugin_id", entry)
            self.assertIn("version", entry)
            self.assertIn("tools", entry)
            self.assertIn("status", entry)
            self.assertIsInstance(entry["tools"], list)


if __name__ == "__main__":
    unittest.main()
