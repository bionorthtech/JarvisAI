"""
Plugin Marketplace

Local, curated registry — no cloud dependency. Registry is a JSON file at
~/jarvis/config/marketplace_registry.json (or override via env). Plugins
listed there can be installed one-click; install copies the plugin source
into ~/jarvis/plugins/<id>/ and runs verification before enabling.

Verification:
  1. plugin.json present and valid JSON
  2. plugin.py present and parses with stdlib `ast` (syntax check)
  3. No `os.system`, `subprocess.Popen(..., shell=True)`, or eval() in plugin.py
     (heuristic — supplement with manual review)
  4. SHA-256 of plugin source recorded in ~/.jarvis/plugin_hashes.json
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import bus

JARVIS_ROOT = Path.home() / "jarvis"
DEFAULT_REGISTRY = JARVIS_ROOT / "config" / "marketplace_registry.json"
PLUGINS_DIR = JARVIS_ROOT / "plugins"
HASHES_FILE = Path.home() / ".jarvis" / "plugin_hashes.json"

REGISTRY_PATH = Path(os.environ.get("JARVIS_MARKETPLACE_REGISTRY", str(DEFAULT_REGISTRY)))

_BANNED_PATTERNS = ("os.system(", "subprocess.Popen", "shell=True", "eval(", "exec(")


def load_registry() -> dict[str, Any]:
    try:
        if REGISTRY_PATH.exists():
            data = json.loads(REGISTRY_PATH.read_text())
            for plugin in data.get("plugins", []):
                plugin["installed"] = (PLUGINS_DIR / plugin["id"]).exists()
            return data
    except Exception:
        pass
    return {"version": 1, "plugins": []}


def install(plugin_id: str, source_path: str | None = None) -> dict[str, Any]:
    """
    Install a plugin from the registry. If `source_path` is provided, install
    from that local directory; otherwise look up the registry entry.
    """
    registry = load_registry()
    entry = next((p for p in registry["plugins"] if p["id"] == plugin_id), None)
    if not entry and not source_path:
        return {"error": f"plugin '{plugin_id}' not found in registry"}

    src = Path(source_path) if source_path else None
    if not src and entry and entry.get("source") not in ("builtin", None):
        src = Path(entry["source"])

    target = PLUGINS_DIR / plugin_id
    if target.exists():
        return {"error": f"plugin '{plugin_id}' already installed", "path": str(target)}

    if entry and entry.get("source") == "builtin":
        return {
            "ok": False,
            "note": f"'{plugin_id}' is a builtin and is already part of the codebase.",
        }

    if not src or not src.exists():
        return {"error": f"source path missing or invalid: {src}"}

    verification = verify(src)
    if not verification["ok"]:
        return {"error": "verification failed", "verification": verification}

    try:
        shutil.copytree(src, target)
    except Exception as e:
        return {"error": f"copy failed: {e}"}

    record_hash(plugin_id, target)
    bus.publish("marketplace.installed", "marketplace", {
        "id": plugin_id, "path": str(target),
    })
    return {"ok": True, "path": str(target), "verification": verification}


def uninstall(plugin_id: str) -> dict[str, Any]:
    registry = load_registry()
    entry = next((p for p in registry["plugins"] if p["id"] == plugin_id), None)
    if entry and entry.get("source") == "builtin":
        return {"error": "cannot uninstall builtin plugins"}

    target = PLUGINS_DIR / plugin_id
    if not target.exists():
        return {"error": "not installed"}
    try:
        shutil.rmtree(target)
        bus.publish("marketplace.uninstalled", "marketplace", {"id": plugin_id})
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)[:200]}


def verify(source_dir: Path) -> dict[str, Any]:
    """Check manifest + syntax + banned patterns. Heuristic only."""
    out: dict[str, Any] = {"ok": False, "checks": [], "warnings": []}

    manifest = source_dir / "plugin.json"
    if not manifest.exists():
        out["checks"].append({"name": "manifest_present", "ok": False,
                              "detail": "plugin.json missing"})
        return out
    out["checks"].append({"name": "manifest_present", "ok": True, "detail": "plugin.json exists"})

    try:
        json.loads(manifest.read_text())
        out["checks"].append({"name": "manifest_valid_json", "ok": True})
    except Exception as e:
        out["checks"].append({"name": "manifest_valid_json", "ok": False, "detail": str(e)[:200]})
        return out

    plugin_py = source_dir / "plugin.py"
    if not plugin_py.exists():
        out["checks"].append({"name": "plugin_py_present", "ok": False,
                              "detail": "plugin.py missing"})
        return out
    out["checks"].append({"name": "plugin_py_present", "ok": True})

    try:
        ast.parse(plugin_py.read_text(errors="replace"))
        out["checks"].append({"name": "syntax_valid", "ok": True})
    except SyntaxError as e:
        out["checks"].append({"name": "syntax_valid", "ok": False,
                              "detail": f"{e.msg} at line {e.lineno}"})
        return out

    text = plugin_py.read_text(errors="replace")
    for pat in _BANNED_PATTERNS:
        if pat in text:
            out["warnings"].append(f"banned pattern detected: {pat}")
    out["checks"].append({
        "name": "banned_patterns",
        "ok": len(out["warnings"]) == 0,
        "detail": "see warnings" if out["warnings"] else "clean",
    })

    out["ok"] = all(c["ok"] for c in out["checks"])
    return out


def record_hash(plugin_id: str, target: Path) -> None:
    h = _hash_directory(target)
    try:
        HASHES_FILE.parent.mkdir(parents=True, exist_ok=True)
        store = {}
        if HASHES_FILE.exists():
            store = json.loads(HASHES_FILE.read_text())
        store[plugin_id] = {
            "sha256": h,
            "recorded_at": time.time(),
            "path": str(target),
        }
        HASHES_FILE.write_text(json.dumps(store, indent=2))
    except Exception:
        pass


def _hash_directory(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            h.update(f.relative_to(path).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()
