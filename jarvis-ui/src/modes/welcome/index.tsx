/**
 * 3B — Welcome / landing surface, rebuilt against the new primitives.
 *
 * CleanMeter-inspired layout: big readout tiles for live status
 * (LM, Memory, Backend), a single quick-ask input, and four
 * launch tiles for the most-used modes. The full nav grid was
 * removed — the grouped sidebar already exposes every mode, so
 * duplicating the grid here added clutter without adding paths.
 *
 * Recent sessions land in their own Section underneath when present.
 */
import { useEffect, useState } from "react";
import {
  MessageSquare, Code2, LayoutDashboard,
  Brain, Send, Bot, Database, Server,
} from "lucide-react";

import type { AppMode, LMStatus, MemoryStats } from "../../types";
import { BACKEND } from "../../config";
import { useTheme } from "../../hooks/useTheme";
import { fmtTime, fmtDate } from "../../utils/format";
import { MetricTile, Section } from "../../components/widgets";

interface SessionRow {
  session_id: string;
  last_message: string;
  last_active: number;
}

interface Props {
  lm:         LMStatus;
  memStats:   MemoryStats;
  onNav:      (m: AppMode) => void;
  onQuickAsk: (text: string) => void;
}

const LAUNCH: { mode: AppMode; label: string; desc: string; icon: typeof MessageSquare }[] = [
  { mode: "chat",      label: "Chat",      desc: "Talk to JARVIS",     icon: MessageSquare },
  { mode: "coder",     label: "Coder",     desc: "Browse & edit code", icon: Code2 },
  { mode: "brain",     label: "Brain",     desc: "Second-brain vault", icon: Brain },
  { mode: "dashboard", label: "Dashboard", desc: "Live agent monitor", icon: LayoutDashboard },
];

export default function WelcomeScreen({ lm, memStats, onNav, onQuickAsk }: Props) {
  const { t } = useTheme();
  const [now, setNow] = useState(new Date());
  const [quickVal, setQuickVal] = useState("");
  const [sessions, setSessions] = useState<SessionRow[]>([]);

  useEffect(() => {
    const iv = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(iv);
  }, []);

  useEffect(() => {
    fetch(`${BACKEND}/sessions`)
      .then(r => r.json())
      .then(d => setSessions(d.sessions ?? []))
      .catch(() => {});
  }, []);

  const quickSend = () => {
    const v = quickVal.trim();
    if (!v) return;
    onQuickAsk(v);
    setQuickVal("");
  };

  return (
    <div className="flex-1 overflow-auto bg-[var(--bg)]">
      <div className="max-w-5xl mx-auto py-12 px-6 space-y-8">

        {/* Header */}
        <header>
          <h1 className="text-4xl font-semibold text-[var(--text)] mb-1">JARVIS</h1>
          <p className="text-[13px] text-[var(--text-subtle)]">Local AI agent · fully offline</p>
          <p className={`text-3xl font-semibold ${t.accent} mt-6 tabular-nums`}>{fmtTime(now)}</p>
          <p className="text-[12px] text-[var(--text-subtle)] mt-1">{fmtDate(now)}</p>
        </header>

        {/* Live status — MetricTile grid */}
        <Section title="Status">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <MetricTile
              label="LM Studio"
              value={lm.connected ? `${Math.round(lm.latency_ms)}ms` : "Offline"}
              sublabel={lm.connected
                ? (lm.models[0] || "model loaded")
                : "Server not reachable"}
              status={lm.connected ? "ok" : "bad"}
              icon={<Bot size={12} />}
            />
            <MetricTile
              label="Memory"
              value={memStats.file_chunks.toString()}
              sublabel={`${memStats.chat_turns} chat turns · ${memStats.project}`}
              status={memStats.file_chunks > 0 ? "ok" : "muted"}
              icon={<Database size={12} />}
            />
            <MetricTile
              label="Backend"
              value="8000"
              sublabel="FastAPI · 127.0.0.1"
              status="ok"
              icon={<Server size={12} />}
            />
          </div>
        </Section>

        {/* Quick-ask */}
        <div className="flex items-center gap-3 bg-[var(--surface-1)] rounded-2xl px-5 py-2">
          <input
            value={quickVal}
            onChange={e => setQuickVal(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") quickSend(); }}
            placeholder="Quick-ask JARVIS anything…"
            className="flex-1 bg-transparent py-3 text-[15px] text-[var(--text)] placeholder:text-[var(--text-subtle)] outline-none"
            autoFocus
          />
          <button
            onClick={quickSend}
            disabled={!quickVal.trim()}
            className={`shrink-0 p-2.5 rounded-xl ${t.btnBg} ${t.btnHoverBg} ${t.accent} disabled:opacity-20 transition-colors`}
          >
            <Send size={16} />
          </button>
        </div>

        {/* Launch tiles — four most-used modes. Full nav lives in the sidebar. */}
        <Section title="Open">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {LAUNCH.map(btn => {
              const Icon = btn.icon;
              return (
                <button
                  key={btn.mode}
                  onClick={() => onNav(btn.mode)}
                  className="flex flex-col items-center gap-3 p-6 rounded-2xl bg-[var(--surface-1)] hover:bg-white/[0.04] transition-colors group apple-card-shadow"
                >
                  <span className={`text-[var(--text-muted)] group-hover:${t.accent} transition-colors`}><Icon size={24} /></span>
                  <span className="text-[14px] text-[var(--text)] font-medium">{btn.label}</span>
                  <span className="text-[12px] text-[var(--text-subtle)]">{btn.desc}</span>
                </button>
              );
            })}
          </div>
        </Section>

        {/* Recent sessions */}
        {sessions.length > 0 && (
          <Section title="Recent">
            <div className="space-y-2">
              {sessions.slice(0, 5).map(s => (
                <button
                  key={s.session_id}
                  onClick={() => onNav("chat")}
                  className="w-full flex items-center gap-3 px-4 py-3 rounded-xl bg-[var(--surface-1)] hover:bg-white/[0.04] text-left transition-colors"
                >
                  <MessageSquare size={12} className="text-[var(--text-subtle)] shrink-0" />
                  <span className="text-[13px] text-[var(--text-muted)] truncate flex-1">{s.last_message || "No messages"}</span>
                  <span className="text-[11px] text-[var(--text-subtle)] shrink-0">{new Date(s.last_active * 1000).toLocaleDateString()}</span>
                </button>
              ))}
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}
