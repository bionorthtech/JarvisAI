/**
 * 3B — Left navigation sidebar with grouped, collapsible sections.
 *
 * Twelve modes were a flat scroll list; with the reskin they live
 * under three semantic groups so the chrome scales:
 *   Work       — chat, terminal, coder
 *   Knowledge  — brain, logs, theater
 *   Control    — dashboard, analytics, bots, apps, settings
 *
 * `Home` stays as a top-level entry. Group-expanded state persists
 * in localStorage so the user's preferred shape survives reload.
 *
 * Bottom strip kept identical — LM status, ping, model, Clear/Sync.
 */
import { useState, type ReactNode } from "react";
import {
  Home, MessageSquare, Terminal, Code2, LayoutDashboard, BarChart3,
  ScrollText, AppWindow, Brain, Eye, Settings, Bot,
  Activity, Zap, Cpu, Trash2, RefreshCw,
  ChevronDown, ChevronRight,
} from "lucide-react";

import type { AppMode, LMStatus } from "../types";
import { useTheme } from "../hooks/useTheme";
import { StatRow, ActionBtn, NavBtn } from "./widgets";

type NavItem = { mode: AppMode; icon: ReactNode; label: string };
type NavGroup = { id: string; label: string; items: NavItem[] };

const GROUPS: NavGroup[] = [
  {
    id: "work",
    label: "Work",
    items: [
      { mode: "chat",     icon: <MessageSquare size={14} />, label: "Chat" },
      { mode: "terminal", icon: <Terminal size={14} />,      label: "Terminal" },
      { mode: "coder",    icon: <Code2 size={14} />,         label: "Coder" },
    ],
  },
  {
    id: "knowledge",
    label: "Knowledge",
    items: [
      { mode: "brain",   icon: <Brain size={14} />,      label: "Brain" },
      { mode: "logs",    icon: <ScrollText size={14} />, label: "Logs" },
      { mode: "theater", icon: <Eye size={14} />,        label: "Theater" },
    ],
  },
  {
    id: "control",
    label: "Control",
    items: [
      { mode: "dashboard", icon: <LayoutDashboard size={14} />, label: "Dashboard" },
      { mode: "analytics", icon: <BarChart3 size={14} />,       label: "Analytics" },
      { mode: "bots",      icon: <Bot size={14} />,             label: "Bots" },
      { mode: "apps",      icon: <AppWindow size={14} />,       label: "Apps" },
      { mode: "settings",  icon: <Settings size={14} />,        label: "Settings" },
    ],
  },
];

const _DEFAULT_OPEN: Record<string, boolean> = {
  work: true, knowledge: true, control: true,
};

function _loadOpen(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem("jarvis-sidebar-open");
    if (!raw) return { ..._DEFAULT_OPEN };
    return { ..._DEFAULT_OPEN, ...JSON.parse(raw) };
  } catch {
    return { ..._DEFAULT_OPEN };
  }
}


export default function Sidebar({ mode, setMode, lm, model, onClear, onRefresh }: {
  mode: AppMode; setMode: (m: AppMode) => void; lm: LMStatus; model: string;
  onClear: () => void; onRefresh: () => void;
}) {
  const { t } = useTheme();
  const [open, setOpen] = useState<Record<string, boolean>>(_loadOpen);

  const toggle = (id: string) => {
    setOpen(prev => {
      const next = { ...prev, [id]: !prev[id] };
      try { localStorage.setItem("jarvis-sidebar-open", JSON.stringify(next)); } catch { /* persist failure is non-fatal */ }
      return next;
    });
  };

  return (
    <aside className="w-52 shrink-0 bg-[var(--surface-1)] flex flex-col">
      <div className="px-5 pt-6 pb-5">
        <div className="flex items-center gap-2 mb-1">
          <span className={`w-2 h-2 rounded-full shrink-0 ${lm.connected ? `${t.statusDot} pulse-glow` : "bg-[var(--text-faint)]"}`} />
          <span className="text-[var(--text)] font-semibold text-base">Jarvis</span>
        </div>
        <p className="text-[11px] text-[var(--text-subtle)] ml-4">Local AI</p>
      </div>

      <nav className="px-3 py-2 space-y-3 flex-1 overflow-y-auto">
        {/* Home stays top-level — it's the welcome surface. */}
        <NavBtn
          icon={<Home size={14} />} label="Home"
          active={mode === "welcome"} onClick={() => setMode("welcome")}
        />

        {GROUPS.map(g => {
          const isOpen = open[g.id];
          const activeInGroup = g.items.some(i => i.mode === mode);
          return (
            <div key={g.id} className="space-y-1">
              <button
                onClick={() => toggle(g.id)}
                className="w-full flex items-center gap-1.5 px-2 py-1 text-[var(--text-subtle)] hover:text-[var(--text-muted)] transition-colors"
              >
                {isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                <span className="text-[11px] font-medium">{g.label}</span>
                {!isOpen && activeInGroup && (
                  <span className={`ml-auto w-1.5 h-1.5 rounded-full ${t.statusDot}`} />
                )}
              </button>
              {isOpen && (
                <div className="space-y-1 pl-1">
                  {g.items.map(n => (
                    <NavBtn
                      key={n.mode}
                      icon={n.icon} label={n.label}
                      active={mode === n.mode}
                      onClick={() => setMode(n.mode)}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <div className="px-4 pb-5 pt-4 space-y-2">
        <StatRow icon={<Activity size={12} />} label="LM" val={lm.connected ? "Online" : "Offline"} ok={lm.connected} />
        {lm.connected && <StatRow icon={<Zap size={12} />} label="Ping" val={`${Math.round(lm.latency_ms)}ms`} ok />}
        <StatRow icon={<Cpu size={12} />} label="Model" val={model ? model.slice(0, 12) : "—"} ok={!!model} />
        <div className="flex gap-1.5 pt-2">
          <ActionBtn icon={<Trash2 size={11} />} label="Clear" onClick={onClear} danger />
          <ActionBtn icon={<RefreshCw size={11} />} label="Sync" onClick={onRefresh} />
        </div>
      </div>
    </aside>
  );
}
