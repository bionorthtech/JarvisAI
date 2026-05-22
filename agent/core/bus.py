"""
Inter-agent message bus — SQLite-backed persistence + asyncio Queue push.

Every module publishes events here. WebSocket clients (dashboard) subscribe
and get a live stream. History is queryable after restart.

Usage:
    bus.publish("agent.task_started", "director", {"task_id": ..., "desc": ...})
    q = bus.subscribe()        # returns asyncio.Queue
    bus.unsubscribe(q)
    bus.recent(50, "agent.")   # last 50 agent.* messages
"""
from __future__ import annotations
import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_DB = Path.home() / ".jarvis" / "bus.db"
_subscribers: list[asyncio.Queue] = []


# ─── Storage ──────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB), check_same_thread=False)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id      TEXT PRIMARY KEY,
            ts      REAL NOT NULL,
            topic   TEXT NOT NULL,
            sender  TEXT NOT NULL,
            payload TEXT NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_topic ON messages(topic)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ts    ON messages(ts)")
    c.commit()
    return c

_db: sqlite3.Connection | None = None

def _get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = _conn()
    return _db


# ─── Public API ───────────────────────────────────────────────────────────────

def publish(topic: str, sender: str, payload: dict[str, Any]) -> str:
    """Publish a message. Returns the message ID. Thread-safe."""
    msg_id = str(uuid.uuid4())
    ts = time.time()
    full = {"id": msg_id, "ts": ts, "topic": topic, "sender": sender, **payload}
    try:
        db = _get_db()
        db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?)",
            (msg_id, ts, topic, sender, json.dumps(payload)),
        )
        db.commit()
    except Exception:
        pass
    # Push to all live subscribers (non-blocking)
    for q in list(_subscribers):
        try:
            q.put_nowait(full)
        except (asyncio.QueueFull, RuntimeError):
            pass
    return msg_id


def subscribe(maxsize: int = 500) -> asyncio.Queue:
    """Subscribe to all bus messages. Returns an asyncio.Queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    try:
        _subscribers.remove(q)
    except ValueError:
        pass


def recent(limit: int = 50, topic_prefix: str = "") -> list[dict]:
    """Return recent messages newest-first, optionally filtered by topic prefix."""
    try:
        db = _get_db()
        if topic_prefix:
            rows = db.execute(
                "SELECT id,ts,topic,sender,payload FROM messages "
                "WHERE topic LIKE ? ORDER BY ts DESC LIMIT ?",
                (topic_prefix.rstrip("%") + "%", limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id,ts,topic,sender,payload FROM messages "
                "ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            out.append({"id": r[0], "ts": r[1], "topic": r[2], "sender": r[3],
                        **json.loads(r[4])})
        except Exception:
            pass
    return out


def by_task_id(task_id: str, limit: int = 500) -> list[dict]:
    """D6 — return every persisted bus message whose payload contains
    `task_id == <id>`, ordered oldest-first for replay. Uses a JSON LIKE
    search (SQLite has no JSON1 dependency here) which is fine for the
    expected event volume per task."""
    try:
        db = _get_db()
        rows = db.execute(
            "SELECT id,ts,topic,sender,payload FROM messages "
            "WHERE payload LIKE ? ORDER BY ts ASC LIMIT ?",
            (f'%"task_id": "{task_id}"%', limit),
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            out.append({"id": r[0], "ts": r[1], "topic": r[2], "sender": r[3],
                        **json.loads(r[4])})
        except Exception:
            pass
    return out


def recent_task_ids(limit: int = 30) -> list[dict]:
    """D6 — return the most recent unique task_ids seen in the bus, each
    with task_desc + agent_type + first_ts + last_ts + event_count.
    Used to populate the replay-mode task picker."""
    try:
        db = _get_db()
        rows = db.execute(
            "SELECT ts,topic,sender,payload FROM messages "
            "WHERE topic LIKE 'agent.%' AND payload LIKE '%task_id%' "
            "ORDER BY ts DESC LIMIT ?",
            (limit * 30,),  # over-fetch so we get plenty of distinct task_ids
        ).fetchall()
    except Exception:
        return []
    seen: dict[str, dict] = {}
    for r in rows:
        try:
            payload = json.loads(r[3])
        except Exception:
            continue
        tid = payload.get("task_id")
        if not tid:
            continue
        entry = seen.setdefault(tid, {
            "task_id": tid,
            "task_desc": payload.get("task_desc", ""),
            "agent_type": payload.get("agent_type", ""),
            "first_ts": r[0], "last_ts": r[0],
            "event_count": 0,
        })
        entry["first_ts"] = min(entry["first_ts"], r[0])
        entry["last_ts"] = max(entry["last_ts"], r[0])
        entry["event_count"] += 1
    return sorted(seen.values(), key=lambda e: -e["last_ts"])[:limit]


def subscriber_count() -> int:
    return len(_subscribers)
