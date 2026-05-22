"""
JARVIS Audit Log — tamper-evident, append-only.
SQLite-backed with SHA-256 chained hashes. Every tool call is recorded.

Chain integrity: each row's this_hash = SHA-256(row_data + prev_hash).
Tampering any row breaks every subsequent hash — detectable via verify_chain().
"""
import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.audit")

_DB_PATH = Path.home() / ".jarvis" / "audit.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    tool_name   TEXT    NOT NULL,
    action_type TEXT    NOT NULL,
    params_json TEXT    NOT NULL,
    result_hash TEXT    NOT NULL,
    confirmed   INTEGER NOT NULL DEFAULT 0,
    prev_hash   TEXT    NOT NULL,
    this_hash   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH))
    c.executescript(_SCHEMA)
    c.commit()
    return c


def _last_hash() -> str:
    c = _conn()
    row = c.execute(
        "SELECT this_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    c.close()
    return row[0] if row else "GENESIS"


def _hash_entry(entry: dict, prev: str) -> str:
    payload = json.dumps(entry, sort_keys=True) + prev
    return hashlib.sha256(payload.encode()).hexdigest()


def _sanitize_params(params: dict) -> dict:
    """Remove secrets from params before storing."""
    safe = {}
    for k, v in params.items():
        if k in ("content",):
            # Store size + hash of content, not the content itself
            s = str(v)
            safe[k] = f"<{len(s)} chars, sha256={hashlib.sha256(s.encode()).hexdigest()[:16]}>"
        else:
            safe[k] = str(v)[:500]
    return safe


def log(
    session_id: str,
    tool_name: str,
    action_type: str,
    params: dict,
    result: Optional[str] = None,
    confirmed: bool = False,
) -> None:
    """Append one entry to the audit log. Non-blocking best-effort."""
    try:
        prev = _last_hash()
        result_hash = hashlib.sha256((result or "").encode()).hexdigest()[:16]

        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,
            "tool_name": tool_name,
            "action_type": action_type,
            "params_json": json.dumps(_sanitize_params(params)),
            "result_hash": result_hash,
            "confirmed": int(confirmed),
        }
        this_hash = _hash_entry(entry, prev)

        c = _conn()
        c.execute(
            """INSERT INTO audit_log
               (timestamp, session_id, tool_name, action_type, params_json,
                result_hash, confirmed, prev_hash, this_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry["timestamp"], entry["session_id"], entry["tool_name"],
                entry["action_type"], entry["params_json"], entry["result_hash"],
                entry["confirmed"], prev, this_hash,
            ),
        )
        c.commit()
        c.close()
    except Exception as e:
        logger.warning("audit log write failed: %s", e)


def recent(n: int = 100, session_id: Optional[str] = None) -> list:
    """Return the N most recent audit entries, optionally filtered by session."""
    try:
        c = _conn()
        if session_id:
            rows = c.execute(
                "SELECT id, timestamp, session_id, tool_name, action_type, "
                "params_json, result_hash, confirmed, this_hash "
                "FROM audit_log WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, n),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, timestamp, session_id, tool_name, action_type, "
                "params_json, result_hash, confirmed, this_hash "
                "FROM audit_log ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
        c.close()
        keys = [
            "id", "timestamp", "session_id", "tool_name", "action_type",
            "params_json", "result_hash", "confirmed", "this_hash",
        ]
        return [dict(zip(keys, row)) for row in rows]
    except Exception as e:
        logger.warning("audit log read failed: %s", e)
        return []


def verify_chain() -> tuple:
    """
    Walk the entire log and verify every hash. Returns (ok: bool, message: str).
    A False result means the log has been tampered with.
    """
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, timestamp, session_id, tool_name, action_type, "
            "params_json, result_hash, confirmed, prev_hash, this_hash "
            "FROM audit_log ORDER BY id ASC"
        ).fetchall()
        c.close()
    except Exception as e:
        return False, f"DB error: {e}"

    prev = "GENESIS"
    for row in rows:
        (row_id, ts, sid, tool, atype, params, r_hash,
         confirmed, prev_hash, this_hash) = row

        entry = {
            "timestamp": ts,
            "session_id": sid,
            "tool_name": tool,
            "action_type": atype,
            "params_json": params,
            "result_hash": r_hash,
            "confirmed": confirmed,
        }
        expected_prev = prev
        expected_hash = _hash_entry(entry, expected_prev)

        if prev_hash != expected_prev:
            return False, f"Chain broken at id={row_id}: prev_hash mismatch"
        if this_hash != expected_hash:
            return False, f"Chain broken at id={row_id}: hash mismatch — possible tampering"
        prev = this_hash

    return True, f"Chain intact — {len(rows)} entries verified"


def stats() -> dict:
    """Return summary stats for the audit log."""
    try:
        c = _conn()
        total = c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        danger = c.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action_type IN ('DANGER', 'CRITICAL')"
        ).fetchone()[0]
        last_ts = c.execute(
            "SELECT timestamp FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        c.close()
        return {
            "total_entries": total,
            "danger_critical": danger,
            "last_entry": last_ts[0] if last_ts else None,
        }
    except Exception:
        return {"total_entries": 0, "danger_critical": 0, "last_entry": None}
