"""
D7 — Plugin/bot "personality cards".

Each bot in `agent/bots/` (D7.1) and each plugin in `plugins/` (D7.6)
gets a 1-paragraph capability summary written by the LM after
introspecting the module — docstring + primary class for bots, manifest
description + tool list for plugins. Surfaced in the UI on hover and
in any view that lists bots/plugins, so the user gets a feel for what
each one does without reading source.

Cards are cached to `~/.jarvis/personality_cards.json` so they survive
restarts. A card is regenerated when:
  - The user hits `POST /personality-cards/regenerate/<id>` explicitly.
  - The cache age exceeds `_REFRESH_AFTER_S` (default 7 days).
  - The source module's mtime is newer than the cached `generated_at`.

Generation is lazy and best-effort: a missing card returns a stub
({"text": "(no card yet)", "stale": True}) so the UI always has
something to render.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import bus


logger = logging.getLogger("jarvis.personality_cards")

_CARD_FILE = Path.home() / ".jarvis" / "personality_cards.json"
_BOTS_DIR = Path(__file__).resolve().parents[1] / "bots"
_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"
_REFRESH_AFTER_S = 7 * 24 * 3600

# System prompt template — kept short and prescriptive so cards come out
# uniform across bots.
_PROMPT_TEMPLATE = (
    "You are introspecting a single autonomous bot inside the JARVIS agent. "
    "Below is the module docstring and the primary class name. Write a "
    "60-90 word capability blurb in the bot's *own voice* — first person, "
    "present tense. State what it watches, what it produces, and when it "
    "runs. No fluff, no marketing. End with one sentence on its trigger.\n\n"
    "Class: {class_name}\n"
    "Module docstring:\n\"\"\"\n{docstring}\n\"\"\""
)

# D7.6 — plugin prompt. Plugins expose tools rather than running on a
# schedule, so the framing differs: what tools they offer + when they fire.
_PLUGIN_PROMPT_TEMPLATE = (
    "You are introspecting a single plugin inside the JARVIS agent. "
    "Each plugin contributes one or more tools that the LM can call. "
    "Write a 60-90 word capability blurb in the plugin's *own voice* — "
    "first person, present tense. State what tools you provide, what each "
    "does, and when JARVIS would reach for you. No fluff, no marketing.\n\n"
    "Name: {name}\nDescription: {description}\nTools:\n{tools}"
)


def _discover_bots() -> list[dict[str, Any]]:
    """Walk agent/bots/ for *.py files and return [{id, class_name,
    docstring, module_mtime}]. Skips dunder modules and base utilities."""
    out: list[dict[str, Any]] = []
    if not _BOTS_DIR.exists():
        return out
    for p in sorted(_BOTS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        bot_id = p.stem
        try:
            mod = importlib.import_module(f"agent.bots.{bot_id}")
        except Exception as e:
            logger.debug("could not import %s: %s", bot_id, e)
            continue
        docstring = (mod.__doc__ or "").strip()
        class_name = ""
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            # Take the first class defined IN this module (not imports).
            if getattr(obj, "__module__", "") == f"agent.bots.{bot_id}":
                class_name = name
                break
        out.append({
            "id": bot_id,
            "class_name": class_name,
            "docstring": docstring,
            "module_mtime": p.stat().st_mtime,
        })
    return out


def _load_cache() -> dict[str, Any]:
    try:
        if _CARD_FILE.exists():
            return json.loads(_CARD_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        _CARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CARD_FILE.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


async def _generate_card(bot: dict[str, Any]) -> Optional[str]:
    """One LM call to produce a personality blurb. Returns None on error."""
    from agent.core.lm_studio import get_client
    prompt = _PROMPT_TEMPLATE.format(
        class_name=bot.get("class_name") or bot["id"],
        docstring=bot.get("docstring") or "(no docstring)",
    )
    try:
        result = await get_client().complete(
            [{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.6,
        )
        text = (result.text or "").strip()
        return text or None
    except Exception as e:
        logger.debug("card generation failed for %s: %s", bot["id"], e)
        return None


async def regenerate(bot_id: str) -> dict[str, Any]:
    """Force-regenerate a single bot's card. Used by the API endpoint."""
    cache = _load_cache()
    target = next((b for b in _discover_bots() if b["id"] == bot_id), None)
    if not target:
        return {"ok": False, "error": f"unknown bot: {bot_id}"}
    text = await _generate_card(target)
    if not text:
        return {"ok": False, "error": "LM did not return a card"}
    cache[bot_id] = {
        "text": text,
        "generated_at": time.time(),
        "module_mtime": target["module_mtime"],
        "class_name": target["class_name"],
    }
    _save_cache(cache)
    bus.publish("personality.card_generated", "personality_cards", {"id": bot_id})
    return {"ok": True, "card": cache[bot_id]}


