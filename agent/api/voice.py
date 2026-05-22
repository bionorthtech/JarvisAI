"""/voice router (voice interface).

URLs unchanged from pre-split main.py. Voice sidecar (whisper.cpp + piper +
openwakeword) is consumed lazily — heavy imports happen inside handlers so
the backend cold-starts without the binaries installed.
"""
from __future__ import annotations
import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(tags=["voice"])


@router.get("/voice/status")
async def voice_status():
    from agent.core import voice
    return voice.status()


@router.post("/voice/enabled")
async def voice_set_enabled(enabled: bool = Query(...)):
    from agent.core import voice
    return voice.set_enabled(enabled)


class VoiceConfig(BaseModel):
    wake_word: str | None = None
    stt_model: str | None = None
    tts_voice: str | None = None


@router.post("/voice/configure")
async def voice_configure(body: VoiceConfig):
    from agent.core import voice
    return voice.configure(
        wake_word=body.wake_word, stt_model=body.stt_model, tts_voice=body.tts_voice,
    )


class TTSRequest(BaseModel):
    text: str


@router.post("/voice/tts")
async def voice_tts(body: TTSRequest):
    from agent.core import voice
    return await asyncio.to_thread(voice.synthesize, body.text)


class STTRequest(BaseModel):
    wav_path: str


@router.post("/voice/stt")
async def voice_stt(body: STTRequest):
    from agent.core import voice
    return await asyncio.to_thread(voice.transcribe, body.wav_path)
