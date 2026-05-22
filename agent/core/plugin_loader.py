"""
Plugin loader — auto-discovers and registers external plugins.

Plugin layout:  plugins/<name>/plugin.json  +  plugins/<name>/plugin.py
plugin.json defines tool schemas; plugin.py provides async implementations.

Plugins with "requires_internet": true are skipped unless internet access is
explicitly enabled (JARVIS_INTERNET_ACCESS=1 env var). Default: OFF.

Every loaded plugin emits `plugin.loaded` once at discovery and a
`plugin.heartbeat` every `_HEARTBEAT_INTERVAL_S` (60s by default) via
`run_heartbeat_loop()`, which is started from the FastAPI lifespan. The
heartbeat is centrally emitted so plugin authors don't need to know about
the bus — the loader publishes on their behalf using the manifest it
already has.
"""
import importlib.util
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from . import bus

logger = logging.getLogger("jarvis.plugins")

# Internet access is OFF by default. Set JARVIS_INTERNET_ACCESS=1 to enable.
_INTERNET_ENABLED = os.environ.get("JARVIS_INTERNET_ACCESS", "0").strip() == "1"

# Directories scanned for plugins (project-level first, then user-level)
_PLUGIN_DIRS = [
    Path(__file__).parent.parent.parent / "plugins",   # <project>/plugins/
    Path.home() / ".jarvis" / "plugins",               # ~/.jarvis/plugins/
]

# Plugin heartbeat cadence (seconds). Dashboard treats >2x as "silent".
_HEARTBEAT_INTERVAL_S = int(os.environ.get("JARVIS_PLUGIN_HEARTBEAT_S", "60"))

# Discovered plugin metadata, populated by discover_plugins() and consumed by
# the heartbeat loop. Each entry: {plugin_id, version, tools:[name], status}.
_LOADED_PLUGINS: Dict[str, Dict] = {}


def _load_plugin_module(plugin_dir: Path):
    """Import plugin.py from plugin_dir and return the module."""
    py_path = plugin_dir / "plugin.py"
    if not py_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        f"jarvis_plugin_{plugin_dir.name}", py_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover_plugins() -> List[Tuple[Dict, Callable, str]]:
    """
    Scan plugin dirs and return list of (tool_schema, async_callable, tier) tuples.
    Skips plugins that are malformed, missing implementations, or require internet
    when internet access is disabled.
    """
    results: List[Tuple[Dict, Callable, str]] = []

    for base_dir in _PLUGIN_DIRS:
        if not base_dir.exists():
            continue
        for plugin_dir in sorted(base_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_path = plugin_dir / "plugin.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = json.loads(manifest_path.read_text())

                # Internet gate — skip internet-requiring plugins when offline
                if manifest.get("requires_internet", False) and not _INTERNET_ENABLED:
                    logger.info(
                        "plugin %s skipped (requires_internet=true, internet disabled)",
                        plugin_dir.name,
                    )
                    continue

                module = _load_plugin_module(plugin_dir)
                if module is None:
                    logger.warning("plugin %s: missing plugin.py", plugin_dir.name)
                    bus.publish("plugin.failed", "plugin_loader", {
                        "plugin_id": plugin_dir.name, "error": "missing plugin.py",
                    })
                    continue

                tool_names: List[str] = []
                for tool_def in manifest.get("tools", []):
                    tool_name = tool_def.get("name")
                    if not tool_name:
                        continue
                    fn = getattr(module, tool_name, None)
                    if fn is None or not callable(fn):
                        logger.warning(
                            "plugin %s: no function '%s'", plugin_dir.name, tool_name
                        )
                        continue

                    schema = {
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": tool_def.get("description", ""),
                            "parameters": tool_def.get(
                                "parameters",
                                {"type": "object", "properties": {}, "required": []},
                            ),
                        },
                    }
                    tier = tool_def.get("tier", "SAFE")
                    results.append((schema, fn, tier))
                    tool_names.append(tool_name)
                    logger.info(
                        "plugin loaded: %s from %s (tier=%s)", tool_name, plugin_dir.name, tier
                    )

                # Record metadata for heartbeats + emit plugin.loaded.
                _LOADED_PLUGINS[plugin_dir.name] = {
                    "plugin_id": plugin_dir.name,
                    "version": manifest.get("version", "unknown"),
                    "tools": tool_names,
                    "status": "loaded",
                    "loaded_at": time.time(),
                }
                bus.publish("plugin.loaded", "plugin_loader", {
                    "plugin_id": plugin_dir.name,
                    "version": manifest.get("version", "unknown"),
                    "tools": tool_names,
                })

            except Exception as e:
                logger.warning("plugin %s failed to load: %s", plugin_dir.name, e)
                bus.publish("plugin.failed", "plugin_loader", {
                    "plugin_id": plugin_dir.name, "error": str(e)[:200],
                })

    return results


# ─── Heartbeat loop ───────────────────────────────────────────────────────────

def loaded_plugins() -> List[Dict]:
    """Return a snapshot of the currently-loaded plugin metadata."""
    return [dict(p) for p in _LOADED_PLUGINS.values()]


def heartbeat_once() -> None:
    """4A — single heartbeat tick. Publishes `plugin.heartbeat` for every
    loaded plugin once. The orchestrator's periodic registry drives the
    cadence (see autonomy._build_periodic_registry)."""
    try:
        for meta in _LOADED_PLUGINS.values():
            bus.publish("plugin.heartbeat", "plugin_loader", {
                "plugin_id": meta["plugin_id"],
                "version": meta["version"],
                "tools": meta["tools"],
                "status": meta["status"],
            })
    except Exception as e:
        logger.warning("plugin heartbeat tick failed: %s", e)


# run_heartbeat_loop removed in 4B — plugin.heartbeat is now an
# orchestrator registry entry; `heartbeat_once()` above is the public
# tick the registry calls.
