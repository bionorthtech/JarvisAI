"""
Knowledge Curator Bot (Phase 11.6 / Phase 4.3)

Aggregates knowledge gaps from ResearchAgent misses (published as
`research.gap` events on the bus), generates a ranked Doc Wishlist,
and at autonomy level 3 auto-fetches top items.

Trigger: daily via autonomy + on-demand via POST /bots/knowledge-curator/run.

Knowledge gaps are read from the bus history (SQLite) — emit
`research.gap` events with `{topic, context}` payloads from any agent
or plugin that detects a knowledge miss.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from agent.core import bus

WISHLIST_FILE = Path.home() / ".jarvis" / "doc_wishlist.json"


class KnowledgeCurator:
    min_autonomy_level: int = 1
    wake_conditions: list[str] = ["research.gap"]

    def run(self, hours: int = 24) -> dict[str, Any]:
        t0 = time.time()
        gaps = self._recent_gaps(hours=hours)
        wishlist = self._build_wishlist(gaps)

        try:
            WISHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            WISHLIST_FILE.write_text(json.dumps({
                "generated_at": t0,
                "window_hours": hours,
                "gap_count": len(gaps),
                "wishlist": wishlist,
            }, indent=2))
        except Exception:
            pass

        autonomy_level = self._autonomy_level()
        actions: list[str] = []
        if autonomy_level >= 3 and wishlist:
            for item in wishlist[:3]:
                if self._auto_fetch(item):
                    actions.append(f"fetched: {item['topic']}")

        report = {
            "ts": t0,
            "duration_s": round(time.time() - t0, 2),
            "gap_count": len(gaps),
            "wishlist": wishlist,
            "actions": actions,
            "autonomy_level": autonomy_level,
        }

        bus.publish("knowledge_curator.report", "KnowledgeCurator", {
            "gaps": len(gaps),
            "wishlist_size": len(wishlist),
            "auto_fetched": len(actions),
        })

        if wishlist and autonomy_level < 3:
            bus.publish("thought.broadcast", "KnowledgeCurator", {
                "thought": (
                    f"Doc wishlist updated: {len(wishlist)} item(s). "
                    f"Top: {wishlist[0]['topic'][:80]}"
                ),
                "priority": "low",
            })

        return report

    def _recent_gaps(self, hours: int) -> list[dict[str, Any]]:
        cutoff = time.time() - hours * 3600
        events = bus.recent(limit=1000, topic_prefix="research.gap")
        out: list[dict[str, Any]] = []
        for e in events:
            if e.get("ts", 0) < cutoff:
                continue
            topic = e.get("topic_name") or e.get("payload", {}).get("topic")
            if not topic and "topic" in e:
                topic = e.get("topic")
            if not topic:
                continue
            out.append({
                "ts": e.get("ts"),
                "topic": topic if isinstance(topic, str) else str(topic),
                "context": e.get("context", "")[:200],
            })
        return out

    def _build_wishlist(self, gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        c: Counter[str] = Counter()
        contexts: dict[str, list[str]] = {}
        for g in gaps:
            topic = (g.get("topic") or "").strip()
            if not topic:
                continue
            c[topic] += 1
            contexts.setdefault(topic, []).append(g.get("context", "")[:120])

        ranked: list[dict[str, Any]] = []
        for topic, freq in c.most_common(20):
            ranked.append({
                "topic": topic,
                "frequency": freq,
                "score": freq,
                "sample_contexts": contexts[topic][:3],
            })
        return ranked

    def _autonomy_level(self) -> int:
        try:
            from agent.core.autonomy import autonomy
            return autonomy.level
        except Exception:
            return 0

    def _auto_fetch(self, item: dict[str, Any]) -> bool:
        topic = item["topic"]
        try:
            from plugins.web_search import plugin as ws_plugin
            results = ws_plugin.web_search(topic, max_results=3)
        except Exception:
            return False

        if not results:
            return False

        try:
            from agent.core import memory as mem
            mem.add_to_ltm(
                f"[KNOWLEDGE FETCHED] topic={topic}\n{json.dumps(results)[:1500]}",
                tags=["knowledge_curator", "auto_fetch"],
            )
            bus.publish("knowledge_curator.fetched", "KnowledgeCurator", {
                "topic": topic,
                "result_count": len(results) if isinstance(results, list) else 1,
            })
            return True
        except Exception:
            return False


curator = KnowledgeCurator()
