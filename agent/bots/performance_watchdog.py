"""
Performance Watchdog Bot

Tracks key performance metrics over time. Alerts on regressions. Auto-tunes
model routing based on observed real-world data.

Trigger: every 6 hours (lightweight) + weekly full benchmark + on-demand.
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from statistics import median
from typing import Any

from agent.bots import write_report
from agent.core import bus

METRICS_FILE = Path.home() / ".jarvis" / "performance_metrics.jsonl"
BASELINE_FILE = Path.home() / ".jarvis" / "performance_baseline.json"


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = int(len(s) * p)
    return s[min(k, len(s) - 1)]


class PerformanceWatchdog:
    min_autonomy_level: int = 1
    wake_conditions: list[str] = ["perf.regression", "perf.spike"]

    def __init__(self):
        self._lm_latencies: deque[float] = deque(maxlen=200)
        self._chroma_latencies: deque[float] = deque(maxlen=500)
        self._ws_latencies: deque[float] = deque(maxlen=200)
        self._baseline: dict[str, Any] = self._load_baseline()

    def _load_baseline(self) -> dict[str, Any]:
        try:
            if BASELINE_FILE.exists():
                return json.loads(BASELINE_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save_baseline(self, snap: dict[str, Any]) -> None:
        try:
            BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
            BASELINE_FILE.write_text(json.dumps(snap, indent=2))
        except Exception:
            pass

    def record_lm_latency(self, ms: float) -> None:
        self._lm_latencies.append(float(ms))

    def record_chroma_latency(self, ms: float) -> None:
        self._chroma_latencies.append(float(ms))

    def record_ws_latency(self, ms: float) -> None:
        self._ws_latencies.append(float(ms))

    def run(self) -> dict[str, Any]:
        t0 = time.time()
        snap = self._collect_metrics()

        regressions = self._detect_regressions(snap)

        report = {
            "ts": t0,
            "duration_s": round(time.time() - t0, 2),
            "metrics": snap,
            "regressions": regressions,
        }

        try:
            METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with METRICS_FILE.open("a") as f:
                f.write(json.dumps({"ts": t0, **snap}) + "\n")
        except Exception:
            pass

        if not self._baseline:
            self._save_baseline(snap)
            self._baseline = snap

        report["lm_p95_ms"] = snap["lm_studio"]["p95_ms"]
        report["chroma_p95_ms"] = snap["chromadb"]["p95_ms"]

        bus.publish("performance_watchdog.report", "PerformanceWatchdog", {
            "regressions": len(regressions),
            "lm_p95_ms": report["lm_p95_ms"],
            "chroma_p95_ms": report["chroma_p95_ms"],
        })
        write_report("performance_watchdog", report)

        for r in regressions:
            bus.publish("thought.broadcast", "PerformanceWatchdog", {
                "thought": (
                    f"Performance regression: {r['metric']} {r['current']:.1f} "
                    f"vs baseline {r['baseline']:.1f} "
                    f"({r['delta_pct']:+.0f}%)."
                ),
                "priority": "medium",
            })

        return report

    def _collect_metrics(self) -> dict[str, Any]:
        import psutil
        proc_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()

        return {
            "lm_studio": {
                "samples": len(self._lm_latencies),
                "p50_ms": round(median(self._lm_latencies), 1) if self._lm_latencies else 0.0,
                "p95_ms": round(_percentile(list(self._lm_latencies), 0.95), 1),
                "p99_ms": round(_percentile(list(self._lm_latencies), 0.99), 1),
            },
            "chromadb": {
                "samples": len(self._chroma_latencies),
                "p50_ms": round(median(self._chroma_latencies), 1) if self._chroma_latencies else 0.0,
                "p95_ms": round(_percentile(list(self._chroma_latencies), 0.95), 1),
                "p99_ms": round(_percentile(list(self._chroma_latencies), 0.99), 1),
            },
            "websocket": {
                "samples": len(self._ws_latencies),
                "p50_ms": round(median(self._ws_latencies), 1) if self._ws_latencies else 0.0,
                "p95_ms": round(_percentile(list(self._ws_latencies), 0.95), 1),
            },
            "system": {
                "process_rss_mb": proc_mb,
                "cpu_pct": cpu,
                "ram_used_pct": ram.percent,
            },
        }

    def _detect_regressions(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self._baseline:
            return out

        comparisons = [
            ("lm_studio.p95_ms", 0.20),
            ("chromadb.p95_ms", 0.50),
            ("websocket.p95_ms", 0.50),
            ("system.process_rss_mb", 0.50),
        ]
        for path, threshold in comparisons:
            cur = self._dig(snap, path)
            base = self._dig(self._baseline, path)
            if cur is None or base is None or base == 0:
                continue
            delta = (cur - base) / base
            if delta >= threshold:
                out.append({
                    "metric": path,
                    "current": cur,
                    "baseline": base,
                    "delta_pct": delta * 100,
                })
        return out

    @staticmethod
    def _dig(d: dict[str, Any], path: str) -> Any:
        cur: Any = d
        for part in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur


watchdog = PerformanceWatchdog()
