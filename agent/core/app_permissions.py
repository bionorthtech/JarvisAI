"""
Per-app permission store for the Apps Control tab.
Stores allow/ask/block rules in ~/.jarvis/app_permissions.json.
Checked by app_launcher plugin before launching.
"""
import json
import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger("jarvis.app_permissions")

_PATH = Path.home() / ".jarvis" / "app_permissions.json"

Permission = Literal["allow", "ask", "block"]

_DEFAULTS: dict[str, Permission] = {}


def _load() -> dict[str, Permission]:
    try:
        if _PATH.exists():
            return json.loads(_PATH.read_text())
    except Exception as e:
        logger.debug("app_permissions load error: %s", e)
    return dict(_DEFAULTS)


def _save(data: dict) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning("app_permissions save error: %s", e)


def get(app: str) -> Permission:
    return _load().get(app, "ask")


def set_permission(app: str, perm: Permission) -> None:
    data = _load()
    data[app] = perm
    _save(data)
    logger.info("app permission: %s → %s", app, perm)


def list_all() -> dict[str, Permission]:
    return _load()


def remove(app: str) -> bool:
    data = _load()
    if app in data:
        del data[app]
        _save(data)
        return True
    return False
