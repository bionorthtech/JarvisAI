"""/plugins, /marketplace, /adapters, /personality-cards router.

Plugin ecosystem surface: discover loaded plugins, install/uninstall via
local marketplace, manage external adapters (MQTT/webhooks), and serve
personality cards (D7) for each bot.

URLs unchanged from pre-split main.py.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(tags=["plugins"])


# ── External adapters ────────────────────────────────────────────

class AdapterCreate(BaseModel):
    id: str
    kind: str
    config: dict


class AdapterDispatch(BaseModel):
    action: str
    payload: dict


@router.get("/adapters/list")
async def adapters_list():
    from agent.core import adapters
    return {"adapters": adapters.registry.list(), "kinds": list(adapters.ADAPTER_KINDS)}


@router.post("/adapters/add")
async def adapters_add(body: AdapterCreate):
    from agent.core import adapters
    return adapters.registry.add(body.id, body.kind, body.config)


@router.delete("/adapters/{adapter_id}")
async def adapters_remove(adapter_id: str):
    from agent.core import adapters
    return adapters.registry.remove(adapter_id)


@router.post("/adapters/{adapter_id}/toggle")
async def adapters_toggle(adapter_id: str, enabled: bool = Query(...)):
    from agent.core import adapters
    return adapters.registry.toggle(adapter_id, enabled)


@router.post("/adapters/{adapter_id}/dispatch")
async def adapters_dispatch(adapter_id: str, body: AdapterDispatch):
    from agent.core import adapters
    return adapters.registry.dispatch(adapter_id, body.action, body.payload)


# ── Marketplace ──────────────────────────────────────────────────

@router.get("/marketplace/registry")
async def marketplace_registry():
    from agent.core import marketplace
    return marketplace.load_registry()


class InstallRequest(BaseModel):
    plugin_id: str
    source_path: str | None = None


@router.post("/marketplace/install")
async def marketplace_install(body: InstallRequest):
    from agent.core import marketplace
    return await asyncio.to_thread(marketplace.install, body.plugin_id, body.source_path)


@router.delete("/marketplace/{plugin_id}")
async def marketplace_uninstall(plugin_id: str):
    from agent.core import marketplace
    return await asyncio.to_thread(marketplace.uninstall, plugin_id)


# ── Loaded plugins ───────────────────────────────────────────────────────────

@router.get("/plugins")
async def list_plugins():
    from agent.core.tool_registry import registry as _tools
    schemas = _tools.get_tool_schemas()
    builtin = {"read_file","write_file","run_shell","list_directory",
               "take_screenshot","type_text","key_press","mouse_move","mouse_click"}
    plugins = [
        {
            "name": s["function"]["name"],
            "description": (s["function"].get("description") or "").split("\n")[0][:80],
        }
        for s in schemas
        if s["function"]["name"] not in builtin
    ]
    return {"plugins": plugins, "total_tools": len(schemas)}


@router.get("/plugins/overrides")
async def get_plugin_overrides():
    override_path = Path.home() / ".jarvis" / "plugin_overrides.json"
    if override_path.exists():
        return json.loads(override_path.read_text())
    return {}


# ── Personality cards (D7) ───────────────────────────────────────────────────

@router.get("/personality-cards")
async def personality_cards_list():
    """D7 — return one personality card per discovered bot. Missing cards
    come back as stubs so the UI always renders the full roster."""
    from agent.core import personality_cards
    return {"cards": personality_cards.all_cards()}


@router.post("/personality-cards/regenerate/{bot_id}")
async def personality_card_regenerate(bot_id: str):
    """D7 — force-regenerate a single bot's card via the LM."""
    from agent.core import personality_cards
    return await personality_cards.regenerate(bot_id)


@router.post("/personality-cards/fill")
async def personality_cards_fill():
    """D7 — generate cards for every bot that doesn't have a fresh one.
    Skips work already done; safe to call from a maintenance schedule."""
    from agent.core import personality_cards
    return await personality_cards.fill_missing()


# ── D7.6 — Plugin personality cards ──────────────────────────────────────────

@router.get("/plugin-cards")
async def plugin_cards_list():
    """D7.6 — one personality card per discovered plugin. Missing cards come
    back as stubs so the UI always renders the full plugin roster."""
    from agent.core import personality_cards
    return {"cards": personality_cards.all_plugin_cards()}


@router.post("/plugin-cards/regenerate/{plugin_slug}")
async def plugin_card_regenerate(plugin_slug: str):
    """D7.6 — force-regenerate a single plugin's card via the LM. The slug
    is the directory name under plugins/ (no namespace prefix)."""
    from agent.core import personality_cards
    return await personality_cards.regenerate_plugin(plugin_slug)


@router.post("/plugin-cards/fill")
async def plugin_cards_fill():
    """D7.6 — generate cards for every plugin without a fresh one. Skips
    work already done; safe to call from a maintenance schedule."""
    from agent.core import personality_cards
    return await personality_cards.fill_missing_plugins()
