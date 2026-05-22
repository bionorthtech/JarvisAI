"""
Analytics aggregator for the /analytics endpoint.
Collects: per-request latency history, token budget, GPU stats, model info.
"""
import asyncio
import logging
import subprocess
import time
from collections import deque

logger = logging.getLogger("jarvis.analytics")

# Rolling window of latency samples (ms)
_LATENCY_HISTORY: deque = deque(maxlen=20)
_TOKEN_HISTORY:   deque = deque(maxlen=20)   # (session_id, tokens_used)
_SESSION_TOKENS: dict[str, int] = {}          # session_id → total tokens this session

# Cached LM status — updated by update_lm_cache() so analytics never stalls waiting
_LM_CACHE: dict = {}
_LM_CACHE_TS: float = 0.0
_LM_CACHE_TTL = 30.0  # seconds


def update_lm_cache(connected: bool, models: list, latency_ms: float) -> None:
    """Call this from /health so analytics always has a fresh reading."""
    global _LM_CACHE, _LM_CACHE_TS
    _LM_CACHE = {"connected": connected, "models": models, "latency_ms": round(latency_ms, 1)}
    _LM_CACHE_TS = time.monotonic()


def record_latency(ms: float) -> None:
    _LATENCY_HISTORY.append({"t": round(time.time()), "ms": round(ms, 1)})


def record_tokens(session_id: str, tokens: int) -> None:
    _SESSION_TOKENS[session_id] = _SESSION_TOKENS.get(session_id, 0) + tokens
    _TOKEN_HISTORY.append({"t": round(time.time()), "session": session_id, "n": tokens})


def _gpu_stats() -> dict:
    """Pull VRAM and GPU utilization via nvidia-smi."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            return {
                "name": parts[0],
                "vram_used_mb": int(parts[1]),
                "vram_total_mb": int(parts[2]),
                "utilization_pct": int(parts[3]),
                "temp_c": int(parts[4]),
            }
    except Exception as e:
        logger.debug("nvidia-smi failed: %s", e)
    return {}


def _cpu_ram() -> dict:
    try:
        import psutil
        return {
            "cpu_pct": psutil.cpu_percent(interval=0.2),
            "ram_used_gb": round(psutil.virtual_memory().used / 1e9, 1),
            "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
        }
    except ImportError:
        pass
    # Fallback: read /proc
    try:
        with open("/proc/meminfo") as f:
            lines = {l.split(":")[0]: int(l.split()[1]) for l in f if ":" in l}
        total = lines.get("MemTotal", 0)
        avail = lines.get("MemAvailable", 0)
        return {
            "ram_used_gb": round((total - avail) / 1e6, 1),
            "ram_total_gb": round(total / 1e6, 1),
        }
    except Exception:
        return {}


async def get_analytics(lm_client=None) -> dict:
    """Assemble full analytics snapshot."""
    gpu = await asyncio.to_thread(_gpu_stats)
    cpu_ram = await asyncio.to_thread(_cpu_ram)

    # LM Studio status — use cache if fresh, otherwise do a live check
    lm_info: dict = dict(_LM_CACHE) if _LM_CACHE else {}
    cache_age = time.monotonic() - _LM_CACHE_TS
    if lm_client and (not _LM_CACHE or cache_age > _LM_CACHE_TTL):
        try:
            status = await lm_client.check_connection()
            lm_info = {
                "connected": status.reachable,
                "models": status.models,
                "latency_ms": round(status.latency_ms, 1),
            }
            update_lm_cache(status.reachable, status.models, status.latency_ms)
        except Exception:
            pass

    latencies = list(_LATENCY_HISTORY)
    avg_latency = round(sum(x["ms"] for x in latencies) / len(latencies), 1) if latencies else 0

    return {
        "lm": lm_info,
        "latency": {
            "history": latencies,
            "avg_ms": avg_latency,
            "p95_ms": round(sorted(x["ms"] for x in latencies)[int(len(latencies)*0.95)] if len(latencies) >= 2 else avg_latency, 1),
        },
        "tokens": {
            "history": list(_TOKEN_HISTORY),
            "sessions": _SESSION_TOKENS,
            "total": sum(_SESSION_TOKENS.values()),
        },
        "gpu": gpu,
        "system": cpu_ram,
    }