async def fill_missing() -> dict[str, Any]:
    """Generate cards for every bot that doesn't have a fresh one. Safe to
    call in the background — skips work that's already done."""
    cache = _load_cache()
    bots = _discover_bots()
    now = time.time()
    generated = 0
    for bot in bots:
        existing = cache.get(bot["id"])
        if existing:
            age = now - existing.get("generated_at", 0)
            mtime_changed = existing.get("module_mtime", 0) < bot["module_mtime"]
            if age < _REFRESH_AFTER_S and not mtime_changed:
                continue
        text = await _generate_card(bot)
        if not text:
            continue
        cache[bot["id"]] = {
            "text": text,
            "generated_at": now,
            "module_mtime": bot["module_mtime"],
            "class_name": bot["class_name"],
        }
        _save_cache(cache)
        generated += 1
        # Yield between LM calls so we don't hog the event loop.
        await asyncio.sleep(0.1)
    bus.publish("personality.fill_complete", "personality_cards",
                {"generated": generated, "total": len(bots)})
    return {"ok": True, "generated": generated, "total": len(bots)}


def all_cards() -> list[dict[str, Any]]:
    """Return one entry per discovered bot. Missing cards get a stub so the
    UI can always render the full roster.

    Each entry: {id, class_name, text, generated_at, stale}
    """
    cache = _load_cache()
    bots = _discover_bots()
    now = time.time()
    out = []
    for bot in bots:
        entry = cache.get(bot["id"])
        if entry:
            stale = (now - entry.get("generated_at", 0)) >= _REFRESH_AFTER_S \
                or entry.get("module_mtime", 0) < bot["module_mtime"]
            out.append({
                "id": bot["id"],
                "class_name": entry.get("class_name") or bot["class_name"],
                "text": entry.get("text", ""),
                "generated_at": entry.get("generated_at"),
                "stale": stale,
            })
        else:
            out.append({
                "id": bot["id"],
                "class_name": bot["class_name"],
                "text": "(no card yet — generate to introspect this bot)",
                "generated_at": None,
                "stale": True,
            })
    return out


# ─── D7.6 — Plugin coverage ────────────────────────────────────────────────────
# Plugins live in `plugins/<name>/plugin.{py,json}`. The manifest gives us
# a clean public-facing description + tool list. Cards are namespaced with a
# `plugin:` prefix in the cache so they share the file but don't collide
# with bot ids.

_PLUGIN_PREFIX = "plugin:"


