"""
Autonomous Operation Mode (1.6) — Jarvis heartbeat daemon.

Levels:
  0 — Off: only acts when asked
  1 — Maintenance: auto-index files, prune memories, run health checks
  2 — Proactive: suggest tasks, prepare summaries, notify unprompted
  3 — Full Auto: pursue standing goals from the goal list

Safeguards:
  - Per-cycle shell command budget (hard cap)
  - DANGER/CRITICAL tier always requires user confirmation
  - Full audit trail via bus events
  - Kill switch: set level=0 to halt everything immediately
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import audit, bus

_STATE_FILE = Path.home() / ".jarvis" / "autonomy.json"


def _audit_drive_action(*, tool_name: str, action_type: str,
                        origin: str, detail: str) -> None:
    """F5.2 — audit-log every autonomous action with an `origin` tag.
    Best-effort: never raises into the daemon loop.
    """
    try:
        audit.log(
            session_id="autonomy_daemon",
            tool_name=tool_name,
            action_type=action_type,
            params={"origin": origin, "detail": detail[:300]},
            confirmed=False,
        )
    except Exception:
        pass

_DEFAULT_STATE = {
    "level": 0,           # 0-3
    "cycle_budget": 10,   # max shell calls per cycle
    "cycle_interval": 300,  # seconds between autonomy cycles
    "standing_goals": [],   # list of goal strings for level 3
    "goal_meta": {},      # D5 — {goal_str: {created_at, last_reinforced_at, last_stale_alert}}
    "last_cycle": 0.0,
    "cycles_run": 0,
    "actions_taken": 0,
    "bot_last_run": {},   # {bot_id: ts} — bot scheduler state
    "drive_last_dispatch": {},  # F5 — {drive_name: ts} cooldown per drive
}

# D5 — standing goals decay after 30 days untouched. Once stale, JARVIS
# publishes `goal.stale` at most once per 24h per goal until the user
# acts (reinforce / re-scope / drop).
_GOAL_STALE_AFTER_S = 30 * 24 * 3600
_GOAL_STALE_ALERT_COOLDOWN_S = 24 * 3600

# F5.3 — system-wide cap on autonomous tasks in flight at any moment.
# Past this, drive-triggered dispatches queue instead of stacking.
_MAX_AUTONOMOUS_IN_FLIGHT = 5

# F5.1 — drive thresholds for autonomous dispatch.
_DRIVE_TRIGGER = 0.7
# Per-drive cooldown so a wedged drive can't thrash the dispatcher.
_DRIVE_COOLDOWN_S = 1800  # 30 minutes

# Per-bot schedule (seconds between runs).
_BOT_SCHEDULE = {
    "memory_gardener":      86400,   # nightly
    "code_health":          604800,  # weekly
    "performance_watchdog": 21600,   # every 6h
    "knowledge_curator":    86400,   # daily
    "homelab_warden":       300,     # every 5 minutes — service+container watch
}


# 1B.1 — Periodic registry. Each entry is a time-driven task the
# autonomy daemon owns instead of letting each module spin its own
# asyncio loop. Bots stay in _BOT_SCHEDULE for now (they have a richer
# event contract — heartbeat / dispatch / error topics). This registry
# absorbs the simpler "tick every N seconds" daemons that used to live
# at main.py level (drives.tick_loop first; aliveness daemons follow
# in 1B.2).
#
# Each entry:
#   name              — stable id for last_run bookkeeping
#   interval_s        — seconds between runs
#   min_autonomy_level — gate
#   handler           — async callable, no args, must not raise
#   bp_class          — "lm" | "io" | "free" for backpressure
@dataclass(frozen=True)
class PeriodicEntry:
    name: str
    interval_s: int
    min_autonomy_level: int
    handler: Callable[[], Any]
    bp_class: str  # "lm" | "io" | "free"


def _build_periodic_registry() -> "list[PeriodicEntry]":
    """Lazy registry construction so module-import doesn't pull every
    handler's transitive deps. Called once at daemon start."""
    from agent.core import drives as _drv

    async def _tick_drives_async() -> None:
        # drives._tick_once is sync but fast (no I/O beyond a small JSON
        # write); wrap in a thread for consistency with future entries.
        await asyncio.to_thread(_drv._tick_once)

    async def _tick_style_learner_async() -> None:
        # 1C — daily distill of style signals into personality_traits
        # adjustments. Cheap when signals are sparse; never calls the LM.
        from agent.core import style_learner as _sl
        await asyncio.to_thread(_sl.distill_daily)

    async def _tick_personality_traits_async() -> None:
        # sample vault tags / chat topics / tool usage. Internally
        # rate-limited at the 10-min boundary; the registry just gives it
        # a stable cadence so it stops firing every 5-min maintenance cycle.
        from agent.core import personality_traits as _pt
        await asyncio.to_thread(_pt.refresh)

    async def _tick_brain_co_ownership_async() -> None:
        # vault scan for capture/link/stale-note suggestions.
        # Read-only over the vault; emits aliveness.notification events.
        # Was called every maintenance cycle (5 min); 30-min cadence is
        # plenty for a "watch what's missing" signal.
        from agent.aliveness import brain_co_ownership as _bco
        await asyncio.to_thread(_bco.scan_and_publish)

    async def _tick_system_metrics_async() -> None:
        # 4A — system metrics publisher. Was a standalone _metrics_loop
        # in main.py firing every 15s; now an orchestrator entry so the
        # one-process-owns-all-periodic-work invariant holds.
        import psutil
        cpu  = psutil.cpu_percent(interval=None)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        bus.publish("system.metrics", "sidecar", {
            "cpu_pct":      cpu,
            "ram_pct":      ram.percent,
            "ram_used_mb":  ram.used  // 1024 // 1024,
            "ram_total_mb": ram.total // 1024 // 1024,
            "disk_pct":     disk.percent,
            "disk_free_gb": disk.free // 1024 // 1024 // 1024,
            "ws_clients":   bus.subscriber_count(),
        })

    async def _tick_plugin_heartbeat_async() -> None:
        # 4A — plugin heartbeat. Was a standalone run_heartbeat_loop in
        # main.py; now driven by the orchestrator with the same 60s cadence.
        from agent.core import plugin_loader as _pl
        await asyncio.to_thread(_pl.heartbeat_once)

    return [
        PeriodicEntry(
            name="drives.tick",
            interval_s=_drv._TICK_SECONDS,   # 900s
            min_autonomy_level=0,             # drives always tick
            handler=_tick_drives_async,
            bp_class="io",
        ),
        PeriodicEntry(
            name="style_learner.distill_daily",
            interval_s=24 * 3600,             # once per day
            min_autonomy_level=0,             # passive observation, no LM
            handler=_tick_style_learner_async,
            bp_class="io",
        ),
        PeriodicEntry(
            name="personality_traits.refresh",
            interval_s=10 * 60,               # 10 min (matches internal rate-limit)
            min_autonomy_level=1,             # maintenance-and-up
            handler=_tick_personality_traits_async,
            bp_class="io",
        ),
        PeriodicEntry(
            name="brain_co_ownership.scan",
            interval_s=30 * 60,               # 30 min — was every 5 min cycle
            min_autonomy_level=1,
            handler=_tick_brain_co_ownership_async,
            bp_class="io",
        ),
        PeriodicEntry(
            name="system.metrics",
            interval_s=15,                    # was a standalone 15s loop
            min_autonomy_level=0,             # always publish, even at level 0
            handler=_tick_system_metrics_async,
            bp_class="io",
        ),
        PeriodicEntry(
            name="plugin.heartbeat",
            interval_s=60,                    # was a standalone 60s loop
            min_autonomy_level=0,
            handler=_tick_plugin_heartbeat_async,
            bp_class="io",
        ),
    ]


