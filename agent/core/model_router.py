"""
Model Routing with Performance Metrics (1.8)

Routes tasks to the best available local model based on:
  - Task complexity score (keyword heuristics)
  - Available models reported by LM Studio
  - Historical performance data (tokens/sec, success rate)

Performance data is tracked per model+complexity_tier combo and
persisted to ~/.jarvis/model_perf.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import bus

_PERF_FILE = Path.home() / ".jarvis" / "model_perf.json"

# Complexity tiers
TIER_SIMPLE = "simple"    # chat, quick Q&A, single-step
TIER_MEDIUM = "medium"    # code snippets, research, multi-step
TIER_COMPLEX = "complex"  # multi-file refactor, analysis, planning

# Keywords that elevate complexity tier
_COMPLEX_KEYWORDS = {
    "refactor", "architecture", "analyze", "ast", "tree-sitter",
    "multi-file", "design", "system", "optimize", "concurrent", "async",
}
_MEDIUM_KEYWORDS = {
    "write code", "implement", "debug", "search", "summarize",
    "explain", "review", "test", "fix", "update",
}


def score_complexity(goal: str) -> str:
    """Return TIER_SIMPLE / TIER_MEDIUM / TIER_COMPLEX from goal text."""
    lower = goal.lower()
    if any(kw in lower for kw in _COMPLEX_KEYWORDS):
        return TIER_COMPLEX
    if any(kw in lower for kw in _MEDIUM_KEYWORDS):
        return TIER_MEDIUM
    return TIER_SIMPLE


class ModelRouter:
    def __init__(self):
        self._perf: dict[str, dict] = self._load()

    def _load(self) -> dict:
        try:
            if _PERF_FILE.exists():
                return json.loads(_PERF_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            _PERF_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PERF_FILE.write_text(json.dumps(self._perf, indent=2))
        except Exception:
            pass

    def _key(self, model_id: str, tier: str) -> str:
        return f"{model_id}::{tier}"

    def record(self, model_id: str, tier: str, tokens: int, duration_s: float, success: bool):
        """Record a completed completion for learning."""
        key = self._key(model_id, tier)
        rec = self._perf.setdefault(key, {
            "calls": 0, "success": 0, "total_tokens": 0, "total_s": 0.0,
        })
        rec["calls"] += 1
        rec["total_tokens"] += tokens
        rec["total_s"] += duration_s
        if success:
            rec["success"] += 1
        self._save()
        bus.publish("model_router.recorded", "router", {
            "model": model_id, "tier": tier,
            "tps": round(tokens / max(duration_s, 0.1), 1),
            "success_rate": round(rec["success"] / rec["calls"], 3),
        })

    def best_model(self, goal: str, available_models: list[str]) -> str:
        """Pick the best model for this goal from the available list."""
        if not available_models:
            return "local-model"

        tier = score_complexity(goal)

        # Score each model: primary key = success_rate, tiebreak = tokens/sec
        scored = []
        for m in available_models:
            key = self._key(m, tier)
            rec = self._perf.get(key, {})
            calls = rec.get("calls", 0)
            if calls == 0:
                # No data yet — prefer shorter model IDs (usually lighter/faster)
                scored.append((0.5, 0.0, -len(m), m))
            else:
                sr = rec["success"] / calls
                tps = rec["total_tokens"] / max(rec["total_s"], 0.1)
                scored.append((sr, tps, 0, m))

        scored.sort(reverse=True)
        best = scored[0][-1]
        bus.publish("model_router.decision", "router", {
            "goal_preview": goal[:100], "tier": tier,
            "chosen": best, "candidates": len(available_models),
        })
        return best

    def stats(self) -> dict:
        out = {}
        for key, rec in self._perf.items():
            model, tier = key.split("::", 1)
            calls = rec["calls"]
            out[key] = {
                "model": model, "tier": tier, "calls": calls,
                "success_rate": round(rec["success"] / max(calls, 1), 3),
                "avg_tps": round(rec["total_tokens"] / max(rec["total_s"], 0.1), 1),
            }
        return out


router = ModelRouter()
