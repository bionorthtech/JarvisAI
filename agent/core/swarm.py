"""
Agent Swarm — Director + typed Subagents + spawning protocol.

Architecture:
  Director receives a high-level goal, decomposes it into Tasks, assigns each
  to the best-matching SubAgent, tracks progress, and synthesizes results.

  SubAgents are typed workers with a defined skill set, tool whitelist, and
  resource budget. They are spawned on demand and destroyed when idle.

Lifecycle states: INIT → READY → RUNNING → BLOCKED → DONE | FAILED
Constraints:
  - Max depth: 3 (director → subagent → child)
  - Max concurrent agents: 8
  - Every DANGER/CRITICAL tool call still requires user confirmation
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import bus
from .budgets import budget_manager, BudgetExceeded


# ─── Agent Learning: outcome logging ─────────────────────────────────────────

def _log_outcome(agent: "SubAgent", success: bool, duration: float) -> None:
    """Persist task outcome to ChromaDB long-term memory for agent learning (1.5)."""
    try:
        from agent.core import memory as mem
        doc = (
            f"[AGENT OUTCOME] type={agent.agent_type} "
            f"skills={agent.task.required_skills} "
            f"goal={agent.task.goal[:200]} "
            f"success={success} duration={duration:.1f}s"
        )
        mem.add_to_ltm(doc, tags=["agent_outcome", agent.agent_type,
                                   "success" if success else "failure"])
    except Exception:
        pass  # learning is best-effort; never block task completion


# ─── Enums & data structures ─────────────────────────────────────────────────

class AgentStatus(str, Enum):
    INIT    = "INIT"
    READY   = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    DONE    = "DONE"
    FAILED  = "FAILED"


@dataclass
class Task:
    id:              str          = field(default_factory=lambda: str(uuid.uuid4()))
    description:     str          = ""
    goal:            str          = ""
    required_skills: list[str]   = field(default_factory=list)
    assigned_to:     Optional[str] = None
    status:          AgentStatus  = AgentStatus.INIT
    result:          Optional[str] = None
    error:           Optional[str] = None
    parent_task_id:  Optional[str] = None
    depth:           int           = 0
    created_at:      float         = field(default_factory=time.time)
    started_at:      Optional[float] = None
    finished_at:     Optional[float] = None
    # Resource budgets
    token_budget:      int = 8192
    wall_time_budget:  int = 300
    shell_call_budget: int = 20
    # Usage tracking
    tokens_used:     int = 0
    shell_calls_used: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "description": self.description, "goal": self.goal,
            "required_skills": self.required_skills, "assigned_to": self.assigned_to,
            "status": self.status.value, "result": self.result, "error": self.error,
            "parent_task_id": self.parent_task_id, "depth": self.depth,
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at,
            "tokens_used": self.tokens_used, "shell_calls_used": self.shell_calls_used,
        }


# ─── SubAgent base ────────────────────────────────────────────────────────────

class SubAgent:
    name:           str       = "SubAgent"
    agent_type:     str       = "generic"
    skills:         list[str] = []
    tool_whitelist: list[str] = []
    MAX_DEPTH:      int       = 3
    MAX_CONCURRENT: int       = 8

    # F1.2 — autonomy contract. min_autonomy_level is the daemon level
    # at which the Director may spawn this agent against drive-derived
    # goals without user prompting. wake_conditions are bus topics
    # that may trigger an out-of-band spawn at that level.
    # Default base: never autonomous (override per typed agent below).
    min_autonomy_level: int = 99
    wake_conditions:    list[str] = []

    def __init__(self, task: Task):
        self.id     = str(uuid.uuid4())
        self.task   = task
        self.status = AgentStatus.INIT
        # Item 5 — free-text "what am I doing right now" surfaced via
        # Director.status() to the Dashboard. Updated via _step().
        self._step_note: str = ""
        self._publish("spawned", status=self.status.value)

    def _step(self, note: str) -> None:
        """Set the current progress note + publish an agent.step event.
        Cheap; safe to call from inside _execute() at phase boundaries."""
        self._step_note = note
        self._publish("step", status=self.status.value, note=note[:200])

    def can_handle(self, required_skills: list[str]) -> bool:
        return bool(set(required_skills) & set(self.skills))

    MAX_RETRIES = 2  # self-healing: retry transient failures up to N times

    async def run(self) -> str:
        self.status = AgentStatus.RUNNING
        self.task.started_at = time.time()
        budget_manager.register(
            self.task.id, self.name,
            max_tokens=self.task.token_budget,
            max_wall_time_seconds=self.task.wall_time_budget,
            max_shell_calls=self.task.shell_call_budget,
        )
        self._publish("started", status=self.status.value)

        last_err: str | None = None
        try:
            for attempt in range(1, self.MAX_RETRIES + 2):  # 1 initial + MAX_RETRIES
                try:
                    result = await self._execute()
                    self.status = AgentStatus.DONE
                    self.task.finished_at = time.time()
                    self.task.result = result
                    duration = self.task.finished_at - (self.task.started_at or self.task.finished_at)
                    # 1A.2 — fire reflection with parent_id=task.id so
                    # skill_distiller can correlate by parent_id when
                    # deciding whether to distill this completion.
                    try:
                        from agent.core import reflection
                        from agent.core.lm_studio import get_client
                        asyncio.create_task(reflection.reflect(
                            self.task.description, result or "",
                            tool_summary={},  # subagents don't surface tool counts here
                            hit_max_steps=False,
                            client=get_client(), model=None,
                            source="swarm",
                            parent_id=self.task.id,
                        ))
                    except Exception:
                        pass  # reflection must never break a subagent
                    self._publish("completed", status=self.status.value,
                                  result=result[:500] if result else "", attempt=attempt)
                    _log_outcome(self, success=True, duration=duration)
                    return result
                except (asyncio.TimeoutError, BudgetExceeded) as e:
                    last_err = f"budget exceeded: {getattr(e, 'dimension', 'wall_time')}"
                    break  # budget breaches are not retryable
                except Exception as e:
                    last_err = str(e)
                    if attempt <= self.MAX_RETRIES:
                        self._publish("retrying", status=self.status.value,
                                      attempt=attempt, error=last_err[:200])
                        await asyncio.sleep(2 ** attempt)  # exponential backoff

            self.status = AgentStatus.FAILED
            self.task.error = last_err or "unknown error"
            self.task.finished_at = time.time()
            duration = self.task.finished_at - (self.task.started_at or self.task.finished_at)
            self._publish("failed", status=self.status.value, error=self.task.error)
            _log_outcome(self, success=False, duration=duration)
            return f"[FAILED: {self.task.error}]"
        finally:
            summary = budget_manager.release(self.task.id)
            if summary:
                self.task.tokens_used = summary["tokens_used"]
                self.task.shell_calls_used = summary["shell_calls_used"]

    async def _execute(self) -> str:
        raise NotImplementedError

    def _publish(self, event: str, **extra):
        bus.publish(f"agent.{event}", self.name, {
            "agent_id": self.id, "agent_type": self.agent_type,
            "task_id": self.task.id, "task_desc": self.task.description[:120],
            **extra,
        })


# ─── Typed Agents ─────────────────────────────────────────────────────────────

async def _gateway_ask(goal: str, context: str, budget: int) -> str:
    """Run gateway.ask() with model routing (1.8) and wall-time budget."""
    from agent.core.gateway import gateway, get_client
    from agent.core.model_router import router, score_complexity
    import time as _time

    # Resolve best model from available LM Studio models
    model: str | None = None
    try:
        client = get_client()
        status = await client.check_connection()
        if status.reachable and status.models:
            model = router.best_model(goal, status.models)
    except Exception:
        pass  # fall back to gateway default

    prompt = f"{context}\n\nTask: {goal}"
    tier = score_complexity(goal)
    t0 = _time.monotonic()
    try:
        result = await asyncio.wait_for(gateway.ask(prompt, model=model), timeout=budget)
        duration = _time.monotonic() - t0
        if model:
            tokens = len(result.split())  # rough token estimate
            router.record(model, tier, tokens, duration, success=True)
        return result
    except Exception:
        if model:
            router.record(model, tier, 0, _time.monotonic() - t0, success=False)
        raise


class CodeAgent(SubAgent):
    name       = "CodeAgent"
    agent_type = "code"
    skills     = ["write code", "refactor", "debug", "run tests", "ast analysis",
                  "code review", "implement feature"]
    tool_whitelist = ["read_file", "write_file", "run_shell", "list_directory",
                      "search_codebase"]
    min_autonomy_level: int = 3
    wake_conditions:    list[str] = ["code.changed.bulk", "test.failure"]

    async def _execute(self) -> str:
        return await _gateway_ask(
            self.task.goal,
            f"[CodeAgent] {self.task.description}",
            self.task.wall_time_budget,
        )


class ResearchAgent(SubAgent):
    name       = "ResearchAgent"
    agent_type = "research"
    skills     = ["web search", "documentation lookup", "kiwix search",
                  "chromadb query", "summarize", "research topic"]
    tool_whitelist = ["web_search_opt_in", "chromadb_query", "read_file", "kiwix_search"]
    min_autonomy_level: int = 3
    wake_conditions:    list[str] = ["research.gap", "curiosity.action.run"]

    async def _execute(self) -> str:
        return await _gateway_ask(
            self.task.goal,
            f"[ResearchAgent] {self.task.description}",
            self.task.wall_time_budget,
        )


class FileAgent(SubAgent):
    name       = "FileAgent"
    agent_type = "file"
    skills     = ["organize files", "batch rename", "index directories",
                  "cleanup orphaned files", "find files", "file management"]
    tool_whitelist = ["read_file", "write_file", "list_directory", "run_shell"]
    min_autonomy_level: int = 3
    wake_conditions:    list[str] = ["download.new", "fs.bulk_change"]

    async def _execute(self) -> str:
        return await _gateway_ask(
            self.task.goal,
            f"[FileAgent] {self.task.description}",
            self.task.wall_time_budget,
        )


class MemoryAgent(SubAgent):
    name       = "MemoryAgent"
    agent_type = "memory"
    skills     = ["chromadb ingestion", "memory consolidation", "context pruning",
                  "recall and retrieval", "index files", "memory management"]
    tool_whitelist = ["chromadb_query", "chromadb_ingest", "read_file"]
    # Memory consolidation is maintenance work — runs from level 1.
    min_autonomy_level: int = 1
    wake_conditions:    list[str] = ["memory.bloat", "session.end"]

    async def _execute(self) -> str:
        from agent.core import memory as mem
        stats = mem.stats()
        result = f"Memory: {stats.get('file_chunks', 0)} chunks, {stats.get('chat_turns', 0)} turns in project '{stats.get('project', '?')}'"
        bus.publish("memory.stats", "MemoryAgent", stats)
        return result


class MonitorAgent(SubAgent):
    name       = "MonitorAgent"
    agent_type = "monitor"
    skills     = ["process watching", "resource alerting", "log tailing",
                  "health checks", "system monitoring", "uptime check"]
    tool_whitelist = ["run_shell", "read_file"]
    # Read-only observation — safe from level 1.
    min_autonomy_level: int = 1
    wake_conditions:    list[str] = ["system.metrics_request", "perf.regression"]

    async def _execute(self) -> str:
        import psutil
        cpu  = psutil.cpu_percent(interval=1)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        result = (f"CPU {cpu:.1f}%  RAM {ram.percent:.1f}% ({ram.used//1024//1024} MB / {ram.total//1024//1024} MB)  "
                  f"Disk {disk.percent:.1f}% ({disk.free//1024//1024//1024} GB free)")
        bus.publish("system.metrics", "MonitorAgent", {
            "cpu_pct": cpu, "ram_pct": ram.percent,
            "ram_used_mb": ram.used // 1024 // 1024,
            "ram_total_mb": ram.total // 1024 // 1024,
            "disk_pct": disk.percent,
            "disk_free_gb": disk.free // 1024 // 1024 // 1024,
        })
        return result


class UIAgent(SubAgent):
    """GUI automation: screenshots, type text, click, keystroke sequences."""
    name       = "UIAgent"
    agent_type = "ui"
    skills     = ["gui automation", "screenshot capture", "form filling",
                  "click sequences", "type text", "keystroke", "ui interaction"]
    tool_whitelist = ["screenshot", "type_text", "click", "key_press",
                      "ydotool", "xdotool"]
    # GUI control = highest blast radius — only at level 3.
    min_autonomy_level: int = 3
    wake_conditions:    list[str] = []

    async def _execute(self) -> str:
        from agent.tools import gui_tools
        goal = self.task.goal.lower()
        if "screenshot" in goal or "capture screen" in goal:
            return await gui_tools.take_screenshot()
        if "type" in goal:
            text = self.task.description
            return await gui_tools.type_text(text)
        return await _gateway_ask(
            self.task.goal,
            f"[UIAgent] {self.task.description}",
            self.task.wall_time_budget,
        )


# ─── Agent Registry & matching ────────────────────────────────────────────────

_AGENT_TYPES: list[type[SubAgent]] = [
    CodeAgent, ResearchAgent,
    FileAgent, MemoryAgent, MonitorAgent, UIAgent,
]


def _best_agent_type(required_skills: list[str]) -> type[SubAgent]:
    """Return the agent type with the most skill overlap."""
    scored = [(sum(s in a.skills for s in required_skills), a) for a in _AGENT_TYPES]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored[0][0] > 0 else CodeAgent  # default to CodeAgent


# ─── Director ─────────────────────────────────────────────────────────────────

class Director:
    """
    Receives high-level goals, decomposes them into Tasks,
    assigns each to the best SubAgent, and synthesizes results.
    """

    MAX_CONCURRENT = 8
    MAX_DEPTH      = 3

    def __init__(self):
        self.id            = str(uuid.uuid4())
        self.active_tasks: dict[str, Task]     = {}
        self.active_agents: dict[str, SubAgent] = {}
        self._lock = asyncio.Lock()

    # ── Goal decomposition ───────────────────────────────────────────────────

    async def _decompose(
        self,
        goal: str,
        depth: int = 0,
        prior_skills: list[dict] | None = None,
    ) -> list[Task]:
        """Ask the LLM to break a goal into subtasks with skill tags.

        `prior_skills` (C14.1 follow-up) lets `run_goal` inject distilled
        skills from past similar tasks so the planner reuses proven
        approaches instead of re-deriving them. Each entry must have a
        `slug` (label) and `body` (the skill markdown) — typically built
        by Director.run_goal from `skill_distiller.search` + `get_skill`.
        """
        prior_section = ""
        if prior_skills:
            blocks = []
            for s in prior_skills[:3]:
                slug = s.get("slug", "")
                body = (s.get("body") or "").strip()
                if not body:
                    continue
                # Trim each skill to ~120 words (rough — keeps the prompt
                # bounded if we ever bump the limit higher).
                blocks.append(f"### {slug}\n{body[:600]}")
            if blocks:
                prior_section = (
                    "\nPrior approaches that worked for similar goals — "
                    "reuse the pattern if it fits, otherwise ignore:\n\n"
                    + "\n\n".join(blocks)
                    + "\n\n---\n"
                )
        try:
            from agent.core.lm_studio import get_client
            client = get_client()
            prompt = (
                "You are JARVIS task planner. Break this goal into 1-4 concrete subtasks.\n"
                "Return ONLY a JSON array of objects with keys: description, required_skills (list).\n"
                f"Available skills: {[s for a in _AGENT_TYPES for s in a.skills]}\n"
                f"{prior_section}"
                f"Goal: {goal}"
            )
            resp = client.chat.completions.create(
                model="local-model",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.2,
            )
            text = resp.choices[0].message.content.strip()
            # Extract JSON
            import re
            m = re.search(r"\[.*\]", text, re.DOTALL)
            subtasks_raw = json.loads(m.group() if m else text)
        except Exception:
            # Fallback: single task with goal as description
            subtasks_raw = [{"description": goal, "required_skills": ["write code"]}]

        tasks = []
        for raw in subtasks_raw[:4]:
            t = Task(
                description=raw.get("description", goal),
                goal=goal,
                required_skills=raw.get("required_skills", []),
                depth=depth,
            )
            tasks.append(t)
        return tasks

    # ── Task execution ───────────────────────────────────────────────────────

    async def run_goal(self, goal: str, depth: int = 0) -> str:
        """Full director cycle: decompose → assign → execute → synthesize."""
        if depth >= self.MAX_DEPTH:
            return f"[Director] Max depth {self.MAX_DEPTH} reached — aborting recursion."

        if len(self.active_agents) >= self.MAX_CONCURRENT:
            return "[Director] Agent pool full — try again shortly."

        bus.publish("director.goal", "director", {
            "goal": goal[:200], "depth": depth,
            "active_agents": len(self.active_agents),
        })

        # C14.1 — recall prior distilled skills for this goal. The hit
        # list goes to the bus (dashboard/theater) AND into _decompose as
        # context so the planner can reuse proven approaches rather than
        # re-deriving them. Bodies are pulled lazily so we don't read
        # everything when nothing matches.
        prior_skills: list[dict] = []
        try:
            from agent.aliveness import skill_distiller
            hits = skill_distiller.search(goal, limit=3)
            if hits:
                for h in hits:
                    full = skill_distiller.get_skill(h["slug"])
                    if full and full.get("body"):
                        prior_skills.append({"slug": h["slug"], "body": full["body"]})
                bus.publish("director.skill_match", "director", {
                    "goal": goal[:200],
                    "matches": [{"slug": h["slug"], "task_desc": h["task_desc"]}
                                for h in hits],
                    "prepended_to_decompose": len(prior_skills),
                })
        except Exception:
            pass

        tasks = await self._decompose(goal, depth, prior_skills or None)

        bus.publish("director.plan", "director", {
            "task_count": len(tasks),
            "tasks": [{"id": t.id, "desc": t.description, "skills": t.required_skills}
                      for t in tasks],
        })

        # Execute tasks (parallel where possible)
        coros = []
        agents = []
        for task in tasks:
            agent_cls = _best_agent_type(task.required_skills)
            agent = agent_cls(task)
            task.assigned_to = agent.name
            async with self._lock:
                self.active_tasks[task.id]   = task
                self.active_agents[agent.id] = agent
            agents.append((agent, task))
            coros.append(agent.run())

        results = await asyncio.gather(*coros, return_exceptions=True)

        # Clean up
        async with self._lock:
            for agent, task in agents:
                self.active_tasks.pop(task.id, None)
                self.active_agents.pop(agent.id, None)

        # Synthesize
        parts = []
        for (agent, task), result in zip(agents, results):
            if isinstance(result, Exception):
                parts.append(f"[{agent.name}] ERROR: {result}")
            else:
                parts.append(f"[{agent.name}] {result}")

        synthesis = "\n\n".join(parts)
        bus.publish("director.done", "director", {
            "goal": goal[:200], "result_preview": synthesis[:300],
            "task_count": len(tasks),
        })
        return synthesis

    # ── Status ───────────────────────────────────────────────────────────────

    def status(self) -> dict:
        now = time.time()
        return {
            "active_tasks":  len(self.active_tasks),
            "active_agents": len(self.active_agents),
            "agents": [
                {
                    "id": a.id, "type": a.agent_type, "name": a.name,
                    "status": a.status.value,
                    "task": a.task.description[:80],
                    # Item 5 — surface what the agent is doing right now.
                    "step":      getattr(a, "_step_note", "") or "",
                    "elapsed_s": (
                        round(now - a.task.started_at, 1)
                        if a.task.started_at else 0.0
                    ),
                    "started_at": a.task.started_at,
                    "tokens_used":     a.task.tokens_used,
                    "shell_calls_used": a.task.shell_calls_used,
                }
                for a in self.active_agents.values()
            ],
            "tasks": [t.to_dict() for t in self.active_tasks.values()],
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

director = Director()
