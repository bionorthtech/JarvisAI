/**
 * Bots mode — the live state of every scheduled bot.
 *
 * Lists each bot in `_BOT_SCHEDULE` with:
 *   - schedule interval
 *   - last run timestamp + age
 *   - last status (ok / failed / never_run / unresolved)
 *   - autonomy-eligibility (does the current daemon level meet the
 *     bot's min_autonomy_level? if not, autonomous runs are gated and
 *     the user has to fire them manually)
 *   - "Run now" button → POST /bots/{id}/run
 *   - collapsible "last result" preview from /bots/reports
 */
import { useCallback, useEffect, useState } from "react";
import { Bot, RefreshCw, Play, ChevronDown, ChevronRight, Activity } from "lucide-react";

import { BACKEND } from "../../config";
import { useTheme } from "../../hooks/useTheme";
import { usePolling } from "../../hooks/usePolling";
import PaneHeader from "../../components/PaneHeader";
import { Section } from "../../components/widgets";

interface BotStatus {
  id: string;
  interval_s: number;
  last_run_ts: number | null;
  last_run_age_s: number | null;
  next_due_ts: number;
  due_in_s: number;
  last_status: string;
  last_error: string | null;
  min_autonomy_level: number | null;
  autonomy_eligible: boolean;
  wake_conditions: string[];
  endpoint: string;
}

interface BotsResponse {
  autonomy_level: number;
  now_ts: number;
  bots: BotStatus[];
}

const PRETTY_NAME: Record<string, string> = {
  memory_gardener:      "Memory Gardener",
  code_health:          "Code Health Monitor",
  performance_watchdog: "Performance Watchdog",
  knowledge_curator:    "Knowledge Curator",
  homelab_warden:       "Homelab Warden",
};

const PRETTY_DESC: Record<string, string> = {
  memory_gardener:      "Nightly ChromaDB hygiene — prune stale memories.",
  code_health:          "Weekly TODO debt + large-file audit.",
  performance_watchdog: "Every 6h: LM Studio + ChromaDB latency sampling.",
  knowledge_curator:    "Daily research-gap mining from agent miss-log.",
  homelab_warden:       "Every 5 min: failed services + stopped containers.",
};

