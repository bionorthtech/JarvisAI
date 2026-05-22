/**
 * B2 — Theater mode.
 *
 * Watch JARVIS think (Part D2). Subscribes to /ws/live and refetches
 * `/theater/recent` whenever a narrator-relevant topic fires, so new
 * lines appear within a tick of the underlying event. Falls back to a
 * 10s safety poll in case the WS drops. Renders each bus event as a
 * character-voiced bubble; newest pulses briefly; chat-style auto-
 * scroll keeps the latest line in view.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Eye, RefreshCw } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";

import { BACKEND, WS_BACKEND } from "../../config";
import { useTheme } from "../../hooks/useTheme";
import { useLiveWS } from "../../hooks/useLiveWS";
import PaneHeader from "../../components/PaneHeader";

// Topics the narrator turns into theater lines — listed here so we can
// refetch only when one of these fires, instead of every bus tick.
const NARRATOR_TOPIC_PREFIXES = [
  "agent.", "autonomy.", "system.metrics", "thought.",
  "lm.progress", "health.score", "curiosity.",
  "goal.", "homelab.",
];
function isNarratorTopic(topic: string): boolean {
  return NARRATOR_TOPIC_PREFIXES.some(p => topic.startsWith(p));
}

interface Narrative {
  id: string;
  ts: number;
  actor: string;
  voice: string;
  color: string;        // amber|red|violet|green|lime|cyan|orange|blue|rose|slate|gray
  tone: string;
  saying: string;
  kind: string;         // thought|report|finding|action|handoff|status|telemetry|event
  raw_topic: string;
  raw_sender: string;
}

interface Persona { name: string; voice: string; color: string; tone: string }

const ACTOR_COLOR_CLS: Record<string, { dot: string; chip: string; bubble: string; name: string }> = {
  amber:  { dot: "bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]",  chip: "bg-amber-500/10 border-amber-500/25 text-amber-300",   bubble: "border-amber-500/15 bg-amber-500/[0.04]",   name: "text-amber-300" },
  red:    { dot: "bg-red-400 shadow-[0_0_8px_rgba(248,113,113,0.6)]",   chip: "bg-red-500/10 border-red-500/25 text-red-300",         bubble: "border-red-500/15 bg-red-500/[0.04]",       name: "text-red-300"   },
  violet: { dot: "bg-violet-400 shadow-[0_0_8px_rgba(167,139,250,0.6)]",chip: "bg-violet-500/10 border-violet-500/25 text-violet-300",bubble: "border-violet-500/15 bg-violet-500/[0.04]", name: "text-violet-300" },
  green:  { dot: "bg-green-400 shadow-[0_0_8px_rgba(74,222,128,0.6)]",  chip: "bg-green-500/10 border-green-500/25 text-green-300",   bubble: "border-green-500/15 bg-green-500/[0.04]",   name: "text-green-300" },
  lime:   { dot: "bg-lime-400 shadow-[0_0_8px_rgba(163,230,53,0.6)]",   chip: "bg-lime-500/10 border-lime-500/25 text-lime-300",      bubble: "border-lime-500/15 bg-lime-500/[0.04]",     name: "text-lime-300"  },
  cyan:   { dot: "bg-cyan-400 shadow-[0_0_8px_rgba(34,211,238,0.6)]",   chip: "bg-cyan-500/10 border-cyan-500/25 text-cyan-300",      bubble: "border-cyan-500/15 bg-cyan-500/[0.04]",     name: "text-cyan-300"  },
  orange: { dot: "bg-orange-400 shadow-[0_0_8px_rgba(251,146,60,0.6)]", chip: "bg-orange-500/10 border-orange-500/25 text-orange-300",bubble: "border-orange-500/15 bg-orange-500/[0.04]", name: "text-orange-300" },
  blue:   { dot: "bg-blue-400 shadow-[0_0_8px_rgba(96,165,250,0.6)]",   chip: "bg-blue-500/10 border-blue-500/25 text-blue-300",      bubble: "border-blue-500/15 bg-blue-500/[0.04]",     name: "text-blue-300"  },
  rose:   { dot: "bg-rose-400 shadow-[0_0_8px_rgba(251,113,133,0.6)]",  chip: "bg-rose-500/10 border-rose-500/25 text-rose-300",      bubble: "border-rose-500/15 bg-rose-500/[0.04]",     name: "text-rose-300"  },
  slate:  { dot: "bg-slate-500",                                       chip: "bg-slate-500/10 border-slate-500/25 text-[var(--text-muted)]",   bubble: "border-slate-500/15 bg-slate-500/[0.04]",   name: "text-[var(--text-muted)]" },
  gray:   { dot: "bg-slate-600",                                       chip: "bg-slate-600/10 border-slate-600/25 text-[var(--text-muted)]",   bubble: "border-slate-600/15 bg-slate-500/[0.03]",   name: "text-[var(--text-muted)]" },
};
const DEFAULT_COLOR = ACTOR_COLOR_CLS.slate;

const KINDS = ["", "thought", "report", "finding", "action", "handoff", "status"];

function fmtTs(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export default function TheaterPane() {
  const { t } = useTheme();
  const [items, setItems] = useState<Narrative[]>([]);
  const [personas, setPersonas] = useState<Record<string, Persona>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filterKind, setFilterKind] = useState<string>("");
  const [highlight, setHighlight] = useState<string | null>(null);
  const lastSeenIdRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    fetch(`${BACKEND}/theater/recent?limit=80`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d: { narratives: Narrative[]; personas: Record<string, Persona> }) => {
        const fresh = (d.narratives || []).slice().reverse();
        const newestId = fresh.length ? fresh[fresh.length - 1].id : null;
        if (newestId && newestId !== lastSeenIdRef.current) {
          setHighlight(newestId);
          setTimeout(() => setHighlight(null), 1500);
          lastSeenIdRef.current = newestId;
        }
        setItems(fresh);
        setPersonas(d.personas || {});
        setError(null);
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  // Live WS subscription — refetch whenever a narrator-relevant topic
  // fires. Keeps a 10s safety poll in case the WS drops.
  const { events, connected: wsConnected } = useLiveWS(`${WS_BACKEND}/ws/live`);
  const lastTriggerTopicRef = useRef<string | null>(null);
  useEffect(() => {
    if (!events.length) return;
    const latest = events[0];
    if (!isNarratorTopic(latest.topic)) return;
    // Throttle: don't refetch more than once per 600ms even if the bus
    // bursts (e.g. agent lifecycle fires 4 events in <100ms).
    const key = `${latest.topic}:${latest.id ?? latest.ts ?? Date.now()}`;
    if (lastTriggerTopicRef.current === key) return;
    lastTriggerTopicRef.current = key;
    load();
  }, [events, load]);

  useEffect(() => {
    load();
    const iv = setInterval(load, 10_000);
    return () => clearInterval(iv);
  }, [load]);

  useEffect(() => {
    if (autoScroll) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items, autoScroll]);

  const visible = filterKind ? items.filter(n => n.kind === filterKind) : items;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="shrink-0 p-4 border-b border-white/[0.04] space-y-3">
        <PaneHeader icon={<Eye size={13} />} title="Theater — watch JARVIS think" />
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex gap-1">
            {KINDS.map(k => (
              <button
                key={k || "ALL"}
                onClick={() => setFilterKind(k)}
                className={`px-2 py-1 text-[9px] rounded border transition-all ${
                  filterKind === k
                    ? `${t.navActive} ${t.navActiveText} ${t.navActiveBorder}`
                    : "border-white/[0.04] text-[var(--text-subtle)] hover:text-[var(--text-muted)]"
                }`}
              >
                {k || "ALL"}
              </button>
            ))}
          </div>
          <button onClick={load} className={`ml-auto ${t.accentDim} hover:${t.accent} transition-colors`} title="Refresh">
            <RefreshCw size={12} />
          </button>
          <button
            onClick={() => setAutoScroll(v => !v)}
            className={`text-[9px] px-2 py-1 rounded border border-white/[0.04] ${autoScroll ? t.accent : "text-[var(--text-subtle)]"}`}
          >
            auto-scroll
          </button>
        </div>
        {Object.keys(personas).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(personas).map(([sender, p]) => {
              const cls = ACTOR_COLOR_CLS[p.color] || DEFAULT_COLOR;
              return (
                <span
                  key={sender}
                  title={`${sender} — ${p.tone}`}
                  className={`text-[9px] px-2 py-0.5 rounded border ${cls.chip}`}
                >
                  <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle ${cls.dot}`} />
                  {p.name}
                </span>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2 bg-[var(--surface-2)]">
        {loading && items.length === 0 && (
          <div className={`${t.accentDim} text-xs animate-pulse`}>Loading the cast…</div>
        )}
        {error && (
          <div className="text-xs text-red-400 border border-red-500/20 bg-red-500/5 rounded px-3 py-2">
            Theater offline: {error}
          </div>
        )}
        {!loading && !error && visible.length === 0 && (
          <div className="text-xs text-[var(--text-subtle)] italic">
            Stage is quiet. Keep using JARVIS or trigger a bot run from the
            Bots tab — events will flow in here as they happen.
          </div>
        )}
        <AnimatePresence initial={false}>
        {visible.map(n => {
          const cls = ACTOR_COLOR_CLS[n.color] || DEFAULT_COLOR;
          const isPulsing = highlight === n.id;
          return (
            <motion.div
              key={n.id}
              initial={{ opacity: 0, x: 16 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
              className={`flex gap-3 items-start ${isPulsing ? "animate-pulse" : ""}`}
            >
              <div className="shrink-0 w-20 pt-1.5 text-right">
                <div className={`text-[10px] font-semibold ${cls.name} tracking-wider`}>{n.actor}</div>
                <div className="text-[8px] text-[var(--text-subtle)] tabular-nums">{fmtTs(n.ts)}</div>
              </div>
              <div className="shrink-0 pt-2.5">
                <span className={`block w-1.5 h-1.5 rounded-full ${cls.dot}`} />
              </div>
              <div className={`flex-1 min-w-0 px-3 py-1.5 rounded border ${cls.bubble}`}>
                <div className={`text-[11px] leading-relaxed text-[var(--text)] ${n.kind === "thought" ? "italic" : ""}`}>
                  {n.saying}
                </div>
                <div className="text-[8px] text-[var(--text-subtle)] mt-0.5 font-mono">
                  <span className="opacity-60">{n.raw_topic}</span>
                  <span className="mx-1">·</span>
                  <span className="opacity-50">{n.kind}</span>
                </div>
              </div>
            </motion.div>
          );
        })}
        </AnimatePresence>
        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 px-4 py-2 border-t border-white/[0.04] flex items-center justify-between text-[9px] text-[var(--text-subtle)]">
        <span>{visible.length} of {items.length} narratives · {wsConnected ? "live (WS + 10s)" : "polling (WS down)"}</span>
        <span className="text-[var(--text-subtle)]">
          D2 — narrator in <code className="text-[var(--text-muted)]">agent/core/narrator.py</code>
        </span>
      </div>
    </div>
  );
}