def _discover_plugins() -> list[dict[str, Any]]:
    """Walk plugins/*/plugin.json and return [{id, plugin_name, description,
    tools, manifest_mtime}]. Plugins missing a manifest are skipped quietly."""
    out: list[dict[str, Any]] = []
    if not _PLUGINS_DIR.exists():
        return out
    for entry in sorted(_PLUGINS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        manifest = entry / "plugin.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text())
        except Exception as e:
            logger.debug("could not parse %s: %s", manifest, e)
            continue
        out.append({
            "id": f"{_PLUGIN_PREFIX}{entry.name}",
            "slug": entry.name,
            "plugin_name": data.get("name", entry.name),
            "description": (data.get("description") or "").strip(),
            "tools": [
                {"name": t.get("name", ""), "tier": t.get("tier", "?"),
                 "description": (t.get("description") or "").strip()}
                for t in (data.get("tools") or [])
            ],
            "manifest_mtime": manifest.stat().st_mtime,
        })
    return out


async def _generate_plugin_card(plugin: dict[str, Any]) -> Optional[str]:
    """One LM call to produce a plugin personality blurb."""
    from agent.core.lm_studio import get_client
    tool_lines = "\n".join(
        f"- {t['name']} [{t['tier']}]: {t['description']}"
        for t in plugin["tools"]
    ) or "(no tools declared)"
    prompt = _PLUGIN_PROMPT_TEMPLATE.format(
        name=plugin["plugin_name"],
        description=plugin["description"] or "(no description)",
        tools=tool_lines,
    )
    try:
        result = await get_client().complete(
            [{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.6,
        )
        text = (result.text or "").strip()
        return text or None
    except Exception as e:
        logger.debug("plugin card generation failed for %s: %s", plugin["id"], e)
        return None


async def regenerate_plugin(plugin_slug: str) -> dict[str, Any]:
    """Force-regenerate a single plugin's card. The slug is the directory
    name (no `plugin:` prefix); the cache key gets prefixed internally."""
    cache = _load_cache()
    target = next((p for p in _discover_plugins() if p["slug"] == plugin_slug), None)
    if not target:
        return {"ok": False, "error": f"unknown plugin: {plugin_slug}"}
    text = await _generate_plugin_card(target)
    if not text:
        return {"ok": False, "error": "LM did not return a card"}
    cache[target["id"]] = {
        "text": text,
        "generated_at": time.time(),
        "manifest_mtime": target["manifest_mtime"],
        "plugin_name": target["plugin_name"],
        "tool_count": len(target["tools"]),
    }
    _save_cache(cache)
    bus.publish("personality.card_generated", "personality_cards",
                {"id": target["id"]})
    return {"ok": True, "card": cache[target["id"]]}


async def fill_missing_plugins() -> dict[str, Any]:
    """Generate cards for every plugin without a fresh one. Mirrors
    fill_missing() for bots. Safe to call repeatedly."""
    cache = _load_cache()
    plugins = _discover_plugins()
    now = time.time()
    generated = 0
    for p in plugins:
        existing = cache.get(p["id"])
        if existing:
            age = now - existing.get("generated_at", 0)
            mtime_changed = existing.get("manifest_mtime", 0) < p["manifest_mtime"]
            if age < _REFRESH_AFTER_S and not mtime_changed:
                continue
        text = await _generate_plugin_card(p)
        if not text:
            continue
        cache[p["id"]] = {
            "text": text,
            "generated_at": now,
            "manifest_mtime": p["manifest_mtime"],
            "plugin_name": p["plugin_name"],
            "tool_count": len(p["tools"]),
        }
        _save_cache(cache)
        generated += 1
        await asyncio.sleep(0.1)
    bus.publish("personality.fill_complete", "personality_cards",
                {"scope": "plugins", "generated": generated, "total": len(plugins)})
    return {"ok": True, "generated": generated, "total": len(plugins)}


def all_plugin_cards() -> list[dict[str, Any]]:
    """One entry per discovered plugin. Missing cards get a stub so the
    UI can always render the full roster.

    Each entry: {id, slug, plugin_name, description, tool_count, tools,
                 text, generated_at, stale}
    """
    cache = _load_cache()
    plugins = _discover_plugins()
    now = time.time()
    out = []
    for p in plugins:
        entry = cache.get(p["id"])
        if entry:
            stale = (now - entry.get("generated_at", 0)) >= _REFRESH_AFTER_S \
                or entry.get("manifest_mtime", 0) < p["manifest_mtime"]
            out.append({
                "id": p["id"],
                "slug": p["slug"],
                "plugin_name": entry.get("plugin_name") or p["plugin_name"],
                "description": p["description"],
                "tool_count": len(p["tools"]),
                "tools": p["tools"],
                "text": entry.get("text", ""),
                "generated_at": entry.get("generated_at"),
                "stale": stale,
            })
        else:
            out.append({
                "id": p["id"],
                "slug": p["slug"],
                "plugin_name": p["plugin_name"],
                "description": p["description"],
                "tool_count": len(p["tools"]),
                "tools": p["tools"],
                "text": "(no card yet — generate to introspect this plugin)",
                "generated_at": None,
                "stale": True,
            })
    return out
