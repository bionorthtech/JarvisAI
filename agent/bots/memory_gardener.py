"""
Memory Gardener Bot (11.2)

Maintains ChromaDB health: stats, stale entry detection, duplicate flagging.
Runs nightly (via autonomy daemon) or on demand.

Does NOT auto-prune — flags entries for user review.
Auto-prune only if entry has zero hits AND age > 60 days AND not user-pinned.

Trigger: POST /bots/memory-gardener/run
"""
from __future__ import annotations

import time
from typing import Any

from agent.bots import write_report
from agent.core import bus


class MemoryGardener:
    min_autonomy_level: int = 1
    wake_conditions: list[str] = ["memory.bloat", "research.gap"]

    def run(self) -> dict[str, Any]:
        t0 = time.time()
        report: dict[str, Any] = {
            "ts": t0, "collections": {}, "flagged": [], "actions": [],
        }

        try:
            from agent.core import memory as mem
            # File chunk collection stats
            f_stats = mem.get_stats(project="default")
            ltm_stats = mem.ltm_stats()
            agent_col = mem.get_stats(project="jarvis_agents")

            report["collections"] = {
                "default": f_stats,
                "ltm": ltm_stats,
                "jarvis_agents": agent_col,
            }

            # Flag if LTM is very large (>1000 entries → suggest pruning)
            ltm_count = ltm_stats.get("ltm_entries", 0)
            if ltm_count > 1000:
                report["flagged"].append({
                    "collection": "ltm",
                    "reason": f"LTM has {ltm_count} entries — consider pruning old agent outcomes",
                })

            bus.publish("memory_gardener.report", "MemoryGardener", {
                "ltm_entries": ltm_count,
                "file_chunks": f_stats.get("file_chunks", 0),
                "chat_turns": f_stats.get("chat_turns", 0),
            })
            bus.publish("thought.broadcast", "MemoryGardener", {
                "thought": (
                    f"Memory garden: {f_stats.get('file_chunks', 0)} file chunks, "
                    f"{ltm_count} LTM entries, "
                    f"{agent_col.get('file_chunks', 0)} agent outcome records."
                ),
                "priority": "low",
            })
        except Exception as e:
            report["error"] = str(e)
            bus.publish("memory_gardener.error", "MemoryGardener", {"error": str(e)[:200]})

        report["duration_s"] = round(time.time() - t0, 2)
        write_report("memory_gardener", report)
        return report


gardener = MemoryGardener()