# Kill switch — flip ORCHESTRATOR_ENABLED=0 to fall back to per-daemon
# loops in main.py. Default on.
def _orchestrator_enabled() -> bool:
    import os as _os
    return _os.environ.get("JARVIS_ORCHESTRATOR_ENABLED", "1") not in ("0", "false", "no")


class AutonomyDaemon:
    def __init__(self):
        self._state: dict[str, Any] = self._load()
        self._running = False
        self._task: asyncio.Task | None = None
        # F5.3 — in-flight autonomous tasks (drive- and goal-spawned).
        self._in_flight: int = 0
        self._queued_dispatches: list[dict[str, Any]] = []
        # last observable user activity (chat, POST, etc.). The
        # emotion model uses this to grow BOREDOM during idle stretches.
        # Initialized to now so we don't fire a stale spike at startup.
        self._last_user_activity: float = time.time()
        # bus subscription so the emotion model reflects what JARVIS
        # is actually doing. Created lazily in start() (needs a running loop).
        self._emotion_queue: asyncio.Queue | None = None

    def record_user_activity(self, source: str = "unknown") -> None:
        """call this whenever the user does something
        observable: sends a chat, hits a settings POST, etc. Resets the
        idle clock the emotion + inactivity-awareness systems read."""
        self._last_user_activity = time.time()

    def _drain_emotion_events(self) -> int:
        """pull every queued bus message off the emotion subscription
        and feed it to `_internal_state.apply_event`. Non-blocking; returns
        the count applied. The queue is bounded (500) — if it overflows
        between cycles the older events are dropped, which is fine because
        emotion is a recent-bias signal anyway."""
        if not self._emotion_queue:
            return 0
        applied = 0
        # Drain up to 200 per cycle so a flood can't burn the whole tick.
        for _ in range(200):
            try:
                evt = self._emotion_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            topic = evt.get("topic", "")
            applied += _internal_state.apply_event(topic, evt)
        return applied

    def _load(self) -> dict:
        try:
            if _STATE_FILE.exists():
                state = {**_DEFAULT_STATE, **json.loads(_STATE_FILE.read_text())}
                # Migration (1B.1 critic fix): older builds persisted
                # periodic_in_flight, which would leave entries
                # permanently flagged after a mid-handler crash. The
                # in-flight map is now in-memory only.
                state.pop("periodic_in_flight", None)
                return state
        except Exception:
            pass
        return dict(_DEFAULT_STATE)

    def _save(self):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps(self._state, indent=2))
        except Exception:
            pass

    @property
    def level(self) -> int:
        return self._state["level"]

    def set_level(self, level: int) -> dict:
        level = max(0, min(3, int(level)))
        old = self._state["level"]
        self._state["level"] = level
        self._save()
        bus.publish("autonomy.level_changed", "daemon", {"old": old, "new": level})
        if level == 0:
            # F1.3 kill switch — halt the running loop and signal to all
            # consumers that autonomous activity is paused.
            self._running = False
            if self._task and not self._task.done():
                self._task.cancel()
            bus.publish("autonomy.killed", "daemon", {
                "previous_level": old, "killed_at": time.time(),
            })
        return self.status()

    def status(self) -> dict:
        return {
            "level": self._state["level"],
            "level_name": ["Off", "Maintenance", "Proactive", "Full Auto"][self._state["level"]],
            "cycle_interval": self._state["cycle_interval"],
            "cycle_budget": self._state["cycle_budget"],
            "last_cycle": self._state["last_cycle"],
            "cycles_run": self._state["cycles_run"],
            "actions_taken": self._state["actions_taken"],
            "standing_goals": self._state["standing_goals"],
        }

    def add_goal(self, goal: str) -> dict:
        if goal not in self._state["standing_goals"]:
            self._state["standing_goals"].append(goal)
            now = time.time()
            self._state.setdefault("goal_meta", {})[goal] = {
                "created_at": now,
                "last_reinforced_at": now,
                "last_stale_alert": 0.0,
            }
            self._save()
            bus.publish("goal.added", "daemon", {"goal": goal})
        return self.status()

    def remove_goal(self, goal: str) -> dict:
        self._state["standing_goals"] = [g for g in self._state["standing_goals"] if g != goal]
        meta = self._state.setdefault("goal_meta", {})
        if goal in meta:
            del meta[goal]
        self._save()
        bus.publish("goal.dropped", "daemon", {"goal": goal})
        return self.status()

    def reinforce_goal(self, goal: str) -> dict:
        """D5 — refresh a goal's last-reinforced timestamp. Triggered by:
        user action (UI button, /autonomy/goals/reinforce), or
        automatically when the goal produces output during a cycle."""
        meta = self._state.setdefault("goal_meta", {})
        if goal in self._state["standing_goals"]:
            now = time.time()
            entry = meta.setdefault(goal, {
                "created_at": now, "last_reinforced_at": now, "last_stale_alert": 0.0,
            })
            entry["last_reinforced_at"] = now
            entry["last_stale_alert"] = 0.0   # reset cooldown so a future relapse alerts again
            self._save()
            bus.publish("goal.reinforced", "daemon", {"goal": goal})
        return self.status()

    def goals_with_meta(self) -> list[dict]:
        """D5 — return standing goals enriched with decay metadata so the
        UI can show age + stale badge + reinforce/drop affordances."""
        now = time.time()
        meta = self._state.get("goal_meta", {})
        out = []
        for g in self._state["standing_goals"]:
            m = meta.get(g, {})
            last = m.get("last_reinforced_at") or m.get("created_at") or now
            age_s = max(0.0, now - last)
            out.append({
                "goal": g,
                "created_at": m.get("created_at", now),
                "last_reinforced_at": last,
                "age_days": round(age_s / 86400, 2),
                "is_stale": age_s >= _GOAL_STALE_AFTER_S,
            })
        return out

    def _check_stale_goals(self) -> None:
        """D5 — called once per maintenance cycle. Publishes `goal.stale`
        for goals untouched ≥30d, throttled to one alert per 24h per goal.
        """
        now = time.time()
        meta = self._state.setdefault("goal_meta", {})
        changed = False
        for g in self._state["standing_goals"]:
            entry = meta.setdefault(g, {
                "created_at": now, "last_reinforced_at": now, "last_stale_alert": 0.0,
            })
            last = entry.get("last_reinforced_at", now)
            if now - last < _GOAL_STALE_AFTER_S:
                continue
            if now - entry.get("last_stale_alert", 0.0) < _GOAL_STALE_ALERT_COOLDOWN_S:
                continue
            entry["last_stale_alert"] = now
            changed = True
            bus.publish("goal.stale", "daemon", {
                "goal": g,
                "age_days": round((now - last) / 86400, 1),
            })
        if changed:
            self._save()

    async def start(self):
        self._running = True
        # subscribe once so each cycle can drain new bus events into
        # the emotion model. Must happen here (not __init__) because Queue
        # binds to the running loop.
        self._emotion_queue = bus.subscribe(maxsize=500)
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while self._running:
            interval = self._state["cycle_interval"]
            await asyncio.sleep(interval)
            if self._state["level"] == 0:
                continue
            await self._run_cycle()

    async def _run_cycle(self):
        level = self._state["level"]
        if level == 0:
            return

        self._state["last_cycle"] = time.time()
        self._state["cycles_run"] += 1
        self._save()

        bus.publish("autonomy.cycle_start", "daemon", {
            "level": level, "cycle": self._state["cycles_run"]
        })

        # keep the emotion model honest. Decay first (so stale spikes
        # fade), then drain bus events the daemon care about, then apply
        # idle-driven boredom growth. The first cycle after startup is when
        # we replay anything that arrived during the lifespan window.
        try:
            _internal_state.decay()
            self._drain_emotion_events()
            _internal_state.apply_idle(time.time() - self._last_user_activity)
        except Exception as e:
            bus.publish("autonomy.emotion_error", "daemon", {"error": str(e)[:200]})

        actions = 0
        try:
            if level >= 1:
                actions += await self._maintenance_cycle()
            if level >= 2:
                actions += await self._proactive_cycle()
            if level >= 3:
                actions += await self._full_auto_cycle()
        except Exception as e:
            bus.publish("autonomy.cycle_error", "daemon", {"error": str(e)[:200]})

        self._state["actions_taken"] += actions
        self._save()
        bus.publish("autonomy.cycle_done", "daemon", {
            "level": level, "cycle": self._state["cycles_run"],
            "actions": actions, "total_actions": self._state["actions_taken"],
        })

    async def _maintenance_cycle(self) -> int:
        """Level 1: memory housekeeping + scheduled bots."""
        actions = 0
        # Phase 11 bot scheduler — run any bot whose interval has elapsed
        actions += await self._run_due_bots()

        # 1B.1 — periodic registry. Non-bot periodic tasks (drives.tick,
        # later: aliveness daemons) checked here instead of running their
        # own asyncio loops in main.py. Behavior-identical for slice 1.
        if _orchestrator_enabled():
            actions += await self._run_periodic_registry()

        # F6.1 — daily digest at 19:00 local (no-op if already composed today).
        try:
            from . import daily_digest
            result = await asyncio.to_thread(daily_digest.compose_if_due)
            if result.get("ok") and not result.get("skipped"):
                actions += 1
        except Exception as e:
            bus.publish("autonomy.digest_error", "daemon", {"error": str(e)[:200]})

        # D5 — surface standing goals untouched ≥30 days.
        try:
            self._check_stale_goals()
        except Exception:
            pass

        # D3 — morning briefing at 08:00 local (no-op if already written).
        try:
            from ..aliveness import morning_briefing
            await morning_briefing.compose_if_due()
        except Exception as e:
            bus.publish("autonomy.morning_brief_error", "daemon",
                        {"error": str(e)[:200]})

        # brain co-ownership scan and personality_traits.refresh
        # now run through _PERIODIC_REGISTRY (1B.2). Their per-cycle calls
        # used to live here; removing them avoids the 5-minute hammer on
        # what's really a 10-30-minute interest.

        # D7 — auto-fill stale personality cards. fill_missing() internally
        # skips cards still inside the 7-day refresh window, so this only
        # spends LM time on actually-stale cards. Daily cooldown so we
        # don't spam at every maintenance cycle.
        try:
            last_fill = float(self._state.get("last_card_fill_ts", 0))
            if time.time() - last_fill >= 23 * 3600:
                from agent.core import personality_cards
                result = await personality_cards.fill_missing()
                if result.get("ok") and result.get("generated", 0) > 0:
                    actions += result["generated"]
                self._state["last_card_fill_ts"] = time.time()
                self._save()
        except Exception as e:
            bus.publish("autonomy.personality_error", "daemon",
                        {"error": str(e)[:200]})

        # weekly self-introspection beat. Once every 7 days,
        # JARVIS summarizes "what I learned about myself this week" from
        # git log and pushes a thought-broadcast. The /self/explain
        # endpoint already exists; this just times the surface.
        try:
            last = float(self._state.get("last_weekly_introspection_ts", 0))
            if time.time() - last >= 7 * 86400:
                from agent.core import self_introspection
                report = await asyncio.to_thread(
                    self_introspection.recent_changes, 7, 25,
                )
                if report.get("ok") and report.get("file_count", 0) > 0:
                    files = report["files"]
                    sample = ", ".join(
                        f["path"].split("/")[-1] for f in files[:5]
                    )
                    bus.publish("thought.broadcast", "autonomy", {
                        "thought": (
                            f"This week I changed {report['file_count']} files "
                            f"— most-recent: {sample}. "
                            "Browse with /self/recent-changes."
                        ),
                        "priority": "low",
                    })
                    bus.publish("aliveness.notification", "autonomy", {
                        "category": "introspection",
                        "body": f"Weekly recap — {report['file_count']} files touched.",
                        "cta_topic": "self.recent_changes",
                    })
                    self._state["last_weekly_introspection_ts"] = time.time()
                    self._save()
                    actions += 1
        except Exception as e:
            bus.publish("autonomy.introspection_error", "daemon",
                        {"error": str(e)[:200]})

        return actions

    # In-memory in-flight tracking — never persisted (critic 1B.1-#2:
    # a crash mid-handler would otherwise leave the flag on disk and
    # permanently skip the entry after restart). Reset implicitly by
    # process boot.
    _periodic_in_flight: dict[str, bool] | None = None
    # Registry-build retry cooldown (critic 1B.1-#1: a transient
    # ImportError used to pin registry to [] for the process lifetime).
    _last_registry_build_attempt: float = 0.0

    async def _run_periodic_registry(self) -> int:
        """1B.1 — Walk the periodic registry and dispatch any entry whose
        interval has elapsed. Each entry has its own cadence; this loop
        is just the gate.

        Backpressure: entries with `bp_class="lm"` are skipped (not
        dropped — their last_run is left alone so they fire next cycle)
        when there's already an autonomous LM task in flight. `io` and
        `free` classes always run when due.

        Dispatch via `asyncio.create_task` so a slow handler can't block
        the rest of the cycle. Tracks in-flight per name (in-memory!) to
        avoid firing the same entry twice while it's still running.
        """
        # Lazy build with retry. If the import fails (e.g. a circular
        # import during refactors), we retry every ~60s — never pin to
        # [] for the process lifetime.
        registry = getattr(self, "_periodic_registry", None)
        if registry is None and (time.time() - self._last_registry_build_attempt) > 60.0:
            self._last_registry_build_attempt = time.time()
            try:
                self._periodic_registry = _build_periodic_registry()
                registry = self._periodic_registry
            except Exception as e:
                bus.publish("orchestrator.init_error", "daemon",
                            {"error": str(e)[:200]})
                # Leave _periodic_registry as None so we retry next pass.
                return 0
        # In-memory in-flight map; never persisted, so a crash can't
        # leave a stale lock on disk. Initialize unconditionally so the
        # attribute is always present even when the registry is empty.
        if self._periodic_in_flight is None:
            self._periodic_in_flight = {}
        in_flight = self._periodic_in_flight

        if not registry:
            return 0

        last_run = self._state.setdefault("periodic_last_run", {})
        now = time.time()
        level = int(self._state.get("level", 0))
        actions = 0

        for entry in registry:
            if level < entry.min_autonomy_level:
                continue
            if in_flight.get(entry.name):
                continue  # previous run still going; skip without bumping
            last = float(last_run.get(entry.name, 0))
            if now - last < entry.interval_s:
                continue
            if entry.bp_class == "lm" and self._in_flight > 0:
                continue  # backpressure: hold lm-class tasks under load

            async def _run_one(e=entry):
                try:
                    in_flight[e.name] = True
                    await e.handler()
                    bus.publish("orchestrator.dispatched", "daemon", {
                        "name": e.name, "interval_s": e.interval_s,
                    })
                except Exception as ex:
                    bus.publish("orchestrator.handler_error", "daemon", {
                        "name": e.name, "error": str(ex)[:200],
                    })
                finally:
                    in_flight[e.name] = False
                    last_run[e.name] = time.time()
                    # _save() coalesced — the main cycle saves at the
                    # end anyway. Skipping the per-task save avoids the
                    # race the critic flagged (concurrent _run_one writes
                    # clobbering each other).

            asyncio.create_task(_run_one())
            actions += 1

        return actions

    async def _run_due_bots(self) -> int:
        """Dispatch any bot whose schedule interval has elapsed since its last
        run. B3 — additionally emit `bot.heartbeat` for every scheduled bot
        each cycle so the dashboard can show 'configured but not running' if
        a bot stays silent past 2× its interval.

        Surfaces `bot_last_error` for any unresolved/failed bot — read by
        /bots/status and rendered in the Bots-mode expansion so the user
        can see *why* a bot is red, not just that it is."""
        bot_last_run = self._state.setdefault("bot_last_run", {})
        bot_last_status = self._state.setdefault("bot_last_status", {})
        bot_last_error = self._state.setdefault("bot_last_error", {})
        now = time.time()
        actions = 0

        for bot_id, interval in _BOT_SCHEDULE.items():
            last = float(bot_last_run.get(bot_id, 0))
            status = bot_last_status.get(bot_id, "never_run")

            # heartbeat — published every cycle regardless of dispatch so
            # the dashboard sees a steady liveness signal.
            bus.publish("bot.heartbeat", "daemon", {
                "bot_id": bot_id,
                "last_run_ts": last or None,
                "next_due_ts": (last + interval) if last else now,
                "interval_s": interval,
                "status": status,
            })

            if now - last < interval:
                continue
            bot, resolve_err = self._resolve_bot(bot_id)
            if bot is None:
                bot_last_status[bot_id] = "unresolved"
                bot_last_error[bot_id] = resolve_err or "no matching bot id"
                continue
            try:
                bus.publish("autonomy.bot_dispatch", "daemon", {
                    "bot": bot_id, "interval_s": interval,
                })
                await asyncio.to_thread(bot.run)
                bot_last_run[bot_id] = now
                bot_last_status[bot_id] = "ok"
                bot_last_error.pop(bot_id, None)
                actions += 1
            except Exception as e:
                bot_last_status[bot_id] = "failed"
                bot_last_error[bot_id] = f"{type(e).__name__}: {str(e)[:240]}"
                bus.publish("autonomy.bot_error", "daemon", {
                    "bot": bot_id, "error": str(e)[:200],
                })

        if actions:
            self._save()
        return actions

    @staticmethod
    def _resolve_bot(bot_id: str) -> tuple[Any, str | None]:
        """Return (bot_instance, None) on success, (None, error_msg) on
        import failure or unknown id. The error_msg surfaces in
        /bots/status so the UI can show why a bot is unresolved."""
        try:
            if bot_id == "memory_gardener":
                from agent.bots.memory_gardener import gardener
                return gardener, None
            if bot_id == "code_health":
                from agent.bots.code_health import code_monitor
                return code_monitor, None
            if bot_id == "performance_watchdog":
                from agent.bots.performance_watchdog import watchdog
                return watchdog, None
            if bot_id == "knowledge_curator":
                from agent.bots.knowledge_curator import curator
                return curator, None
            if bot_id == "homelab_warden":
                from agent.bots.homelab_warden import warden
                return warden, None
        except Exception as e:
            return None, f"{type(e).__name__}: {str(e)[:240]}"
        return None, f"unknown bot id: {bot_id}"

    async def _proactive_cycle(self) -> int:
        """Level 2: publish suggestions, run system metrics, surface a single
        inactivity-aware notification when the user has been idle (B6.7)."""
        actions = 0
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory()
            if cpu > 85:
                bus.publish("autonomy.suggestion", "daemon", {
                    "suggestion": f"CPU is at {cpu:.0f}% — consider closing idle apps.",
                    "priority": "medium",
                })
                actions += 1
            if ram.percent > 88:
                bus.publish("autonomy.suggestion", "daemon", {
                    "suggestion": f"RAM at {ram.percent:.0f}% — memory pressure detected.",
                    "priority": "medium",
                })
                actions += 1
        except Exception:
            pass

        # inactivity awareness. After ~20 min idle, fire ONE
        # nudge from a rotating pool, then go quiet again until activity
        # resumes. Single notification, not a storm.
        actions += self._inactivity_nudge()
        return actions

    # single nudge per idle stretch. Resets when the user records
    # any activity (chat, settings POST, etc. — see record_user_activity).
    _IDLE_THRESHOLD_S = 20 * 60       # 20 min
    _IDLE_NUDGE_COOLDOWN_S = 60 * 60  # same idle stretch: at most one nudge/hour

    def _inactivity_nudge(self) -> int:
        """fire exactly one helpful notification per long-idle
        stretch. Choice rotates between: bus summary, brain stale-note
        nudge, doc-wishlist suggestion. Returns 1 on fire, 0 on skip."""
        idle_s = time.time() - self._last_user_activity
        if idle_s < self._IDLE_THRESHOLD_S:
            return 0
        last = self._state.get("idle_nudge_last", 0.0)
        # Has the idle stretch had its one nudge already? Cooldown rolls
        # forward — we don't pile up multiple nudges in the same idle
        # window even if a user steps away for hours.
        if last and (time.time() - last) < self._IDLE_NUDGE_COOLDOWN_S:
            return 0
        # And: don't fire if the user touched anything since the last nudge.
        if last and self._last_user_activity > last:
            self._state["idle_nudge_last"] = 0  # reset for next stretch
            return 0

        # Choose a flavor based on which signal is most actionable.
        # Reads recent bus to decide; never blocks on LM.
        flavor, body, cta_topic = self._pick_idle_nudge()
        bus.publish("autonomy.suggestion", "daemon", {
            "suggestion": body,
            "priority":   "low",
            "kind":       "inactivity",
            "flavor":     flavor,
            "cta_topic":  cta_topic,
            "idle_min":   int(idle_s / 60),
        })
        bus.publish("aliveness.notification", "daemon", {
            "category": "inactivity",
            "body":     body,
            "cta":      cta_topic,
            "ts":       time.time(),
        })
        self._state["idle_nudge_last"] = time.time()
        self._save()
        return 1

    def _pick_idle_nudge(self) -> tuple[str, str, str]:
        """choose what kind of idle nudge to surface this stretch.
        Order: brain stale-note > bus summary > doc-wishlist. The first
        signal with real material wins so the user gets actionable
        nudges, not just generic 'still here'."""
        # 1. Brain — daily-note untouched today?
        try:
            from plugins.second_brain.plugin import brain_today
            today = brain_today()
            path = today.get("path") if isinstance(today, dict) else None
            if path:
                from pathlib import Path as _P
                p = _P(path)
                if p.exists() and (time.time() - p.stat().st_mtime) > 8 * 3600:
                    return ("brain_stale",
                            "Today's daily note hasn't been touched since this morning — want me to draft a quick recap?",
                            "ui.navigate.brain")
        except Exception:
            pass
        # 2. Bus summary — anything interesting happened in last hour?
        try:
            recent = bus.recent(60)
            if recent:
                # count distinct topics
                topics = {e.get("topic", "") for e in recent}
                if len(topics) >= 5:
                    return ("bus_summary",
                            f"It's been quiet on your end — JARVIS handled {len(recent)} events across {len(topics)} channels. Want a recap?",
                            "ui.navigate.theater")
        except Exception:
            pass
        # 3. Default — gentle ambient nudge with no specific CTA.
        return ("ambient",
                "Idle for a bit — drop a note in the brain or kick a goal whenever you're ready.",
                "ui.navigate.brain")

    async def _full_auto_cycle(self) -> int:
        """Level 3: pursue standing goals + dispatch from drive thresholds (F5)
        + auto-generate goals when drives/emotions cross thresholds (B6.4)."""
        from agent.core.swarm import director
        actions = 0

        # emotion/drive-driven goal generation. Runs BEFORE drive
        # dispatch so newly-minted goals can be picked up the same cycle.
        actions += self._generate_self_goals()

        # F5.1 — drive-derived dispatch BEFORE standing goals so urgent
        # drives jump the queue ahead of long-form goals.
        actions += await self._drive_dispatch()

        for goal in list(self._state["standing_goals"])[:2]:  # max 2 per cycle
            if not self._reserve_slot():
                self._queue_dispatch({"kind": "standing_goal", "goal": goal})
                continue
            try:
                bus.publish("autonomy.pursuing_goal", "daemon", {"goal": goal[:200]})
                # F5.2 — origin tag on audit trail for accountability.
                _audit_drive_action(
                    tool_name="director.run_goal",
                    action_type="standing_goal",
                    origin="user_goal",
                    detail=goal[:200],
                )
                result = await asyncio.wait_for(
                    director.run_goal(goal, depth=0),
                    timeout=self._state["cycle_budget"] * 15,
                )
                bus.publish("autonomy.goal_done", "daemon", {
                    "goal": goal[:200], "result": result[:300],
                })
                # D5 — goal produced output, reinforce its decay clock.
                try:
                    self.reinforce_goal(goal)
                except Exception:
                    pass
                actions += 1
            except Exception as e:
                bus.publish("autonomy.goal_error", "daemon",
                            {"goal": goal[:200], "error": str(e)[:200]})
            finally:
                self._release_slot()
        return actions

    # ── F5 helpers ─────────────────────────────────────────────────────────

    def _reserve_slot(self) -> bool:
        """F5.3 — try to claim a slot under the in-flight cap. False if full."""
        if self._in_flight >= _MAX_AUTONOMOUS_IN_FLIGHT:
            return False
        self._in_flight += 1
        bus.publish("autonomy.slot_change", "daemon", {
            "in_flight": self._in_flight, "cap": _MAX_AUTONOMOUS_IN_FLIGHT,
        })
        return True

    def _release_slot(self) -> None:
        if self._in_flight > 0:
            self._in_flight -= 1
        bus.publish("autonomy.slot_change", "daemon", {
            "in_flight": self._in_flight, "cap": _MAX_AUTONOMOUS_IN_FLIGHT,
        })
        # Drain one queued dispatch if any.
        if self._queued_dispatches and self._in_flight < _MAX_AUTONOMOUS_IN_FLIGHT:
            item = self._queued_dispatches.pop(0)
            bus.publish("autonomy.dispatch_unqueued", "daemon", item)

    def _queue_dispatch(self, item: dict[str, Any]) -> None:
        """Park a would-be dispatch when at cap (max 50 in queue)."""
        self._queued_dispatches = (self._queued_dispatches + [item])[-50:]
        bus.publish("autonomy.dispatch_queued", "daemon", {
            "kind": item.get("kind"), "queue_size": len(self._queued_dispatches),
        })

    def slots(self) -> dict[str, Any]:
        return {
            "in_flight": self._in_flight,
            "cap": _MAX_AUTONOMOUS_IN_FLIGHT,
            "queued": len(self._queued_dispatches),
        }

    # templates for self-initiated goals. Source dim → goal text.
    # When a dim is above _DRIVE_TRIGGER (0.7) AND its cooldown has expired,
    # the matching goal is appended to standing_goals tagged origin=drive_auto
    # in the audit log. Goals respect existing DANGER tier confirms when they
    # eventually run through director.run_goal.
    _SELF_GOAL_TEMPLATES: dict[str, str] = {
        # Emotions (read from _internal_state)
        "BOREDOM":     "Pick an unused plugin and write a one-paragraph "
                       "capability summary into the brain.",
        "CURIOSITY":   "Take the most-frequent recent topic and draft a "
                       "follow-up question I'd want answered.",
        # Drives (read from drives.get_state)
        "LEARNING":    "Generate a 'lessons learned' note from the last 24h "
                       "of agent outcomes.",
        "MAINTENANCE": "Run Memory Gardener + Code Health and roll the "
                       "findings into a single brain note.",
    }
    _SELF_GOAL_COOLDOWN_S = 4 * 3600   # 4h per dim — prevents goal-spam

    def _generate_self_goals(self) -> int:
        """generate up to one new standing goal per elevated dim
        per tick. Reads both drives and emotions; the highest-priority
        unrepeated dim above _DRIVE_TRIGGER produces its template goal.

        - Cooldown is per dim (4h) so a stuck-high signal can't goal-spam.
        - Skips dims whose template goal is already in standing_goals.
        - Audit trail tags every generated goal `origin=drive_auto`.
        - Emits `goal.added` + `autonomy.self_goal` so the dashboard sees
          which signal produced the goal.
        """
        from agent.core import drives
        now = time.time()
        last = self._state.setdefault("self_goal_last", {})

        # Build {dim: level} from both sources.
        levels: dict[str, float] = {}
        levels.update(drives.get_state())
        levels.update(_internal_state.snapshot()["state"])

        # Pick the highest dim that has a template, is above threshold,
        # not on cooldown, and whose template isn't already a standing goal.
        existing = set(self._state["standing_goals"])
        candidates = []
        for dim, lvl in levels.items():
            template = self._SELF_GOAL_TEMPLATES.get(dim.upper())
            if not template or lvl < _DRIVE_TRIGGER:
                continue
            if template in existing:
                continue
            if now - last.get(dim, 0) < self._SELF_GOAL_COOLDOWN_S:
                continue
            candidates.append((lvl, dim, template))

        if not candidates:
            return 0

        # One per cycle — pick the most elevated dim. Avoids opening the
        # standing-goal list to a flood of auto-generated work.
        candidates.sort(reverse=True)
        lvl, dim, template = candidates[0]

        self._state["standing_goals"].append(template)
        meta = self._state.setdefault("goal_meta", {})
        meta[template] = {
            "created_at": now,
            "last_reinforced_at": now,
            "last_stale_alert": 0.0,
            "origin": "drive_auto",
            "source_dim": dim,
            "source_level": round(lvl, 3),
        }
        last[dim] = now
        self._save()

        _audit_drive_action(
            tool_name="autonomy._generate_self_goals",
            action_type="self_goal",
            origin="drive_auto",
            detail=f"{dim}={lvl:.2f}: {template[:200]}",
        )
        bus.publish("autonomy.self_goal", "daemon", {
            "goal": template, "source_dim": dim, "source_level": round(lvl, 3),
        })
        bus.publish("goal.added", "daemon", {
            "goal": template, "origin": "drive_auto",
        })
        return 1

    async def _drive_dispatch(self) -> int:
        """F5.1 — at level 3, drives above _DRIVE_TRIGGER spawn matching
        bots/agents. Each drive has a cooldown so a stuck-high drive
        doesn't thrash the dispatcher.
        """
        from agent.core import drives
        last = self._state.setdefault("drive_last_dispatch", {})
        now = time.time()
        actions = 0
        levels = drives.get_state()  # {CURIOSITY, MAINTENANCE, LEARNING}

        plan: list[tuple[str, str]] = []  # (drive_name, handler_key)
        for drive_name in ("CURIOSITY", "LEARNING", "MAINTENANCE"):
            level = float(levels.get(drive_name, 0.0))
            if level < _DRIVE_TRIGGER:
                continue
            if now - float(last.get(drive_name, 0.0)) < _DRIVE_COOLDOWN_S:
                continue
            plan.append((drive_name, drive_name))

        for drive_name, _handler in plan:
            if not self._reserve_slot():
                self._queue_dispatch({"kind": "drive", "drive": drive_name})
                continue
            try:
                bus.publish("autonomy.drive_dispatch", "daemon", {
                    "drive": drive_name, "level": float(levels.get(drive_name, 0.0)),
                    "in_flight": self._in_flight,
                })
                # F5.2 — every drive-spawned action carries origin=drive_auto.
                _audit_drive_action(
                    tool_name=f"drive.{drive_name.lower()}",
                    action_type="drive_dispatch",
                    origin="drive_auto",
                    detail=f"{drive_name}={levels.get(drive_name, 0.0):.2f}",
                )
                ok = await self._handle_drive(drive_name)
                if ok:
                    last[drive_name] = now
                    actions += 1
                    # Reward the drive — fulfilling it drops its level.
                    drives.bump(drive_name, 0.25)
            except Exception as e:
                bus.publish("autonomy.drive_error", "daemon", {
                    "drive": drive_name, "error": str(e)[:200],
                })
            finally:
                self._release_slot()
        if actions:
            self._save()
        return actions

    async def _handle_drive(self, drive_name: str) -> bool:
        """Route a high drive to the right bot/agent. Respects each handler's
        F1 min_autonomy_level (must be <= current daemon level)."""
        level = self._state["level"]

        if drive_name == "MAINTENANCE":
            from agent.bots.memory_gardener import gardener, MemoryGardener
            if level >= MemoryGardener.min_autonomy_level:
                await asyncio.to_thread(gardener.run)
                return True
            return False

        if drive_name == "CURIOSITY":
            # Top up the curiosity queue and broadcast intent. ResearchAgent
            # spawn happens out-of-band once the user clicks the CTA OR
            # when standing_goals are seeded from a candidate (B6.4 path).
            from agent.core import curiosity as cur
            if level >= getattr(cur, "min_autonomy_level", 2):
                gen = await asyncio.to_thread(cur.generate, 1)
                items = gen.get("items") or []
                if items:
                    bus.publish("curiosity.action.run", "daemon", {
                        "topic": items[0].get("topic"),
                        "candidate_id": items[0].get("id"),
                        "origin": "drive_auto",
                    })
                return True
            return False

        if drive_name == "LEARNING":
            from agent.core import learning_tracks as lt
            if level >= getattr(lt, "min_autonomy_level", 2):
                due = await asyncio.to_thread(lt.due_tracks)
                if not due:
                    return False
                track_id = due[0]
                track = await asyncio.to_thread(lt.get_track, track_id)
                if not track or not track.get("current_topic"):
                    return False
                bus.publish("research.gap.fill", "daemon", {
                    "topic":    track["current_topic"],
                    "track_id": track_id,
                    "origin":   "drive_auto",
                })
                return True
            return False

        return False


