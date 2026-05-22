"""
Voice Interface

Wake-word + STT + TTS pipeline using fully local, offline binaries:

  - Wake word: openWakeWord (CPU, ~50 MB)
  - STT:       whisper.cpp `main` binary
  - TTS:       Piper

This module is BINARY-AVAILABILITY-AWARE: it does not assume any of the
above are installed. `status()` reports what's present and provides
copy-paste install commands for what's missing.

Toggling on requires all three present + the user's explicit consent
(persisted to ~/.jarvis/voice.json).

The actual recording pipeline runs as a subprocess sidecar so audio
buffers never enter the FastAPI process.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import bus

VOICE_STATE_FILE = Path.home() / ".jarvis" / "voice.json"
PIPER_VOICE_DIR = Path.home() / ".jarvis" / "piper_voices"
WHISPER_MODEL_DIR = Path.home() / ".jarvis" / "whisper_models"


_DEFAULT_STATE = {
    "enabled": False,
    "wake_word": "hey jarvis",
    "stt_model": "base.en",
    "tts_voice": "en_US-amy-low",
    "stt_binary": "",
    "tts_binary": "",
    "wake_binary": "",
}


def _load() -> dict[str, Any]:
    try:
        if VOICE_STATE_FILE.exists():
            return {**_DEFAULT_STATE, **json.loads(VOICE_STATE_FILE.read_text())}
    except Exception:
        pass
    return dict(_DEFAULT_STATE)


def _save(state: dict[str, Any]) -> None:
    try:
        VOICE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        VOICE_STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def _binary_present(name: str) -> str:
    return shutil.which(name) or ""


def status() -> dict[str, Any]:
    state = _load()
    whisper_bin = state.get("stt_binary") or _binary_present("whisper") or _binary_present("main")
    piper_bin = state.get("tts_binary") or _binary_present("piper")
    oww_present = _binary_present("openwakeword") or False

    voice_models = list(PIPER_VOICE_DIR.glob("*.onnx")) if PIPER_VOICE_DIR.exists() else []
    whisper_models = list(WHISPER_MODEL_DIR.glob("*.bin")) if WHISPER_MODEL_DIR.exists() else []

    install_hints = {
        "whisper.cpp": (
            "git clone https://github.com/ggerganov/whisper.cpp ~/whisper.cpp && "
            "cd ~/whisper.cpp && make && "
            f"mkdir -p {WHISPER_MODEL_DIR} && "
            f"./models/download-ggml-model.sh {state['stt_model']} && "
            f"cp models/ggml-{state['stt_model']}.bin {WHISPER_MODEL_DIR}/"
        ),
        "piper": (
            "pip install piper-tts && "
            f"mkdir -p {PIPER_VOICE_DIR} && "
            f"python -m piper.download_voices {state['tts_voice']} -d {PIPER_VOICE_DIR}"
        ),
        "openwakeword": "pip install openwakeword",
    }

    components = {
        "whisper.cpp": {
            "binary": whisper_bin,
            "installed": bool(whisper_bin),
            "models": [str(m.name) for m in whisper_models],
            "install_hint": install_hints["whisper.cpp"],
        },
        "piper": {
            "binary": piper_bin,
            "installed": bool(piper_bin),
            "voices": [str(v.name) for v in voice_models],
            "install_hint": install_hints["piper"],
        },
        "openwakeword": {
            "installed": bool(oww_present),
            "install_hint": install_hints["openwakeword"],
        },
    }

    fully_installed = all(c["installed"] for c in components.values())

    return {
        "enabled": bool(state["enabled"] and fully_installed),
        "user_toggle": bool(state["enabled"]),
        "fully_installed": fully_installed,
        "wake_word": state["wake_word"],
        "stt_model": state["stt_model"],
        "tts_voice": state["tts_voice"],
        "components": components,
    }


def set_enabled(enabled: bool) -> dict[str, Any]:
    state = _load()
    state["enabled"] = bool(enabled)
    _save(state)
    bus.publish("voice.toggled", "voice", {"enabled": state["enabled"]})
    return status()


def configure(*, wake_word: str | None = None, stt_model: str | None = None,
              tts_voice: str | None = None) -> dict[str, Any]:
    state = _load()
    if wake_word:
        state["wake_word"] = wake_word
    if stt_model:
        state["stt_model"] = stt_model
    if tts_voice:
        state["tts_voice"] = tts_voice
    _save(state)
    return status()


def synthesize(text: str) -> dict[str, Any]:
    """One-shot TTS: text → wav file path."""
    s = _load()
    piper = s.get("tts_binary") or _binary_present("piper")
    if not piper:
        return {"error": "piper not installed", "install_hint": status()["components"]["piper"]["install_hint"]}
    voice_path = PIPER_VOICE_DIR / f"{s['tts_voice']}.onnx"
    if not voice_path.exists():
        return {"error": f"voice model missing: {voice_path}"}

    out_path = Path.home() / ".jarvis" / f"tts_{int(time.time())}.wav"
    try:
        proc = subprocess.run(
            [piper, "--model", str(voice_path), "--output_file", str(out_path)],
            input=text.encode("utf-8"),
            capture_output=True, timeout=30,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr.decode("utf-8", errors="replace")[:300]}
        bus.publish("voice.synthesized", "voice", {"chars": len(text), "path": str(out_path)})
        return {"ok": True, "path": str(out_path), "bytes": out_path.stat().st_size}
    except Exception as e:
        return {"error": str(e)[:300]}


def transcribe(wav_path: str) -> dict[str, Any]:
    """One-shot STT: wav file → text."""
    s = _load()
    whisper = s.get("stt_binary") or _binary_present("whisper") or _binary_present("main")
    if not whisper:
        return {"error": "whisper not installed", "install_hint": status()["components"]["whisper.cpp"]["install_hint"]}
    model_path = WHISPER_MODEL_DIR / f"ggml-{s['stt_model']}.bin"
    if not model_path.exists():
        return {"error": f"whisper model missing: {model_path}"}

    try:
        proc = subprocess.run(
            [whisper, "-m", str(model_path), "-f", wav_path, "-otxt"],
            capture_output=True, timeout=60,
        )
        text = proc.stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return {"error": proc.stderr.decode("utf-8", errors="replace")[:300]}
        bus.publish("voice.transcribed", "voice", {"chars": len(text)})
        return {"ok": True, "text": text}
    except Exception as e:
        return {"error": str(e)[:300]}
