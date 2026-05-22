"""/chat, /confirm router.

The conversational surface: blocking chat, SSE event stream (the main
agent path), pure text stream, and DANGER/CRITICAL tool confirmation +
impact preview.

URLs unchanged from pre-split main.py.
"""
from __future__ import annotations
import json
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.core.session import manager
from agent.core.confirmations import confirm_registry, get_tier, preview_impact
from agent.core.autonomy import autonomy as _autonomy

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    model: Optional[str] = None
    project: Optional[str] = None


class ConfirmResponse(BaseModel):
    approved: bool


async def _get_models() -> list[str]:
    from agent.core.lm_studio import get_client
    try:
        status = await get_client().check_connection()
        return status.models
    except Exception:
        return []


# ── Chat (blocking) ──────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(request: ChatRequest):
    _autonomy.record_user_activity("chat")  # B6.7 reset idle clock
    session = manager.get_or_create(request.session_id)
    if request.model:
        session.preferred_model = request.model
    models = await _get_models()
    response = await session.process_request(request.message, models)
    return {"response": response, "session_id": session.session_id}


# ── Chat Events (SSE — main agent path) ──────────────────────────────────────

@router.post("/chat/events")
async def chat_events(request: ChatRequest):
    _autonomy.record_user_activity("chat/events")  # B6.7
    session = manager.get_or_create(request.session_id)
    if request.model:
        session.preferred_model = request.model
    if request.project:
        session.project = request.project
    models = await _get_models()

    async def generate():
        try:
            async for event in session.process_events(request.message, models):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Chat Stream (pure text streaming, no tools) ──────────────────────────────

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    _autonomy.record_user_activity("chat/stream")  # B6.7
    session = manager.get_or_create(request.session_id)
    if request.model:
        session.preferred_model = request.model
    models = await _get_models()

    async def generate():
        try:
            async for chunk in session.process_stream(request.message, models):
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Confirmation (DANGER/CRITICAL tool approval) ─────────────────────────────
# IMPORTANT: /confirm/preview MUST be declared before /confirm/{confirm_id} so
# FastAPI matches the literal path first. Otherwise "preview" gets captured as
# {confirm_id} and the request body fails schema validation.

class PreviewRequest(BaseModel):
    tool_name: str
    args: dict


@router.post("/confirm/preview")
async def confirm_preview(body: PreviewRequest):
    """Dry-run / impact preview for DANGER/CRITICAL tier actions.
    Returns a structured payload the UI confirmation modal renders before
    asking the user to approve. Does NOT execute anything."""
    tier = get_tier(body.tool_name)
    impact = preview_impact(body.tool_name, body.args)
    return {
        "tool_name": body.tool_name,
        "tier": tier,
        "needs_confirm": tier in ("DANGER", "CRITICAL"),
        "impact": impact,
    }


@router.post("/confirm/{confirm_id}")
async def resolve_confirm(confirm_id: str, body: ConfirmResponse):
    ok = confirm_registry.respond(confirm_id, body.approved)
    if not ok:
        return {"error": "confirmation not found or already resolved"}
    return {"ok": True}