function fmtInterval(s: number): string {
  if (s < 60)        return `${s}s`;
  if (s < 3600)      return `${Math.round(s / 60)}m`;
  if (s < 86400)    return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function fmtAge(s: number | null): string {
  if (s === null) return "never";
  if (s < 60)        return `${s}s ago`;
  if (s < 3600)      return `${Math.round(s / 60)}m ago`;
  if (s < 86400)    return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function statusTone(s: string): string {
  if (s === "ok")         return "text-emerald-400";
  if (s === "failed")     return "text-red-400";
  if (s === "unresolved") return "text-amber-400";
  return "text-[var(--text-subtle)]";
}

export default function BotsPane() {
  const { t } = useTheme();
  const [data, setData] = useState<BotsResponse | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [reports, setReports] = useState<Record<string, any>>({});

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${BACKEND}/bots/status`);
      setData(await r.json());
      setLastRefreshed(new Date());
    } catch { /* keep last */ }
  }, []);

  const loadReports = useCallback(async () => {
    try {
      const r = await fetch(`${BACKEND}/bots/reports?limit=20`);
      const d = await r.json();
      const by: Record<string, any> = {};
      for (const rep of d.reports ?? []) {
        // Match report file name to bot id by prefix
        for (const id of Object.keys(PRETTY_NAME)) {
          if (rep.name?.startsWith(id) && !by[id]) by[id] = rep;
        }
      }
      setReports(by);
    } catch { /* keep last */ }
  }, []);

  useEffect(() => { load(); loadReports(); }, [load, loadReports]);
  usePolling(load, 10_000);

  const runBot = async (botId: string) => {
    setRunning(botId);
    setRunResult(p => ({ ...p, [botId]: "running…" }));
    try {
      const ep = `/bots/${botId.replace(/_/g, "-")}/run`;
      const r = await fetch(`${BACKEND}${ep}`, { method: "POST" });
      const d = await r.json();
      // Render the most-useful field — varies per bot, so try common ones
      const summary =
        d?.summary ||
        d?.message ||
        (typeof d?.score === "number" ? `score: ${d.score}` : null) ||
        (typeof d?.findings_total === "number"
          ? `${d.findings_total} findings`
          : null) ||
        JSON.stringify(d).slice(0, 120);
      setRunResult(p => ({ ...p, [botId]: `✓ ${summary}` }));
      // Reload state + reports
      load();
      loadReports();
    } catch (e) {
      setRunResult(p => ({ ...p, [botId]: `✗ ${e}` }));
    } finally {
      setRunning(null);
    }
  };

  const toggle = (botId: string) => {
    setExpanded(p => {
      const n = new Set(p);
      n.has(botId) ? n.delete(botId) : n.add(botId);
      return n;
    });
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-6 space-y-6">
        <PaneHeader icon={<Bot size={13} />} title="Bots" lastRefreshed={lastRefreshed}>
          <span className="text-[9px] text-[var(--text-subtle)]">
            autonomy level: <span className={data && data.autonomy_level > 0 ? "text-emerald-400" : "text-[var(--text-subtle)]"}>
              {data?.autonomy_level ?? "?"}
            </span>
          </span>
          <button onClick={load}
            className="text-[10px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)]">
            <RefreshCw size={11} />
          </button>
        </PaneHeader>

        {!data && (
          <p className="text-[11px] text-[var(--text-subtle)]">Loading…</p>
        )}

        {data && (
          <Section title={`Scheduled (${data.bots.length})`}>
            <div className="space-y-3">
              {data.bots.map(b => {
        const open = expanded.has(b.id);
        const rep = reports[b.id];
        return (
          <div key={b.id} className="bg-[var(--surface-1)] border border-white/[0.04] rounded-xl">
            <div className="px-5 py-3 flex items-center gap-3">
              <button onClick={() => toggle(b.id)}
                className="shrink-0 text-[var(--text-subtle)] hover:text-[var(--text)]">
                {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              </button>
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2">
                  <span className={`text-[12px] font-bold ${t.accent}`}>{PRETTY_NAME[b.id] ?? b.id}</span>
                  <span className={`text-[9px] uppercase tracking-widest ${statusTone(b.last_status)}`}>
                    {b.last_status.replace("_", " ")}
                  </span>
                  {!b.autonomy_eligible && (
                    <span className="text-[8px] uppercase tracking-widest text-amber-400/70"
                          title={`Needs autonomy ≥ ${b.min_autonomy_level}; current is ${data.autonomy_level}`}>
                      gated · needs L{b.min_autonomy_level}
                    </span>
                  )}
                </div>
                <p className="text-[10px] text-[var(--text-subtle)] mt-0.5">
                  {PRETTY_DESC[b.id] ?? ""}
                </p>
              </div>
              <div className="shrink-0 text-right text-[9px] text-[var(--text-subtle)]">
                <div>every {fmtInterval(b.interval_s)}</div>
                <div>last run: {fmtAge(b.last_run_age_s)}</div>
                {b.last_run_ts !== null && (
                  <div>next: in {fmtInterval(b.due_in_s)}</div>
                )}
              </div>
              <button
                onClick={() => runBot(b.id)}
                disabled={running === b.id}
                className={`shrink-0 text-[10px] px-2.5 py-1 rounded border ${t.accentBorder} ${t.accent} ${t.accentBg} disabled:opacity-40 flex items-center gap-1`}
                title={`POST ${b.endpoint}`}
              >
                <Play size={9} /> {running === b.id ? "Running…" : "Run now"}
              </button>
            </div>

            {runResult[b.id] && (
              <div className={`px-5 pb-2 text-[10px] ${runResult[b.id].startsWith("✓") ? "text-emerald-400" : "text-red-400"}`}>
                {runResult[b.id]}
              </div>
            )}

            {open && (
              <div className="px-5 pb-4 border-t border-white/[0.04] pt-3 space-y-2">
                {b.last_error && (
                  <div className="bg-red-950/40 border border-red-800/40 rounded p-2.5">
                    <div className="text-[9px] uppercase tracking-widest text-red-300/80 mb-1">
                      Last error
                    </div>
                    <code className="block text-[10px] text-red-200 font-mono whitespace-pre-wrap break-words">
                      {b.last_error}
                    </code>
                  </div>
                )}
                {b.wake_conditions.length > 0 && (
                  <div className="text-[9px] text-[var(--text-subtle)]">
                    <span className="uppercase tracking-widest mr-2">Wake on:</span>
                    {b.wake_conditions.map(w => (
                      <code key={w} className="font-mono mr-2 text-[var(--text-muted)]">{w}</code>
                    ))}
                  </div>
                )}
                {rep ? (
                  <div className="bg-[var(--surface-2)] border border-white/[0.04] rounded p-3">
                    <div className="text-[9px] text-[var(--text-subtle)] mb-1 flex items-baseline gap-2">
                      <Activity size={10} />
                      <span className="font-mono">{rep.name}</span>
                    </div>
                    <pre className="text-[10px] text-[var(--text-muted)] whitespace-pre-wrap break-all leading-relaxed max-h-60 overflow-y-auto">
                      {JSON.stringify(rep, null, 2).slice(0, 1500)}
                    </pre>
                  </div>
                ) : (
                  <p className="text-[10px] text-[var(--text-subtle)]">No recent report on disk for this bot.</p>
                )}
              </div>
            )}
          </div>
        );
      })}
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}
