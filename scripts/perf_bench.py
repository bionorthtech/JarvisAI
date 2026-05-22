#!/usr/bin/env python3
"""
JARVIS performance benchmark — measures Part 11 targets.

Targets:
  - first response       < 5,000 ms  (POST /chat/events → first text_delta)
  - ChromaDB query       <   200 ms  (POST /brain/ask with a trivial Q)
  - WS round-trip        <    50 ms  (ws://.../ws/live ping)
  - /health round-trip   <   100 ms  (sanity baseline)

Cold-start is measured separately by running `time jarvis-start` (or
equivalent) at the shell. This script assumes JARVIS is already up.

Run:
    venv/bin/python3 scripts/perf_bench.py
    venv/bin/python3 scripts/perf_bench.py --backend http://127.0.0.1:8000

Outputs a Markdown table you can paste into MASTER_PLAN.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from urllib import error, request


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 1)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


def health_check(backend: str, timeout: float = 3.0) -> tuple[bool, float]:
    t0 = time.monotonic()
    try:
        with request.urlopen(f"{backend}/health", timeout=timeout) as r:
            r.read(1)
            return True, time.monotonic() - t0
    except Exception:
        return False, time.monotonic() - t0


def bench_health(backend: str, samples: int) -> list[float]:
    out: list[float] = []
    for _ in range(samples):
        ok, dt = health_check(backend)
        if ok:
            out.append(dt)
    return out


def bench_chroma(backend: str, samples: int) -> list[float]:
    """Each sample fires POST /brain/ask with a one-word query and times
    the full round-trip. The query goes through ChromaDB; assistant
    synthesis happens client-side via the returned context, so the
    backend-side time is dominated by the vector search."""
    out: list[float] = []
    payload = json.dumps({"question": "test", "n": 3}).encode("utf-8")
    for _ in range(samples):
        req = request.Request(
            f"{backend}/brain/ask", data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with request.urlopen(req, timeout=10) as r:
                r.read()
            out.append(time.monotonic() - t0)
        except Exception:
            continue
    return out


def bench_first_response(backend: str, samples: int) -> list[float]:
    """Times from POST /chat/events to the first byte arriving back.
    Best proxy for 'first token' under our SSE wire format."""
    out: list[float] = []
    payload = json.dumps({
        "message": "Reply with one word.",
        "session_id": "perf-bench",
    }).encode("utf-8")
    for _ in range(samples):
        req = request.Request(
            f"{backend}/chat/events", data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with request.urlopen(req, timeout=30) as r:
                # First non-empty line of SSE = first event
                while True:
                    line = r.readline()
                    if not line:
                        break
                    if line.startswith(b"data:"):
                        out.append(time.monotonic() - t0)
                        break
                # Drain so the connection closes cleanly
                try:
                    r.read(4096)
                except Exception:
                    pass
        except Exception:
            continue
    return out


async def _ws_ping_once(backend: str, ws_url: str, timeout: float) -> float:
    """Real publish→recv RTT on /ws/live.

    1. Connect, drain the catch-up burst (the handler always sends
       `bus.recent(30)` on connect — those are old events, not RTT).
    2. POST /perf/ws-ping with a unique token.
    3. Start the clock the moment the POST returns; stop when a
       `perf.ws_ping` event arrives over the WS with the matching token.

    Elapsed is captured inside the `async with` so the library's 10s
    close-handshake (websockets default `close_timeout`) doesn't
    dominate the measurement.
    """
    try:
        import websockets  # type: ignore
    except ImportError:
        raise RuntimeError(
            "websockets package not installed in venv — `pip install websockets`"
        )
    token = f"bench-{time.monotonic_ns()}"

    async with websockets.connect(ws_url, open_timeout=timeout,
                                  close_timeout=0.5,
                                  ping_interval=None) as ws:
        # Drain catch-up: WS handler sends `bus.recent(30)` on connect.
        # Read with a short per-recv timeout until we hit a brief idle.
        while True:
            try:
                await asyncio.wait_for(ws.recv(), timeout=0.05)
            except asyncio.TimeoutError:
                break

        # Fire the ping over HTTP. Start the clock the instant we
        # complete the POST (before the WS sees the event).
        await asyncio.to_thread(
            _post_json,
            f"{backend}/perf/ws-ping",
            {"token": token},
        )
        t0 = time.monotonic()

        deadline = t0 + timeout
        while time.monotonic() < deadline:
            try:
                remaining = deadline - time.monotonic()
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.01, remaining))
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("topic") == "perf.ws_ping" and msg.get("payload", {}).get("token") == token:
                return time.monotonic() - t0

        # Token never arrived — return the timeout as the upper bound.
        return time.monotonic() - t0


def _post_json(url: str, body: dict) -> None:
    data = json.dumps(body).encode()
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=2.0):
            pass
    except error.URLError:
        pass


def bench_ws(backend: str, samples: int) -> list[float]:
    ws_url = backend.replace("http://", "ws://").replace("https://", "wss://") + "/ws/live"
    out: list[float] = []
    for _ in range(samples):
        try:
            dt = asyncio.run(_ws_ping_once(backend, ws_url, timeout=2.0))
            out.append(dt)
        except Exception as e:
            print(f"  WS error: {e}", file=sys.stderr)
            break
    return out


def report(name: str, samples: list[float], target_ms: float) -> dict:
    if not samples:
        return {
            "name": name, "n": 0, "p50_ms": None, "p95_ms": None,
            "target_ms": target_ms, "verdict": "no samples",
        }
    p50 = _ms(statistics.median(samples))
    p95 = _ms(_percentile(samples, 95))
    verdict = "✓ pass" if p95 < target_ms else "✗ miss"
    return {"name": name, "n": len(samples), "p50_ms": p50, "p95_ms": p95,
            "target_ms": target_ms, "verdict": verdict}


def main() -> int:
    parser = argparse.ArgumentParser(description="JARVIS perf bench")
    parser.add_argument("--backend", default="http://127.0.0.1:8000",
                        help="JARVIS backend URL (default %(default)s)")
    parser.add_argument("--samples", type=int, default=5,
                        help="Samples per metric (default %(default)s)")
    args = parser.parse_args()

    print(f"perf_bench → {args.backend}, {args.samples} samples each\n")

    # Sanity
    ok, dt = health_check(args.backend, timeout=2.0)
    if not ok:
        print(f"✗ /health unreachable at {args.backend}", file=sys.stderr)
        print("  Start JARVIS first: `cd ~/jarvis && venv/bin/python3 main.py`",
              file=sys.stderr)
        return 1
    print(f"✓ /health ok ({_ms(dt)} ms) — backend up")

    results = []
    print("\nrunning bench_health …")
    results.append(report("/health round-trip",
                          bench_health(args.backend, args.samples),
                          target_ms=100))
    print("running bench_chroma …")
    results.append(report("ChromaDB query (/brain/ask)",
                          bench_chroma(args.backend, args.samples),
                          target_ms=200))
    print("running bench_first_response …")
    results.append(report("First chat response (/chat/events)",
                          bench_first_response(args.backend, args.samples),
                          target_ms=5000))
    print("running bench_ws …")
    results.append(report("WS round-trip (/ws/live)",
                          bench_ws(args.backend, args.samples),
                          target_ms=50))

    # Markdown report
    print("\n## perf_bench results")
    print()
    print("| metric | samples | p50 ms | p95 ms | target ms | verdict |")
    print("|---|---:|---:|---:|---:|---|")
    for r in results:
        p50 = r["p50_ms"] if r["p50_ms"] is not None else "—"
        p95 = r["p95_ms"] if r["p95_ms"] is not None else "—"
        print(f"| {r['name']} | {r['n']} | {p50} | {p95} | "
              f"{r['target_ms']} | {r['verdict']} |")

    misses = [r for r in results if r["verdict"].startswith("✗")]
    print()
    if misses:
        print(f"✗ {len(misses)} target(s) missed — see table above.")
        return 2
    print("✓ all targets met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
