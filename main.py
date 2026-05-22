import os

# 3A — offline guard. Must be set BEFORE chromadb / sentence-transformers
# / huggingface_hub imports, otherwise those libs will try to contact
# huggingface.co on first embedding call. Respects the user config —
# if internet_access is True they're cleared, allowing first-time model
# download.
try:
    from agent.core.config import config as _cfg
    if not _cfg.security.internet_access:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
except Exception:
    # Fail closed — assume offline if config can't be loaded.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import pty
import json
import fcntl
import struct
import asyncio
import termios
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import agent.core.drives as drives
import agent.core.bus as bus
from agent.core.autonomy import autonomy as autonomy_daemon


async def _prewarm_lm_studio():
    """G3.1 — fire one tiny completion against the default model so weights
    are loaded + KV cache warm before the first real user request. Best-
    effort: never raises, never blocks startup.
    """
    import time as _t
    await asyncio.sleep(2.0)   # let the server bind first
    try:
        from agent.core.lm_studio import get_client
        client = get_client()
        status = await client.check_connection()
        if not status.reachable:
            bus.publish("lm.prewarm_skipped", "lifespan",
                        {"reason": "lm_studio unreachable"})
            return
        model = (status.models or [None])[0]
        t0 = _t.monotonic()
        await client.complete(
            [{"role": "user", "content": "ok"}],
            model=model,
        )
        latency_ms = int((_t.monotonic() - t0) * 1000)
        bus.publish("lm.prewarmed", "lifespan", {
            "model": model, "latency_ms": latency_ms,
        })
    except Exception as e:
        bus.publish("lm.prewarm_skipped", "lifespan", {"reason": str(e)[:120]})


@asynccontextmanager
async def lifespan(app: FastAPI):
    # All periodic work — drives, system.metrics, plugin.heartbeat,
    # personality_traits.refresh, brain_co_ownership.scan, style_learner
    # distill, scheduled bots — runs through the autonomy periodic
    # registry. We just need to load drives state here since the registry
    # handler calls _tick_once directly, not the full tick_loop wrapper.
    drives.load_state()
    await autonomy_daemon.start()
    asyncio.create_task(_prewarm_lm_studio())   # G3.1 — fire-and-forget
    # 2A — warm ChromaDB cold-cache so the first user query doesn't pay
    # the embedding-model-load latency. Cheap, fire-and-forget.
    async def _prewarm_chroma():
        from agent.core import memory as _mem
        await asyncio.to_thread(_mem.prewarm)
    asyncio.create_task(_prewarm_chroma())

    # G6.1 — tail LM Studio's server log, publish `lm.progress` events.
    # File-watcher (not interval-driven); stays as a standalone task.
    from agent.core import lm_progress
    asyncio.create_task(lm_progress.tail_loop())

    # C14.1 — closed-loop skill distillation. Subscribes to the bus and
    # turns every successful `agent.completed` into a reusable skill
    # under ~/jarvis/memory/skills/. Opportunistic — LM unavailability
    # is silently tolerated.
    from agent.aliveness import skill_distiller
    asyncio.create_task(skill_distiller.run_subscriber_loop())

    # Wire drive threshold → SSE broadcast AND bus
    _notification_queue: asyncio.Queue = asyncio.Queue()
    app.state.notifications = _notification_queue

    def _drive_notify(drive: str, level: float) -> None:
        payload = {
            "type": "drive_alert",
            "drive": drive,
            "level": round(level, 2),
            "message": f"{drive} drive at {level:.0%} — JARVIS wants to act",
        }
        # Legacy SSE queue
        try:
            _notification_queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass
        # New bus
        bus.publish("system.drive_alert", "drives", payload)

    drives.register_notify(_drive_notify)
    yield


app = FastAPI(title="JARVIS", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "tauri://localhost", "http://127.0.0.1:1420"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── B1 Routers ───────────────────────────────────────────────────────────────
# Each per-domain router lives under agent/api/. URLs are unchanged from the
# pre-split monolith — only the file location moved. See agent/api/__init__.py.

from agent.api.bots      import router as bots_router       # noqa: E402
from agent.api.voice     import router as voice_router      # noqa: E402
from agent.api.agents    import router as agents_router     # noqa: E402
from agent.api.analytics import router as analytics_router  # noqa: E402
from agent.api.plugins   import router as plugins_router    # noqa: E402
from agent.api.autonomy  import router as autonomy_router   # noqa: E402
from agent.api.chat      import router as chat_router       # noqa: E402
from agent.api.aliveness import router as aliveness_router  # noqa: E402
from agent.api.brain     import router as brain_router      # noqa: E402
from agent.api.system    import router as system_router     # noqa: E402
from agent.api.feedback  import router as feedback_router   # noqa: E402

app.include_router(bots_router)
app.include_router(voice_router)
app.include_router(agents_router)
app.include_router(analytics_router)
app.include_router(plugins_router)
app.include_router(autonomy_router)
app.include_router(chat_router)
app.include_router(aliveness_router)
app.include_router(brain_router)
app.include_router(system_router)
app.include_router(feedback_router)


# ── WebSocket PTY Terminal ───────────────────────────────────────────────────

@app.websocket("/ws/terminal")
async def terminal_ws(ws: WebSocket):
    await ws.accept()

    master_fd, slave_fd = pty.openpty()

    proc = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "--login",
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    loop = asyncio.get_running_loop()
    output_queue: asyncio.Queue = asyncio.Queue()
    done = asyncio.Event()

    def _on_pty_readable():
        try:
            data = os.read(master_fd, 4096)
            if data:
                loop.call_soon_threadsafe(output_queue.put_nowait, data)
            else:
                loop.call_soon_threadsafe(done.set)
        except OSError:
            loop.call_soon_threadsafe(done.set)

    loop.add_reader(master_fd, _on_pty_readable)

    async def _send_output():
        while not done.is_set():
            try:
                data = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                await ws.send_bytes(data)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

    sender = asyncio.create_task(_send_output())

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            text = msg.get("text")
            if text:
                try:
                    ctrl = json.loads(text)
                    if ctrl.get("type") == "resize":
                        rows = int(ctrl.get("rows", 24))
                        cols = int(ctrl.get("cols", 80))
                        fcntl.ioctl(
                            master_fd,
                            termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0),
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    os.write(master_fd, text.encode())

            raw = msg.get("bytes")
            if raw:
                os.write(master_fd, raw)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        done.set()
        loop.remove_reader(master_fd)
        sender.cancel()
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass


# ── WebSocket live dashboard ─────────────────────────────────────────────────

@app.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    """
    Live dashboard WebSocket. Streams all bus events to the client.
    Sends last 30 messages on connect as catch-up, then pushes live events.
    Heartbeat ping every 25 s keeps the connection alive through proxies.
    """
    await ws.accept()
    q = bus.subscribe()
    try:
        # Catch-up: send recent history so the dashboard isn't empty on load
        for msg in reversed(bus.recent(30)):
            await ws.send_json(msg)

        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25.0)
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                await ws.send_json({"topic": "ping", "ts": __import__("time").time()})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        bus.unsubscribe(q)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
