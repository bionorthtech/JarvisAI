"""
Session persistence — saves/loads session summaries for the Welcome screen resume feature.
State: ~/.jarvis/sessions.json  (list of SessionRecord, newest first, max 20)
"""
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.session_store")

_PATH = Path.home() / ".jarvis" / "sessions.json"
_MAX_SESSIONS = 20


@dataclass
class SessionRecord:
    session_id: str
    project: str
    started: float          # epoch
    last_active: float      # epoch
    message_count: int
    last_message: str       # first 120 chars of last user message
    summary: str            # compactor summary or first assistant reply


def _load() -> list[dict]:
    try:
        if _PATH.exists():
            return json.loads(_PATH.read_text())
    except Exception as e:
        logger.debug("session_store load error: %s", e)
    return []


def _save(records: list[dict]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(records, indent=2))
    except Exception as e:
        logger.debug("session_store save error: %s", e)


def upsert(record: SessionRecord) -> None:
    """Add or update a session record. Keeps newest _MAX_SESSIONS."""
    records = _load()
    # Remove existing entry for this session_id
    records = [r for r in records if r.get("session_id") != record.session_id]
    records.insert(0, asdict(record))
    records = records[:_MAX_SESSIONS]
    _save(records)


def list_sessions() -> list[dict]:
    """Return all saved sessions newest first."""
    return _load()


def get_session(session_id: str) -> Optional[dict]:
    return next((r for r in _load() if r.get("session_id") == session_id), None)


def delete_session(session_id: str) -> bool:
    records = _load()
    new = [r for r in records if r.get("session_id") != session_id]
    if len(new) < len(records):
        _save(new)
        return True
    return False