# ─── Internal State / Emotion System (3.2, B6.1) ──────────────────────────────

_EMOTIONS = ["CURIOSITY", "FOCUS", "FRUSTRATION", "SATISFACTION", "BOREDOM"]

# per-dimension decay rates. Frustration is sticky (low rate); satisfaction
# fades fast; curiosity/focus/boredom drift at the standard rate. Boredom is
# pushed UP by inactivity in tick() (handled separately) — its decay here only
# kicks in when the user is active.
_DECAY_RATES: dict[str, float] = {
    "CURIOSITY":    0.02,
    "FOCUS":        0.02,
    "FRUSTRATION": 0.005,    # 4x slower — frustration sticks
    "SATISFACTION": 0.04,    # 2x faster — satisfaction fades
    "BOREDOM":      0.02,
}
_BASELINE = 0.15

# boredom growth per minute of idle elapsed since last user activity
_BOREDOM_PER_MIN_IDLE = 0.01

# Persisted emotion log — last 24h of nudges + compound moods. Trimmed at boot.
_EMOTION_LOG = Path.home() / ".jarvis" / "emotion_log.jsonl"

# bus topic → list of (dim, delta, reason) triggers. The autonomy daemon
# drains the bus in each tick and applies these so emotion stays in sync with
# what JARVIS is actually doing, not just what we manually nudge.
_EVENT_TRIGGERS: dict[str, list[tuple[str, float, str]]] = {
    "agent.failed":             [("FRUSTRATION",  0.15, "agent failed")],
    "agent.completed":          [("SATISFACTION", 0.10, "agent completed"),
                                 ("BOREDOM",     -0.05, "agent completed")],
    "agent.started":            [("FOCUS",        0.10, "agent started")],
    "research.gap":             [("CURIOSITY",    0.10, "research gap")],
    "research.gap.fill":        [("CURIOSITY",    0.05, "research gap filled")],
    "learning.completed":       [("SATISFACTION", 0.10, "learning topic done"),
                                 ("CURIOSITY",   -0.05, "learning topic done")],
    "digest.composed":          [("SATISFACTION", 0.05, "daily digest")],
    "curiosity.acted":          [("SATISFACTION", 0.05, "curiosity acted")],
    "curiosity.generated":      [("CURIOSITY",    0.03, "new curiosity seed")],
}

