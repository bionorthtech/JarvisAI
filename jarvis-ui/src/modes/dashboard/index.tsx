/**
 * B2 — Dashboard ("Mission Control") mode.
 *
 * Top-level pane summarizing system health: D3 morning brief,
 * D1 health-score arc-reactor, 24h activity report, G6.5 live latency,
 * core metric tiles, director goal submit, active agents, service
 * toggles, merged emotion+drive state, D7 bot personality cards,
 * autonomy ladder, D5 standing goals, AI wants/needs, thought stream,
 * D6 task replay, live event feed.
 *
 * Polls many endpoints on a 20s cadence; also subscribes to /ws/live
 * for system.metrics / system.drive_alert / emotion.update /
 * thought.broadcast deltas.
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  LayoutDashboard, BookOpen, Activity, Bot, Zap, Layers, Brain, Database,
  Cpu, HardDrive, Eye, RefreshCw,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { AnimatePresence, motion } from "framer-motion";

import { BACKEND, WS_BACKEND } from "../../config";
import type { LMStatus, MemoryStats } from "../../types";
import { useTheme } from "../../hooks/useTheme";
import { useLiveWS } from "../../hooks/useLiveWS";
import { usePolling } from "../../hooks/usePolling";
import PaneHeader from "../../components/PaneHeader";
import { MetricCard, Card, Section } from "../../components/widgets";

// ─── D6 Replay Pane (only used by Dashboard) ──────────────────────────────────

type ReplayTask = {
  task_id: string; task_desc: string; agent_type: string;
  first_ts: number; last_ts: number; event_count: number;
};
type ReplayEvent = {
  id?: number; ts: number; topic: string; sender?: string;
  task_id?: string; message?: string; result_preview?: string;
  error?: string; agent_id?: string;
};

function replayTopicColor(topic: string): string {
  if (topic.startsWith("agent.completed") || topic.endsWith(".done")) return "text-emerald-400";
  if (topic.includes("error") || topic.includes("fail")) return "text-red-400";
  if (topic.startsWith("agent.")) return "text-sky-400";
  if (topic.startsWith("autonomy.")) return "text-amber-400";
  if (topic.startsWith("goal.")) return "text-fuchsia-400";
  return "text-[var(--text-muted)]";
}

function ReplayPane() {
  const { t } = useTheme();
  const [tasks, setTasks] = useState<ReplayTask[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<ReplayEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const loadTasks = useCallback(() => {
    fetch(`${BACKEND}/tasks/recent?limit=20`)
      .then(r => r.json()).then(d => setTasks(d.tasks ?? []))
      .catch(() => {});
  }, []);
  useEffect(() => { if (open) loadTasks(); }, [open, loadTasks]);

  const loadReplay = async (task_id: string) => {
    setBusy(true);
    setSelected(task_id);
    try {
      const r = await fetch(`${BACKEND}/tasks/${task_id}/replay?limit=500`);
      if (r.ok) {
        const d = await r.json();
        setEvents(d.events ?? []);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Task Replay"
      actions={
        <button onClick={() => setOpen(o => !o)}
          className="text-[9px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)]">
          {open ? "hide" : "open"}
        </button>
      }
    >
      {open && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <p className="text-[10px] text-[var(--text-subtle)] flex-1">
              Pick a past task to re-render its full bus event chain.
            </p>
            <button onClick={loadTasks}
              className="text-[10px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] flex items-center gap-1">
              <RefreshCw size={10} /> Refresh
            </button>
          </div>
          {tasks.length === 0 ? (
            <p className="text-[10px] text-[var(--text-subtle)]">No tasks yet. Run a goal to seed the list.</p>
          ) : (
            <div className="space-y-1 max-h-40 overflow-y-auto">
              {tasks.map(task => (
                <button key={task.task_id} onClick={() => loadReplay(task.task_id)}
                  className={`w-full text-left px-2 py-1 rounded border text-[10px] transition-all ${selected === task.task_id ? `${t.accentBorder} ${t.accentBg} ${t.accent}` : "border-white/[0.04] text-[var(--text-muted)] hover:text-[var(--text)] hover:border-white/10"}`}>
                  <div className="flex items-baseline gap-2">
                    <span className="font-mono text-[9px] text-[var(--text-subtle)] shrink-0">{task.task_id.slice(0, 8)}</span>
                    <span className="text-[10px] truncate flex-1">{task.task_desc || "(no description)"}</span>
                    <span className="text-[9px] text-[var(--text-subtle)] shrink-0">{task.event_count} events</span>
                  </div>
                  <div className="text-[9px] text-[var(--text-subtle)]">
                    {task.agent_type} · {new Date(task.first_ts * 1000).toLocaleString()}
                  </div>
                </button>
              ))}
            </div>
          )}
          {selected && (
            <div className="bg-[var(--surface-2)] border border-white/[0.04] rounded p-2 max-h-72 overflow-y-auto font-mono">
              {busy && <p className="text-[10px] text-[var(--text-subtle)]">Loading replay…</p>}
              {!busy && events.length === 0 && (
                <p className="text-[10px] text-[var(--text-subtle)]">No events found for this task.</p>
              )}
              {events.map((ev, i) => (
                <div key={ev.id ?? i} className="flex items-start gap-2 py-0.5 border-b border-white/[0.02] last:border-0">
                  <span className="text-[9px] text-[var(--text-subtle)] shrink-0 tabular-nums">
                    {new Date(ev.ts * 1000).toLocaleTimeString()}
                  </span>
                  <span className={`text-[9px] shrink-0 ${replayTopicColor(ev.topic)}`}>{ev.topic}</span>
                  <span className="text-[9px] text-[var(--text-muted)] break-all">
                    {ev.sender && <span className="text-[var(--text-subtle)]">[{ev.sender}] </span>}
                    {ev.message ?? ev.result_preview ?? ev.error ?? ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export default function DashboardPane({ lm, processing, memStats }: { lm: LMStatus; processing: boolean; memStats: MemoryStats }) {
  const { t } = useTheme();
  const { events, connected: wsConnected } = useLiveWS(`${WS_BACKEND}/ws/live`);

  const [drives, setDrives]       = useState<Record<string, number>>({});
  const [swarm, setSwarm]         = useState<{
    active_tasks: number; active_agents: number;
    agents: {
      id: string; type: string; name: string; status: string; task: string;
      step?: string; elapsed_s?: number;
      tokens_used?: number; shell_calls_used?: number;
    }[];
  }>({ active_tasks: 0, active_agents: 0, agents: [] });
  const [sysMetrics, setSysMetrics] = useState<{ cpu_pct: number; ram_pct: number; ram_used_mb: number; ram_total_mb: number; disk_pct: number; disk_free_gb: number } | null>(null);
  const [goalInput, setGoalInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [emotionState, setEmotionState] = useState<{ state: Record<string, number>; dominant: string } | null>(null);
  const [autonomyStatus, setAutonomyStatus] = useState<{ level: number; level_name: string; cycles_run: number; actions_taken: number } | null>(null);
  // D1 — JARVIS health score
  type HealthComponent = { name: string; weight: number; value: number;
    detail: string; nudge: string | null };
  type HealthScore = { ts: number; score: number; mood: string;
    components: HealthComponent[]; pulled_down_by: string[] };
  const [health, setHealth] = useState<HealthScore | null>(null);
  // D3 — morning briefing
  const [morningBrief, setMorningBrief] = useState<string>("");
  const [morningBusy, setMorningBusy] = useState(false);
  const composeMorningBrief = async () => {
    setMorningBusy(true);
    try {
      const r = await fetch(`${BACKEND}/morning/compose?force=true`, { method: "POST" });
      const d = await r.json();
      if (d.ok && d.preview) {
        const t2 = await fetch(`${BACKEND}/morning/today`);
        const dd = await t2.json();
        setMorningBrief(dd.markdown || "");
      }
    } finally {
      setMorningBusy(false);
    }
  };

  // D7 — personality cards
  type PersonalityCard = { id: string; class_name: string; text: string;
    generated_at: number | null; stale: boolean };
  const [personalityCards, setPersonalityCards] = useState<PersonalityCard[]>([]);
  const [regenBusy, setRegenBusy] = useState<string | null>(null);
  const loadPersonalityCards = useCallback(() => {
    fetch(`${BACKEND}/personality-cards`).then(r => r.json())
      .then(d => setPersonalityCards(d.cards ?? [])).catch(() => {});
  }, []);
  const regenerateCard = async (id: string) => {
    setRegenBusy(id);
    try {
      await fetch(`${BACKEND}/personality-cards/regenerate/${id}`, { method: "POST" });
      loadPersonalityCards();
    } finally {
      setRegenBusy(null);
    }
  };
  const fillAllCards = async () => {
    setRegenBusy("__all__");
    try {
      await fetch(`${BACKEND}/personality-cards/fill`, { method: "POST" });
      loadPersonalityCards();
    } finally {
      setRegenBusy(null);
    }
  };
  // C5.3 — Homelab warden (failed services + down containers + journal errors)
  type HomelabFinding = { kind: string; id: string; detail: string };
  type HomelabReport = {
    ts: number; duration_s: number;
    failed_units: { unit: string; description: string }[];
    containers_down: { docker: { name: string; image: string; status: string }[];
                       podman: { name: string; image: string; status: string }[] };
    journal_errors_10min: { available: boolean; count: number };
    findings: HomelabFinding[];
    summary: string;
  };
  const [homelab, setHomelab] = useState<HomelabReport | null>(null);
  const [homelabBusy, setHomelabBusy] = useState(false);
  const loadHomelab = useCallback(async () => {
    setHomelabBusy(true);
    try {
      const r = await fetch(`${BACKEND}/bots/homelab-warden/run`, { method: "POST" });
      if (r.ok) setHomelab(await r.json());
    } catch { /* keep last */ }
    finally { setHomelabBusy(false); }
  }, []);
  useEffect(() => { loadHomelab(); }, [loadHomelab]);

  // Two-click restart confirm: click 1 arms the row (3s window),
  // click 2 commits. Avoids needing the full LM-tool confirm flow
  // for an explicit user-initiated UI action.
  const [armedRestart, setArmedRestart] = useState<string | null>(null);
  const [restartingId, setRestartingId] = useState<string | null>(null);
  const [restartMsg, setRestartMsg] = useState<string | null>(null);
  const armRestart = (key: string) => {
    setArmedRestart(key);
    setRestartMsg(null);
    window.setTimeout(() => setArmedRestart(k => (k === key ? null : k)), 3000);
  };
  const commitRestart = async (
    key: string, kind: "service" | "container",
    id: string, engine?: "docker" | "podman",
  ) => {
    setRestartingId(key);
    setArmedRestart(null);
    try {
      const r = await fetch(`${BACKEND}/bots/homelab-warden/restart`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, id, engine }),
      });
      const d = await r.json();
      setRestartMsg(d.ok ? `✓ restarted ${id}` : `✗ ${d.error || "failed"}`);
      loadHomelab();
    } catch (e) {
      setRestartMsg(`✗ ${e}`);
    } finally {
      setRestartingId(null);
    }
  };

  // D7.6 — plugin personality cards (parallel of D7 for bots)
  type PluginCard = { id: string; slug: string; plugin_name: string;
    description: string; tool_count: number; text: string;
    generated_at: number | null; stale: boolean };
  const [pluginCards, setPluginCards] = useState<PluginCard[]>([]);
  const [pluginRegenBusy, setPluginRegenBusy] = useState<string | null>(null);
  const loadPluginCards = useCallback(() => {
    fetch(`${BACKEND}/plugin-cards`).then(r => r.json())
      .then(d => setPluginCards(d.cards ?? [])).catch(() => {});
  }, []);
  const regeneratePluginCard = async (slug: string) => {
    setPluginRegenBusy(slug);
    try {
      await fetch(`${BACKEND}/plugin-cards/regenerate/${slug}`, { method: "POST" });
      loadPluginCards();
    } finally {
      setPluginRegenBusy(null);
    }
  };
  const fillAllPluginCards = async () => {
    setPluginRegenBusy("__all__");
    try {
      await fetch(`${BACKEND}/plugin-cards/fill`, { method: "POST" });
      loadPluginCards();
    } finally {
      setPluginRegenBusy(null);
    }
  };
  // D5 — standing goals with decay metadata
  type StandingGoal = { goal: string; created_at: number;
    last_reinforced_at: number; age_days: number; is_stale: boolean };
  const [standingGoals, setStandingGoals] = useState<StandingGoal[]>([]);
  const loadGoals = useCallback(() => {
    fetch(`${BACKEND}/autonomy/goals`).then(r => r.json())
      .then(d => setStandingGoals(d.goals ?? [])).catch(() => {});
  }, []);
  const reinforceGoal = async (goal: string) => {
    await fetch(`${BACKEND}/autonomy/goals/reinforce`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal }),
    }).catch(() => {});
    loadGoals();
  };
  const dropGoal = async (goal: string) => {
    await fetch(`${BACKEND}/autonomy/goals`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal }),
    }).catch(() => {});
    loadGoals();
  };
  const [wants, setWants] = useState<{ id: string; want: string; status: string }[]>([]);
  const [report, setReport] = useState<{ event_count: number; agents: { completed: number; failed: number }; autonomy: { cycles: number }; file_changes: number; truncated?: boolean } | null>(null);
  const [thoughts, setThoughts] = useState<{ ts?: number; thought?: string; sender?: string }[]>([]);
  // G6.5 — live latency snapshot
  type PerfSnap = {
    lm_studio?:  { samples: number; p50_ms: number; p95_ms: number; p99_ms: number };
    chromadb?:   { samples: number; p50_ms: number; p95_ms: number; p99_ms: number };
    websocket?:  { samples: number; p50_ms: number; p95_ms: number };
  };
  const [perf, setPerf] = useState<PerfSnap | null>(null);

  // Refresh drives + swarm status on mount and every 20 s
  const load = useCallback(() => {
    fetch(`${BACKEND}/drives`).then(r => r.json()).then(d => setDrives({ curiosity: d.curiosity, maintenance: d.maintenance, learning: d.learning })).catch(() => {});
    fetch(`${BACKEND}/swarm/status`).then(r => r.json()).then(d => { setSwarm(d); setLastRefreshed(new Date()); }).catch(() => {});
    fetch(`${BACKEND}/emotion/state`).then(r => r.json()).then(d => setEmotionState(d)).catch(() => {});
    fetch(`${BACKEND}/autonomy/status`).then(r => r.json()).then(d => setAutonomyStatus(d)).catch(() => {});
    fetch(`${BACKEND}/autonomy/goals`).then(r => r.json()).then(d => setStandingGoals(d.goals ?? [])).catch(() => {});
    fetch(`${BACKEND}/personality-cards`).then(r => r.json()).then(d => setPersonalityCards(d.cards ?? [])).catch(() => {});
    fetch(`${BACKEND}/plugin-cards`).then(r => r.json()).then(d => setPluginCards(d.cards ?? [])).catch(() => {});
    fetch(`${BACKEND}/health-score`).then(r => r.json()).then(d => setHealth(d)).catch(() => {});
    fetch(`${BACKEND}/morning/today`).then(r => r.json()).then(d => setMorningBrief(d.markdown ?? "")).catch(() => {});
    fetch(`${BACKEND}/wants`).then(r => r.json()).then(d => setWants(d.wants ?? [])).catch(() => {});
    fetch(`${BACKEND}/reports/latest?hours=24`).then(r => r.json()).then(d => setReport(d)).catch(() => {});
    fetch(`${BACKEND}/thoughts/recent?limit=10`).then(r => r.json()).then(d => setThoughts(d.thoughts ?? [])).catch(() => {});
    fetch(`${BACKEND}/perf/live`).then(r => r.json()).then(d => setPerf(d)).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 20_000);

  // Live updates from WebSocket
  useEffect(() => {
    if (!events.length) return;
    const latest = events[0];
    if (latest.topic === "system.metrics") {
      setSysMetrics({
        cpu_pct:      latest.cpu_pct as number,
        ram_pct:      latest.ram_pct as number,
        ram_used_mb:  latest.ram_used_mb as number,
        ram_total_mb: latest.ram_total_mb as number,
        disk_pct:     latest.disk_pct as number,
        disk_free_gb: latest.disk_free_gb as number,
      });
    }
    if (latest.topic === "system.drive_alert") {
      const drive = (latest.drive as string)?.toLowerCase();
      if (drive) setDrives(d => ({ ...d, [drive]: latest.level as number }));
    }
    if (latest.topic === "emotion.update") {
      setEmotionState(prev => {
        if (!prev) return prev;
        const newState = { ...prev.state, [(latest.dim as string)]: latest.val as number };
        const dominant = Object.entries(newState).sort((a, b) => b[1] - a[1])[0]?.[0] ?? prev.dominant;
        return { state: newState, dominant };
      });
    }
    if (latest.topic === "thought.broadcast") {
      setThoughts(prev => [{ ts: latest.ts as number, thought: latest.thought as string, sender: latest.sender }, ...prev.slice(0, 19)]);
    }
  }, [events]);

  const submitGoal = () => {
    if (!goalInput.trim() || submitting) return;
    setSubmitting(true);
    fetch(`${BACKEND}/swarm/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal: goalInput }),
    }).then(() => { setGoalInput(""); }).finally(() => setSubmitting(false));
  };

  const resetDrive = (name: string) => {
    fetch(`${BACKEND}/drives/reset/${name}`, { method: "POST" }).then(() => {
      setDrives(d => ({ ...d, [name.toLowerCase()]: 0 }));
    });
  };

  const driveColors: Record<string, string> = {
    curiosity: "bg-amber-500", maintenance: "bg-blue-500", learning: "bg-emerald-500",
  };
  const driveIcons: Record<string, ReactNode> = {
    curiosity: <Eye size={11} />, maintenance: <HardDrive size={11} />, learning: <BookOpen size={11} />,
  };

  // Color by topic prefix
  const topicColor = (topic: string) => {
    if (topic.startsWith("agent.")) return "text-cyan-400";
    if (topic.startsWith("director.")) return t.accent;
    if (topic.startsWith("security.")) return "text-emerald-400";
    if (topic.startsWith("system.")) return "text-[var(--text-muted)]";
    return "text-[var(--text-subtle)]";
  };

  const agentStatusColor: Record<string, string> = {
    RUNNING: "text-emerald-400", BLOCKED: "text-amber-400",
    DONE: "text-[var(--text-subtle)]", FAILED: "text-red-400", INIT: "text-[var(--text-subtle)]", READY: "text-blue-400",
  };

  return (
    <div className="flex-1 overflow-y-auto">
     <div className="max-w-6xl mx-auto p-6 space-y-8">
      <div className="flex items-center justify-between mb-1">
        <PaneHeader icon={<LayoutDashboard size={13} />} title="Mission control" lastRefreshed={lastRefreshed} />
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? "bg-emerald-400 pulse-glow" : "bg-red-500"}`} />
          <span className="text-[9px] text-[var(--text-subtle)]">{wsConnected ? "live" : "reconnecting…"}</span>
        </div>
      </div>

      <Section title="Today">
      {/* D3 — Morning briefing */}
      <Card
        title="Morning Brief"
        actions={
          <button onClick={composeMorningBrief} disabled={morningBusy}
            className="text-[10px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40">
            {morningBusy ? "Composing…" : "Compose now"}
          </button>
        }
      >
        {morningBrief ? (
          <div className="prose prose-invert prose-sm max-w-none text-[10px] text-[var(--text-muted)]">
            <ReactMarkdown>{morningBrief}</ReactMarkdown>
          </div>
        ) : (
          <p className="text-[10px] text-[var(--text-subtle)]">
            No brief yet. JARVIS writes one after 08:00 local each day, or click compose now.
          </p>
        )}
      </Card>

      {/* D1 — JARVIS Health Score (arc reactor) */}
      {health && (() => {
        const scoreColor = health.score >= 90 ? "text-emerald-400"
          : health.score >= 75 ? "text-cyan-400"
          : health.score >= 60 ? "text-amber-400"
          : "text-red-400";
        const ringColor = health.score >= 90 ? "border-emerald-400/40"
          : health.score >= 75 ? "border-cyan-400/40"
          : health.score >= 60 ? "border-amber-400/40"
          : "border-red-400/40";
        return (
          <Card title="JARVIS Health">
            <div className="flex items-center gap-5">
              <div className={`shrink-0 w-20 h-20 rounded-full border-4 ${ringColor} flex flex-col items-center justify-center`}>
                <span className={`text-[24px] font-bold leading-none tabular-nums ${scoreColor}`}>{Math.round(health.score)}</span>
                <span className="text-[8px] text-[var(--text-subtle)] uppercase tracking-widest mt-0.5">{health.mood}</span>
              </div>
              <div className="flex-1 space-y-1">
                {health.components.map(c => (
                  <div key={c.name} className="flex items-center gap-2 text-[10px]">
                    <span className="text-[var(--text-muted)] w-28 shrink-0">{c.name}</span>
                    <div className="flex-1 h-1 bg-white/[0.04] rounded-full overflow-hidden">
                      <div className={`h-full rounded-full ${c.value >= 90 ? "bg-emerald-400" : c.value >= 75 ? "bg-cyan-400" : c.value >= 60 ? "bg-amber-400" : "bg-red-400"}`}
                        style={{ width: `${c.value}%` }} />
                    </div>
                    <span className="text-[var(--text-subtle)] w-7 tabular-nums text-right">{Math.round(c.value)}</span>
                  </div>
                ))}
                {health.components.some(c => c.nudge) && (
                  <div className="text-[9px] text-amber-400/70 mt-2">
                    {health.components.filter(c => c.nudge).slice(0, 2).map(c => (
                      <div key={c.name}>· {c.nudge}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </Card>
        );
      })()}

      {/* 24h Activity Report (2.3) */}
      {report && (
        <Card
          title="24h Report"
          actions={
            report.truncated ? (
              <span className="text-[8px] text-amber-500" title="Bus window capped; counts are a lower bound.">
                truncated
              </span>
            ) : undefined
          }
        >
          <div className="grid grid-cols-4 gap-3 text-center">
            <div><div className="text-[16px] font-black font-mono text-[var(--text)]">{report.event_count}</div><div className="text-[8px] text-[var(--text-subtle)]">events</div></div>
            <div><div className="text-[16px] font-black font-mono text-emerald-400">{report.agents.completed}</div><div className="text-[8px] text-[var(--text-subtle)]">tasks done</div></div>
            <div><div className={`text-[16px] font-black font-mono ${report.agents.failed > 0 ? "text-red-400" : "text-[var(--text-subtle)]"}`}>{report.agents.failed}</div><div className="text-[8px] text-[var(--text-subtle)]">failed</div></div>
            <div><div className="text-[16px] font-black font-mono text-blue-400">{report.file_changes}</div><div className="text-[8px] text-[var(--text-subtle)]">file changes</div></div>
          </div>
        </Card>
      )}
      </Section>

      <Section title="System">
      <div className="grid grid-cols-3 gap-3">
        <MetricCard label="LM Studio"    val={lm.connected ? "Online" : "Offline"}       ok={lm.connected}   icon={<Bot size={14} />} />
        <MetricCard label="Latency"      val={`${Math.round(lm.latency_ms)}ms`}           ok                  icon={<Activity size={14} />} />
        <MetricCard label="JARVIS"       val={processing ? "Working" : "Idle"}            ok={!processing}    icon={<Zap size={14} />} />
        <MetricCard label="Agents"       val={String(swarm.active_agents)}                ok                  icon={<Layers size={14} />} />
        <MetricCard label="Tasks"        val={String(swarm.active_tasks)}                 ok                  icon={<Brain size={14} />} />
        <MetricCard label="Memory"       val={`${memStats.file_chunks}ch`}                ok                  icon={<Database size={14} />} />
        {sysMetrics && <>
          <MetricCard label="CPU"    val={`${sysMetrics.cpu_pct.toFixed(1)}%`}    ok={sysMetrics.cpu_pct < 80}  icon={<Cpu size={14} />} />
          <MetricCard label="RAM"    val={`${sysMetrics.ram_pct.toFixed(0)}%`}    ok={sysMetrics.ram_pct < 85}  icon={<HardDrive size={14} />} />
          <MetricCard label="Disk"   val={`${sysMetrics.disk_free_gb}GB free`}    ok={sysMetrics.disk_pct < 90} icon={<HardDrive size={14} />} />
        </>}
      </div>

      {/* G6.5 — Live latency (LM + ChromaDB p50/p95) */}
      {perf && ((perf.lm_studio?.samples ?? 0) > 0 || (perf.chromadb?.samples ?? 0) > 0) && (
        <Card
          title="Live Latency"
          actions={
            <span className="text-[9px] text-[var(--text-subtle)]">
              {(perf.lm_studio?.samples ?? 0) + (perf.chromadb?.samples ?? 0)} samples
            </span>
          }
        >
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-[12px] text-[var(--text-subtle)] mb-1">LM Studio</div>
              <div className="flex items-baseline gap-3 font-mono">
                <span className="text-[16px] font-black text-[var(--text)]">{perf.lm_studio?.p50_ms?.toFixed(0) ?? "0"}</span>
                <span className="text-[9px] text-[var(--text-subtle)]">p50</span>
                <span className="text-[12px] text-[var(--text-muted)]">{perf.lm_studio?.p95_ms?.toFixed(0) ?? "0"}</span>
                <span className="text-[9px] text-[var(--text-subtle)]">p95</span>
                <span className="text-[10px] text-[var(--text-subtle)]">ms</span>
              </div>
              <div className="text-[9px] text-[var(--text-subtle)] mt-1">{perf.lm_studio?.samples ?? 0} calls</div>
            </div>
            <div>
              <div className="text-[12px] text-[var(--text-subtle)] mb-1">ChromaDB</div>
              <div className="flex items-baseline gap-3 font-mono">
                <span className="text-[16px] font-black text-[var(--text)]">{perf.chromadb?.p50_ms?.toFixed(0) ?? "0"}</span>
                <span className="text-[9px] text-[var(--text-subtle)]">p50</span>
                <span className="text-[12px] text-[var(--text-muted)]">{perf.chromadb?.p95_ms?.toFixed(0) ?? "0"}</span>
                <span className="text-[9px] text-[var(--text-subtle)]">p95</span>
                <span className="text-[10px] text-[var(--text-subtle)]">ms</span>
              </div>
              <div className="text-[9px] text-[var(--text-subtle)] mt-1">{perf.chromadb?.samples ?? 0} queries</div>
            </div>
          </div>
        </Card>
      )}
      </Section>

      <Section title="Director">
      {/* Director goal input */}
      <Card title="Submit Goal">
        <div className="flex gap-2">
          <input
            value={goalInput}
            onChange={e => setGoalInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && submitGoal()}
            placeholder="Give the agent swarm a goal…"
            className={`flex-1 bg-black/30 border ${t.accentBorder} rounded-lg px-3 py-2 text-[12px] text-[var(--text)] placeholder:text-[var(--text-subtle)] focus:outline-none ${t.inputFocus}`}
          />
          <button
            onClick={submitGoal}
            disabled={submitting || !goalInput.trim()}
            className={`px-4 py-2 rounded-lg text-[11px] font-semibold ${t.btnBg} ${t.accentHover} ${t.accent} border ${t.accentBorder} disabled:opacity-40 transition-all`}
          >
            {submitting ? "…" : "Run"}
          </button>
        </div>
      </Card>

      {/* Active agents */}
      {swarm.agents.length > 0 && (
        <Card title={`Active Agents (${swarm.agents.length})`}>
          <div className="space-y-2">
            {swarm.agents.map(ag => {
              const elapsed = ag.elapsed_s ?? 0;
              const elapsedStr = elapsed < 60
                ? `${elapsed.toFixed(0)}s`
                : `${Math.floor(elapsed / 60)}m${(elapsed % 60).toFixed(0)}s`;
              return (
                <div key={ag.id} className="py-1.5 border-b border-white/[0.02] last:border-0">
                  <div className="flex items-center gap-3">
                    <span className={`text-[9px] font-mono font-bold uppercase ${agentStatusColor[ag.status] ?? "text-[var(--text-subtle)]"}`}>{ag.status}</span>
                    <span className={`text-[10px] ${t.accent} font-semibold`}>{ag.name}</span>
                    <span className="text-[10px] text-[var(--text-subtle)] truncate flex-1">{ag.task}</span>
                    {elapsed > 0 && (
                      <span className="text-[9px] text-[var(--text-subtle)] tabular-nums shrink-0">
                        {elapsedStr}
                      </span>
                    )}
                  </div>
                  {ag.step && (
                    <div className="ml-[60px] text-[9px] text-[var(--text-subtle)] italic truncate">
                      → {ag.step}
                    </div>
                  )}
                  {(ag.tokens_used || ag.shell_calls_used) ? (
                    <div className="ml-[60px] text-[9px] text-[var(--text-subtle)] flex gap-3 mt-0.5">
                      {ag.tokens_used ? <span>{ag.tokens_used} tok</span> : null}
                      {ag.shell_calls_used ? <span>{ag.shell_calls_used} shell</span> : null}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </Card>
      )}
      </Section>

      <Section title="Internal State">
      {/* Internal state — emotions + drives merged (3.2) */}
      {(emotionState || Object.keys(drives).length > 0) && (
        <Card
          title="Emotions & Drives"
          actions={
            emotionState ? (
              <span className={`text-[10px] font-semibold ${t.accent}`}>{emotionState.dominant}</span>
            ) : undefined
          }
        >
          <div className="grid grid-cols-2 gap-5">
            <div>
              <div className="text-[12px] text-[var(--text-subtle)] mb-2">Emotions</div>
              <div className="space-y-2">
                {emotionState && Object.entries(emotionState.state).map(([dim, val]) => {
                  const pct = Math.round(val * 100);
                  const barColor = dim === "FRUSTRATION" ? "bg-red-500" : dim === "SATISFACTION" ? "bg-emerald-500" : dim === "CURIOSITY" ? "bg-amber-500" : dim === "FOCUS" ? "bg-blue-500" : "bg-slate-500";
                  return (
                    <div key={dim} className="space-y-0.5">
                      <div className="flex justify-between items-center">
                        <span className="text-[9px] text-[var(--text-subtle)] uppercase tracking-wider">{dim}</span>
                        <span className="text-[9px] font-mono text-[var(--text-subtle)]">{pct}%</span>
                      </div>
                      <div className="h-1 bg-white/[0.04] rounded-full overflow-hidden">
                        <div className={`h-full rounded-full transition-all ${barColor} opacity-60`} style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div>
              <div className="text-[12px] text-[var(--text-subtle)] mb-2">Drives</div>
              <div className="space-y-2">
                {Object.entries(drives).map(([name, val]) => (
                  <div key={name} className="space-y-0.5">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[var(--text-subtle)]">{driveIcons[name]}</span>
                        <span className="text-[9px] text-[var(--text-subtle)] uppercase tracking-wider">{name}</span>
                        {val >= 0.75 && <span className="text-[8px] text-amber-400">⚡</span>}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className={`text-[9px] font-mono ${val >= 0.75 ? "text-amber-400" : "text-[var(--text-subtle)]"}`}>{Math.round(val * 100)}%</span>
                        <button onClick={() => resetDrive(name)} className="text-[8px] text-[var(--text-subtle)] hover:text-[var(--text-muted)] px-1 rounded border border-white/[0.04] hover:border-white/10">reset</button>
                      </div>
                    </div>
                    <div className="h-1 bg-white/[0.04] rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all ${driveColors[name] ?? "bg-slate-500"} ${val >= 0.75 ? "opacity-100" : "opacity-50"}`} style={{ width: `${val * 100}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* AI Wants & Needs (3.3) — moved into Internal State */}
      {wants.length > 0 && (
        <Card
          title="JARVIS Wants"
          actions={
            wants.some(w => w.status === "unmet") ? (
              <span className="text-[9px] text-amber-400 animate-pulse">
                {wants.filter(w => w.status === "unmet").length} unmet
              </span>
            ) : undefined
          }
        >
          <div className="space-y-2">
            {wants.map(w => (
              <div key={w.id} className="flex items-start gap-2 py-1 border-b border-white/[0.02] last:border-0">
                <span className={`mt-0.5 w-1.5 h-1.5 rounded-full shrink-0 ${w.status === "satisfied" ? "bg-emerald-400" : w.status === "unmet" ? "bg-amber-400" : "bg-slate-600"}`} />
                <span className={`text-[10px] ${w.status === "satisfied" ? "text-[var(--text-subtle)] line-through" : "text-[var(--text-muted)]"}`}>{w.want}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Thought Broadcast (3.4) — moved into Internal State */}
      {thoughts.length > 0 && (
        <Card title="Thought Stream">
          <div className="space-y-1.5">
            {thoughts.slice(0, 8).map((th, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="text-[9px] text-[var(--text-subtle)] shrink-0 tabular-nums">
                  {th.ts ? new Date(th.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "--"}
                </span>
                <span className="text-[10px] text-[var(--text-muted)] italic">{th.thought}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
      </Section>

      <Section title="Catalog">
      {/* D7 — Bot personality cards */}
      {personalityCards.length > 0 && (
        <Card
          title="Bot Personalities"
          actions={
            <button onClick={fillAllCards} disabled={regenBusy !== null}
              className="text-[9px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40">
              {regenBusy === "__all__" ? "Filling…" : "Fill missing"}
            </button>
          }
        >
          <div className="space-y-2">
            {personalityCards.map(card => (
              <div key={card.id} className="p-2 rounded border border-white/[0.04] bg-[var(--surface-2)]">
                <div className="flex items-baseline gap-2 mb-1">
                  <span className={`text-[10px] font-mono ${t.accent}`}>{card.class_name || card.id}</span>
                  <span className="text-[9px] text-[var(--text-subtle)]">{card.id}</span>
                  {card.stale && <span className="text-[9px] text-amber-400">stale</span>}
                  <button onClick={() => regenerateCard(card.id)}
                    disabled={regenBusy === card.id}
                    className="ml-auto text-[9px] px-1.5 py-0.5 border border-white/[0.04] rounded text-[var(--text-subtle)] hover:text-[var(--text)] disabled:opacity-40">
                    {regenBusy === card.id ? "…" : "regen"}
                  </button>
                </div>
                <p className="text-[10px] text-[var(--text-muted)] leading-relaxed">{card.text}</p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* D7.6 — Plugin personality cards */}
      {pluginCards.length > 0 && (
        <Card
          title="Plugin Catalog"
          actions={
            <button onClick={fillAllPluginCards} disabled={pluginRegenBusy !== null}
              className="text-[9px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40">
              {pluginRegenBusy === "__all__" ? "Filling…" : "Fill missing"}
            </button>
          }
        >
          <div className="space-y-2">
            {pluginCards.map(card => (
              <div key={card.id} className="p-2 rounded border border-white/[0.04] bg-[var(--surface-2)]">
                <div className="flex items-baseline gap-2 mb-1">
                  <span className={`text-[10px] font-mono ${t.accent}`}>{card.plugin_name}</span>
                  <span className="text-[9px] text-[var(--text-subtle)]">
                    {card.tool_count} tool{card.tool_count === 1 ? "" : "s"}
                  </span>
                  {card.stale && <span className="text-[9px] text-amber-400">stale</span>}
                  <button onClick={() => regeneratePluginCard(card.slug)}
                    disabled={pluginRegenBusy === card.slug}
                    className="ml-auto text-[9px] px-1.5 py-0.5 border border-white/[0.04] rounded text-[var(--text-subtle)] hover:text-[var(--text)] disabled:opacity-40">
                    {pluginRegenBusy === card.slug ? "…" : "regen"}
                  </button>
                </div>
                <p className="text-[10px] text-[var(--text-muted)] leading-relaxed">{card.text}</p>
              </div>
            ))}
          </div>
        </Card>
      )}
      </Section>

      {/* C5.3 — Homelab status (failed services + stopped containers) */}
      {homelab && (homelab.findings.length > 0 || (homelab.journal_errors_10min?.count ?? 0) > 0) && (
      <Section title="Homelab">
        <Card
          title={`Homelab — ${homelab.summary}`}
          actions={
            <button onClick={loadHomelab} disabled={homelabBusy}
              className="text-[9px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40 flex items-center gap-1">
              <RefreshCw size={10} className={homelabBusy ? "animate-spin" : ""} />
              {homelabBusy ? "Sweeping…" : "Rescan"}
            </button>
          }
        >
          <div className="space-y-2">
            {homelab.failed_units.map(u => {
              const key = `service:${u.unit}`;
              const armed = armedRestart === key;
              const busy = restartingId === key;
              return (
                <div key={u.unit} className="flex items-baseline gap-2 py-1 border-b border-white/[0.02] last:border-0">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0" />
                  <span className="text-[10px] font-mono text-[var(--text)] flex-1 truncate" title={u.unit}>
                    {u.unit}
                  </span>
                  <span className="text-[9px] text-[var(--text-subtle)] truncate max-w-[220px]" title={u.description}>
                    {u.description}
                  </span>
                  <button
                    onClick={() => armed ? commitRestart(key, "service", u.unit) : armRestart(key)}
                    disabled={busy}
                    className={`text-[9px] px-1.5 py-0.5 rounded border transition-colors ${
                      armed ? "border-amber-500/40 bg-amber-500/10 text-amber-300 animate-pulse"
                            : "border-white/[0.06] text-[var(--text-muted)] hover:text-[var(--text)] hover:border-white/10"
                    } disabled:opacity-40`}
                    title={armed ? "Click again within 3s to commit" : "Restart this service"}
                  >
                    {busy ? "…" : armed ? "confirm" : "restart"}
                  </button>
                  <span className="text-[8px] text-red-400/70 uppercase">failed</span>
                </div>
              );
            })}
            {[...homelab.containers_down.docker.map(c => ({...c, engine: "docker" as const})),
              ...homelab.containers_down.podman.map(c => ({...c, engine: "podman" as const}))]
              .map((c, i) => {
                const key = `${c.engine}:${c.name}`;
                const armed = armedRestart === key;
                const busy = restartingId === key;
                return (
                  <div key={`${c.engine}-${c.name}-${i}`} className="flex items-baseline gap-2 py-1 border-b border-white/[0.02] last:border-0">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
                    <span className="text-[10px] font-mono text-[var(--text)] flex-1 truncate" title={c.name}>
                      {c.name || "(unnamed)"}
                    </span>
                    <span className="text-[9px] text-[var(--text-subtle)] truncate max-w-[180px]" title={c.image}>
                      {c.image}
                    </span>
                    <button
                      onClick={() => armed
                        ? commitRestart(key, "container", c.name, c.engine)
                        : armRestart(key)}
                      disabled={busy || !c.name}
                      className={`text-[9px] px-1.5 py-0.5 rounded border transition-colors ${
                        armed ? "border-amber-500/40 bg-amber-500/10 text-amber-300 animate-pulse"
                              : "border-white/[0.06] text-[var(--text-muted)] hover:text-[var(--text)] hover:border-white/10"
                      } disabled:opacity-40`}
                      title={armed ? "Click again within 3s to commit" : `Start this ${c.engine} container`}
                    >
                      {busy ? "…" : armed ? "confirm" : "start"}
                    </button>
                    <span className="text-[8px] text-amber-400/70 uppercase truncate max-w-[120px]" title={c.status}>
                      {c.status}
                    </span>
                  </div>
                );
              })}
            {(homelab.journal_errors_10min?.count ?? 0) > 25 && (
              <div className="flex items-baseline gap-2 py-1">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
                <span className="text-[10px] text-[var(--text-muted)] flex-1">
                  {homelab.journal_errors_10min.count} error-priority journal lines in last 10 min
                </span>
              </div>
            )}
          </div>
          {restartMsg && (
            <p className={`text-[9px] mt-2 ${restartMsg.startsWith("✓") ? "text-emerald-400" : "text-red-400"}`}>
              {restartMsg}
            </p>
          )}
          <p className="text-[9px] text-[var(--text-subtle)] mt-2">
            Two-click confirm: first click arms, second click within 3s commits.
          </p>
        </Card>
      </Section>
      )}

      <Section title="Autonomy">
      {/* Autonomy Control (1.6) */}
      {autonomyStatus && (
        <Card
          title="Autonomy Mode"
          actions={
            <span className={`text-[9px] font-mono ${autonomyStatus.level > 0 ? "text-amber-400" : "text-[var(--text-subtle)]"}`}>{autonomyStatus.level_name}</span>
          }
        >
          <div className="flex gap-1 mb-3">
            {[0, 1, 2, 3].map(lvl => (
              <button
                key={lvl}
                onClick={() => {
                  fetch(`${BACKEND}/autonomy/level`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ level: lvl }) })
                    .then(r => r.json()).then(d => setAutonomyStatus(d));
                }}
                className={`flex-1 py-1.5 text-[9px] font-bold rounded border transition-all ${autonomyStatus.level === lvl ? `${t.accentBg} ${t.accent} ${t.accentBorder}` : "border-white/[0.04] text-[var(--text-subtle)] hover:text-[var(--text-muted)]"}`}
              >
                L{lvl}
              </button>
            ))}
          </div>
          <div className="text-[9px] text-[var(--text-subtle)] space-y-0.5">
            <div>Cycles: {autonomyStatus.cycles_run} · Actions: {autonomyStatus.actions_taken}</div>
            <div className="text-[var(--text-faint)]">["Off","Maintenance","Proactive","Full Auto"][level]</div>
          </div>
        </Card>
      )}

      {/* D5 — Standing Goals with decay */}
      {standingGoals.length > 0 && (
        <Card
          title="Standing Goals"
          actions={
            standingGoals.some(g => g.is_stale) ? (
              <span className="text-[9px] text-amber-400 animate-pulse">
                {standingGoals.filter(g => g.is_stale).length} stale
              </span>
            ) : undefined
          }
        >
          <div className="space-y-2">
            {standingGoals.map(g => {
              const ageLabel = g.age_days < 1
                ? `${Math.round(g.age_days * 24)}h`
                : `${Math.round(g.age_days)}d`;
              return (
                <div key={g.goal}
                  className={`p-2 rounded border ${g.is_stale ? "border-amber-500/30 bg-amber-500/[0.04]" : "border-white/[0.04]"}`}>
                  <div className="flex items-start gap-2 mb-1">
                    <span className={`mt-0.5 w-1.5 h-1.5 rounded-full shrink-0 ${g.is_stale ? "bg-amber-400" : "bg-emerald-400"}`} />
                    <span className="text-[10px] text-[var(--text)] flex-1">{g.goal}</span>
                  </div>
                  <div className="flex items-center gap-2 text-[9px] text-[var(--text-subtle)] ml-3.5">
                    <span className={g.is_stale ? "text-amber-400" : ""}>{ageLabel} since reinforced</span>
                    {g.is_stale && <span className="text-amber-400">· stale</span>}
                    <button onClick={() => reinforceGoal(g.goal)}
                      className="ml-auto text-[9px] px-1.5 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-emerald-400 hover:border-emerald-500/30">
                      keep
                    </button>
                    <button onClick={() => dropGoal(g.goal)}
                      className="text-[9px] px-1.5 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-red-400 hover:border-red-500/30">
                      drop
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}
      </Section>

      <Section title="Activity">
      {/* D6 — Task Replay */}
      <ReplayPane />

      {/* Live event feed */}
      <Card
        title="Live Event Feed"
        actions={
          wsConnected ? <span className="w-1 h-1 rounded-full bg-emerald-400 animate-pulse" /> : undefined
        }
      >
        {events.length === 0 ? (
          <p className="text-[11px] text-[var(--text-subtle)]">Waiting for events{wsConnected ? "…" : " (connecting…)"}</p>
        ) : (
          <div className="space-y-0.5 max-h-72 overflow-y-auto font-mono">
            <AnimatePresence initial={false}>
              {events.slice(0, 60).map((ev, i) => (
                <motion.div
                  key={(ev.id as string | number | undefined) ?? `${ev.ts}-${i}`}
                  initial={{ opacity: 0, x: 12 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -8 }}
                  transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
                  className="flex items-start gap-2 py-1 border-b border-white/[0.02] last:border-0"
                >
                  <span className="text-[9px] text-[var(--text-subtle)] shrink-0 tabular-nums">
                    {ev.ts ? new Date(ev.ts * 1000).toLocaleTimeString() : "--:--:--"}
                  </span>
                  <span className={`text-[9px] shrink-0 ${topicColor(ev.topic)}`}>{ev.topic}</span>
                  <span className="text-[9px] text-[var(--text-subtle)] truncate">
                    {ev.sender && <span className="text-[var(--text-subtle)]">[{ev.sender}] </span>}
                    {(ev.message as string) || (ev.task_desc as string) || (ev.goal as string) || (ev.result_preview as string) || (ev.error as string) || ""}
                  </span>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </Card>
      </Section>
     </div>
    </div>
  );
}
