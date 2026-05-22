"""
F4 — Self-directed learning tracks.

Loads curated curricula from `~/jarvis/config/learning_tracks.yaml`, keeps
per-track progress state at `~/.jarvis/learning_tracks_state.json`, and
exposes the API the Brain pane + ResearchAgent + autonomy daemon all
consume.

Tracks roll forward at a configurable cadence (default 7 days/topic).
Completing a topic:
  - moves cursor forward
  - emits `learning.completed` on the bus
  - nudges the LEARNING drive down (closes the loop with B6.2)
  - drops a stub note at second_brain/learning/<track>/<topic>.md
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import bus, drives


# F1.1 contract — module-level engine.
min_autonomy_level: int = 2
wake_conditions: list[str] = ["learning.due", "drive.LEARNING.high"]


_CONFIG_PATH = Path.home() / "jarvis" / "config" / "learning_tracks.yaml"
_STATE_PATH  = Path.home() / ".jarvis" / "learning_tracks_state.json"
_NOTES_ROOT  = Path.home() / "jarvis" / "second_brain" / "learning"

_DEFAULT_CADENCE_DAYS = 7
_LEARNING_BUMP        = 0.30   # how much to drop the LEARNING drive on complete


# ── Tiny YAML reader. Two-pass: tokenize lines, then build the tree with
# lookahead so that a "key:" followed by indented `- items` becomes a list,
# and a "key:" followed by indented "k: v" pairs becomes a map.
def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}

    def coerce(v: str) -> Any:
        v = v.strip()
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        try: return int(v)
        except ValueError: pass
        try: return float(v)
        except ValueError: pass
        return v.strip('"').strip("'")

    # Tokenize: (indent, kind, key, value) where kind ∈ {kv, list}
    tokens: list[tuple[int, str, str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if s.startswith("- "):
            tokens.append((indent, "list", "", s[2:].strip()))
        elif ":" in s:
            k, v = s.split(":", 1)
            tokens.append((indent, "kv", k.strip(), v.strip()))

    def parse(i: int, indent: int) -> tuple[Any, int]:
        """Parse a container starting at tokens[i] with given indent.
        Returns (value, next_index_after_this_container).
        """
        # Peek to decide list vs map
        if i >= len(tokens):
            return None, i
        first = tokens[i]
        if first[0] != indent:
            return None, i
        kind = first[1]
        if kind == "list":
            lst: list[Any] = []
            while i < len(tokens) and tokens[i][0] == indent and tokens[i][1] == "list":
                _, _, _, val = tokens[i]
                lst.append(coerce(val))
                i += 1
            return lst, i
        # map
        m: dict[str, Any] = {}
        while i < len(tokens) and tokens[i][0] == indent and tokens[i][1] == "kv":
            _, _, key, val = tokens[i]
            i += 1
            if val == "":
                # nested container — look at next token's indent
                if i < len(tokens) and tokens[i][0] > indent:
                    child, i = parse(i, tokens[i][0])
                    m[key] = child if child is not None else {}
                else:
                    m[key] = None
            elif val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                m[key] = [coerce(p) for p in inner.split(",") if p.strip()]
            elif val.startswith("{") and val.endswith("}"):
                inner = val[1:-1].strip()
                m[key] = {p.split(":", 1)[0].strip(): coerce(p.split(":", 1)[1])
                          for p in inner.split(",") if ":" in p}
            else:
                m[key] = coerce(val)
        return m, i

    if not tokens:
        return {}
    result, _ = parse(0, tokens[0][0])
    return result if isinstance(result, dict) else {}


# ── Track + state loading ──────────────────────────────────────────────────

def _load_config() -> dict[str, dict[str, Any]]:
    raw = _load_yaml(_CONFIG_PATH)
    tracks = raw.get("tracks") or {}
    return tracks if isinstance(tracks, dict) else {}


def _load_state() -> dict[str, dict[str, Any]]:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict[str, dict[str, Any]]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def _ensure_track_state(state: dict[str, dict[str, Any]], track_id: str) -> dict[str, Any]:
    if track_id not in state:
        state[track_id] = {
            "cursor": 0,
            "completed": [],
            "last_advance_ts": 0.0,
            "status": "active",   # active | paused | dropped | done
        }
    return state[track_id]


# ── Public API ─────────────────────────────────────────────────────────────

def list_tracks() -> list[dict[str, Any]]:
    """Return every configured track with merged progress state."""
    cfg = _load_config()
    state = _load_state()
    out: list[dict[str, Any]] = []
    for tid, track in cfg.items():
        st = _ensure_track_state(state, tid)
        topics: list[str] = list(track.get("topics") or [])
        total = len(topics)
        cursor = max(0, min(int(st.get("cursor", 0)), total))
        current = topics[cursor] if 0 <= cursor < total else None
        out.append({
            "id":             tid,
            "name":           track.get("name", tid),
            "cadence_days":   int(track.get("cadence_days", _DEFAULT_CADENCE_DAYS)),
            "prereqs":        list(track.get("prereqs") or []),
            "sources":        list(track.get("sources") or []),
            "topics_total":   total,
            "topics_done":    len(st.get("completed") or []),
            "current_topic":  current,
            "next_topic":     topics[cursor + 1] if cursor + 1 < total else None,
            "status":         st.get("status", "active"),
            "last_advance_ts": st.get("last_advance_ts", 0.0),
            "progress_pct":   (cursor / total) if total else 1.0,
        })
    _save_state(state)
    return out


def get_track(track_id: str) -> dict[str, Any] | None:
    for t in list_tracks():
        if t["id"] == track_id:
            return t
    return None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "topic"


def _write_note_stub(track_id: str, topic: str, eval_q: str | None) -> Path:
    notes_dir = _NOTES_ROOT / track_id
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"{_slug(topic)}.md"
    if path.exists():
        return path
    body = [
        f"# {topic}",
        "",
        f"_Track: {track_id} · stub generated by F4 learning_tracks._",
        "",
        "## Eval question",
        f"{eval_q or '(none)'}",
        "",
        "## Notes",
        "<!-- ResearchAgent fills this in -->",
        "",
    ]
    path.write_text("\n".join(body))
    return path


def complete_topic(track_id: str, topic: str | None = None) -> dict[str, Any]:
    """Mark the current (or named) topic as complete on a track. Emits
    `learning.completed`, bumps LEARNING drive down, writes note stub.
    Idempotent for the same topic — does not double-advance.
    """
    cfg = _load_config()
    if track_id not in cfg:
        return {"ok": False, "error": f"unknown track: {track_id}"}
    state = _load_state()
    st = _ensure_track_state(state, track_id)
    topics: list[str] = list(cfg[track_id].get("topics") or [])
    if not topics:
        return {"ok": False, "error": "track has no topics"}
    cursor = max(0, min(int(st.get("cursor", 0)), len(topics) - 1))
    actual_topic = topic or topics[cursor]
    if actual_topic in (st.get("completed") or []):
        return {"ok": True, "skipped": "already complete", "topic": actual_topic}

    st.setdefault("completed", []).append(actual_topic)
    if topic is None or topic == topics[cursor]:
        st["cursor"] = cursor + 1
    st["last_advance_ts"] = time.time()
    if st["cursor"] >= len(topics):
        st["status"] = "done"
    _save_state(state)

    eval_template = cfg[track_id].get("eval_question_template") or ""
    eval_q = eval_template.replace("{topic}", actual_topic) if eval_template else None
    note_path = _write_note_stub(track_id, actual_topic, eval_q)

    # F4.3 — close the drive loop.
    drives.bump("LEARNING", _LEARNING_BUMP)

    # NB: bus.recent() spreads payload into the event dict, so payload keys
    # that collide with bus columns (id/ts/topic/sender) get overwritten.
    # Use `topic_name` rather than `topic` for the learning-topic field.
    bus.publish("learning.completed", "learning_tracks", {
        "track_id":     track_id,
        "topic_name":   actual_topic,
        "topics_done":  len(st["completed"]),
        "topics_total": len(topics),
        "note_path":    str(note_path),
        "eval_question": eval_q,
    })
    return {
        "ok":            True,
        "track_id":      track_id,
        "topic":         actual_topic,
        "topics_done":   len(st["completed"]),
        "topics_total":  len(topics),
        "note_path":     str(note_path),
        "track_status":  st["status"],
    }


def set_status(track_id: str, status: str) -> dict[str, Any]:
    """Pause / resume / drop a track."""
    if status not in ("active", "paused", "dropped"):
        return {"ok": False, "error": "status must be active|paused|dropped"}
    cfg = _load_config()
    if track_id not in cfg:
        return {"ok": False, "error": f"unknown track: {track_id}"}
    state = _load_state()
    st = _ensure_track_state(state, track_id)
    old = st.get("status", "active")
    st["status"] = status
    _save_state(state)
    bus.publish("learning.status_changed", "learning_tracks", {
        "track_id": track_id, "old": old, "new": status,
    })
    return {"ok": True, "track_id": track_id, "old": old, "new": status}


def due_tracks() -> list[str]:
    """Track IDs whose cadence has elapsed since the last advance — these
    are what the autonomy daemon (level 2+) wakes ResearchAgent for."""
    out: list[str] = []
    for t in list_tracks():
        if t["status"] != "active" or t["current_topic"] is None:
            continue
        cadence_s = max(1, int(t["cadence_days"])) * 86400
        if (time.time() - float(t["last_advance_ts"])) >= cadence_s:
            out.append(t["id"])
    return out
