"""
Resource Budget Manager

Each agent task gets a budget: max_tokens, max_wall_time_seconds, max_shell_calls.
The BudgetManager tracks usage and exposes a method to check if a task should
be paused/aborted. Director and SubAgent integrate with this to enforce limits.

Default budgets :
    max_tokens:            8192
    max_wall_time_seconds: 300
    max_shell_calls:       20
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import bus

DEFAULT_BUDGET = {
    "max_tokens": 8192,
    "max_wall_time_seconds": 300,
    "max_shell_calls": 20,
}


@dataclass
class BudgetState:
    task_id: str
    agent_name: str
    started_at: float = field(default_factory=time.time)
    tokens_used: int = 0
    shell_calls_used: int = 0
    max_tokens: int = DEFAULT_BUDGET["max_tokens"]
    max_wall_time_seconds: int = DEFAULT_BUDGET["max_wall_time_seconds"]
    max_shell_calls: int = DEFAULT_BUDGET["max_shell_calls"]

    def wall_elapsed(self) -> float:
        return time.time() - self.started_at

    def remaining(self) -> dict[str, float]:
        return {
            "tokens": max(0, self.max_tokens - self.tokens_used),
            "wall_time_s": max(0.0, self.max_wall_time_seconds - self.wall_elapsed()),
            "shell_calls": max(0, self.max_shell_calls - self.shell_calls_used),
        }

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent": self.agent_name,
            "tokens_used": self.tokens_used,
            "shell_calls_used": self.shell_calls_used,
            "wall_elapsed_s": round(self.wall_elapsed(), 2),
            "remaining": self.remaining(),
            "limits": {
                "max_tokens": self.max_tokens,
                "max_wall_time_seconds": self.max_wall_time_seconds,
                "max_shell_calls": self.max_shell_calls,
            },
        }


class BudgetExceeded(Exception):
    def __init__(self, dimension: str, used: float, limit: float, task_id: str):
        self.dimension = dimension
        self.used = used
        self.limit = limit
        self.task_id = task_id
        super().__init__(
            f"Budget exceeded for task {task_id}: {dimension} used={used} limit={limit}"
        )


class BudgetManager:
    """
    Tracks per-task resource usage. Director registers tasks at launch,
    SubAgents call `record_*` to bump counters, `check` raises if any
    dimension is exhausted.
    """

    def __init__(self):
        self._states: dict[str, BudgetState] = {}

    def register(
        self,
        task_id: str,
        agent_name: str,
        max_tokens: int | None = None,
        max_wall_time_seconds: int | None = None,
        max_shell_calls: int | None = None,
    ) -> BudgetState:
        st = BudgetState(
            task_id=task_id,
            agent_name=agent_name,
            max_tokens=max_tokens or DEFAULT_BUDGET["max_tokens"],
            max_wall_time_seconds=max_wall_time_seconds or DEFAULT_BUDGET["max_wall_time_seconds"],
            max_shell_calls=max_shell_calls or DEFAULT_BUDGET["max_shell_calls"],
        )
        self._states[task_id] = st
        bus.publish("budget.registered", "budget_manager", {
            "task_id": task_id, "agent": agent_name, "limits": st.to_dict()["limits"],
        })
        return st

    def record_tokens(self, task_id: str, tokens: int) -> None:
        st = self._states.get(task_id)
        if not st:
            return
        st.tokens_used += int(tokens)
        self._maybe_alert(st, "tokens")

    def record_shell_call(self, task_id: str) -> None:
        st = self._states.get(task_id)
        if not st:
            return
        st.shell_calls_used += 1
        self._maybe_alert(st, "shell_calls")

    def check(self, task_id: str) -> None:
        st = self._states.get(task_id)
        if not st:
            return
        if st.tokens_used >= st.max_tokens:
            raise BudgetExceeded("tokens", st.tokens_used, st.max_tokens, task_id)
        if st.wall_elapsed() >= st.max_wall_time_seconds:
            raise BudgetExceeded("wall_time", st.wall_elapsed(), st.max_wall_time_seconds, task_id)
        if st.shell_calls_used >= st.max_shell_calls:
            raise BudgetExceeded("shell_calls", st.shell_calls_used, st.max_shell_calls, task_id)

    def _maybe_alert(self, st: BudgetState, dim: str) -> None:
        rem = st.remaining()
        if dim == "tokens" and rem["tokens"] < st.max_tokens * 0.1:
            bus.publish("budget.warning", "budget_manager", {
                "task_id": st.task_id, "dim": "tokens",
                "remaining": rem["tokens"], "limit": st.max_tokens,
            })
        elif dim == "shell_calls" and rem["shell_calls"] < 3:
            bus.publish("budget.warning", "budget_manager", {
                "task_id": st.task_id, "dim": "shell_calls",
                "remaining": rem["shell_calls"], "limit": st.max_shell_calls,
            })

    def release(self, task_id: str) -> dict[str, Any] | None:
        st = self._states.pop(task_id, None)
        if st:
            summary = st.to_dict()
            bus.publish("budget.released", "budget_manager", summary)
            return summary
        return None

    def snapshot(self) -> list[dict]:
        return [st.to_dict() for st in self._states.values()]


budget_manager = BudgetManager()