# compound moods. Both dims must exceed the threshold for the compound
# to fire. Order matters: first match wins.
_COMPOUND_THRESHOLD = 0.55
_COMPOUND_MOODS: list[tuple[str, str, str]] = [
    # (compound_name, dim_a, dim_b)
    ("flow",         "FOCUS",        "SATISFACTION"),
    ("stuck",        "FOCUS",        "FRUSTRATION"),
    ("exploratory",  "SATISFACTION", "CURIOSITY"),
    ("restless",     "CURIOSITY",    "BOREDOM"),
    ("overwhelmed",  "FRUSTRATION",  "BOREDOM"),
]


class InternalState:
    """JARVIS internal emotion vector — influences behavior, surfaced to UI.

    Each dimension is a float 0.0–1.0 with its own decay curve (see
    `_DECAY_RATES`). B6.1 added:
    - per-dimension decay (frustration sticky, satisfaction fast, boredom
      from idle, curiosity/focus standard)
    - compound moods (flow / stuck / exploratory / restless / overwhelmed)
    - event-driven triggers via `apply_event(topic, payload)`
    - persisted log at `~/.jarvis/emotion_log.jsonl` (last 24h)
    - top-triggers aggregation for the Settings transparency panel
    - per-dim reset knob
    """

    def __init__(self):
        self._v: dict[str, float] = {e: 0.1 for e in _EMOTIONS}
        self._log: list[dict] = []   # in-memory tail of last 200 events
        self._load_persisted()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load_persisted(self) -> None:
        """Load the in-memory tail from the JSONL log, dropping entries
        older than 24h. Safe if the file is missing or corrupted."""
        if not _EMOTION_LOG.exists():
            return
        cutoff = time.time() - 86400
        try:
            kept = []
            for line in _EMOTION_LOG.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("ts", 0) >= cutoff:
                    kept.append(entry)
            self._log = kept[-200:]
        except Exception:
            pass

    def _persist(self, entry: dict) -> None:
        try:
            _EMOTION_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _EMOTION_LOG.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    # ── Mutations ───────────────────────────────────────────────────────────

    def nudge(self, dim: str, delta: float, reason: str = "") -> None:
        dim = dim.upper()
        if dim not in self._v:
            return
        self._v[dim] = max(0.0, min(1.0, self._v[dim] + delta))
        entry = {
            "ts": time.time(),
            "dim": dim,
            "delta": round(delta, 3),
            "val": round(self._v[dim], 3),
            "reason": reason,
        }
        self._log.append(entry)
        if len(self._log) > 200:
            self._log = self._log[-200:]
        self._persist(entry)
        bus.publish("emotion.update", "internal_state", entry)

    def reset(self, dim: str, reason: str = "manual reset") -> None:
        """clear a single dimension back to baseline. Surfaces in the
        Settings transparency panel as the per-dim reset knob."""
        dim = dim.upper()
        if dim not in self._v:
            return
        delta = round(_BASELINE - self._v[dim], 3)
        self._v[dim] = _BASELINE
        entry = {
            "ts": time.time(), "dim": dim, "delta": delta,
            "val": _BASELINE, "reason": reason, "kind": "reset",
        }
        self._log.append(entry)
        self._persist(entry)
        bus.publish("emotion.update", "internal_state", entry)

    def decay(self):
        """per-dimension decay toward the resting baseline. Each dim
        uses its own rate from `_DECAY_RATES` so frustration lingers and
        satisfaction fades, matching the lived experience the model should
        reflect."""
        for k in self._v:
            rate = _DECAY_RATES.get(k, 0.02)
            self._v[k] += (_BASELINE - self._v[k]) * rate
            self._v[k] = round(self._v[k], 4)

    def apply_idle(self, idle_seconds: float) -> None:
        """boredom grows linearly with idle time. Called once per
        autonomy tick with the current idle duration. Resets on any
        observable user activity (chat input, settings POST, etc.)."""
        if idle_seconds <= 60:
            return
        minutes_idle = idle_seconds / 60
        # Cap delta per tick so a long idle doesn't spike instantly.
        delta = min(0.10, minutes_idle * _BOREDOM_PER_MIN_IDLE)
        if delta > 0.005:
            self.nudge("BOREDOM", delta, f"idle {int(minutes_idle)}min")

    def apply_event(self, topic: str, payload: dict | None = None) -> int:
        """translate a bus event into one or more nudges per
        `_EVENT_TRIGGERS`. Returns the count of nudges applied so the
        daemon can log how much state moved this tick."""
        triggers = _EVENT_TRIGGERS.get(topic, [])
        if not triggers:
            return 0
        for dim, delta, reason in triggers:
            self.nudge(dim, delta, f"{topic}: {reason}")
        return len(triggers)

    # ── Queries ─────────────────────────────────────────────────────────────

    def dominant(self) -> str:
        return max(self._v, key=lambda k: self._v[k])

    def compound_moods(self) -> list[str]:
        """return every active compound mood (both dims > threshold)."""
        return [
            name for name, a, b in _COMPOUND_MOODS
            if self._v[a] > _COMPOUND_THRESHOLD and self._v[b] > _COMPOUND_THRESHOLD
        ]

    def top_triggers(self, hours: int = 24, limit: int = 3) -> list[dict]:
        """frequency-sorted list of the most-cited `reason` strings
        in the in-memory log over the last N hours. Powers the Settings
        transparency panel's "what caused this mood" section."""
        cutoff = time.time() - hours * 3600
        counts: dict[str, dict] = {}
        for entry in self._log:
            if entry.get("ts", 0) < cutoff:
                continue
            reason = entry.get("reason") or "(unspecified)"
            rec = counts.setdefault(reason, {
                "reason": reason, "count": 0, "total_delta": 0.0,
            })
            rec["count"] += 1
            rec["total_delta"] += float(entry.get("delta", 0.0))
        ranked = sorted(counts.values(), key=lambda r: -r["count"])
        return ranked[:limit]

    def snapshot(self) -> dict:
        return {
            "state": dict(self._v),
            "dominant": self.dominant(),
            "compound_moods": self.compound_moods(),
            "log": self._log[-20:],
        }

    def transparency(self) -> dict:
        """payload for the Settings transparency panel.
        Includes dominant mood, intensity %, compound moods, top 3
        triggers in the last 24h, and the resettable dimensions."""
        dom = self.dominant()
        return {
            "dominant":       dom,
            "intensity_pct":  round(self._v[dom] * 100, 1),
            "state":          dict(self._v),
            "compound_moods": self.compound_moods(),
            "top_triggers":   self.top_triggers(hours=24, limit=3),
            "resettable":     list(self._v.keys()),
            "baseline":       _BASELINE,
        }


# ─── Singletons ───────────────────────────────────────────────────────────────

_internal_state = InternalState()
autonomy = AutonomyDaemon()
