/**
 * B2 — Analytics mode.
 *
 * Periodic snapshot from `/analytics`: LM Studio reachability + latency,
 * GPU stats (VRAM, util, temp), system metrics (RAM, CPU), token usage.
 * Renders a tiny latency sparkline so trends are visible at a glance.
 *
 * Polls every 10s.
 */
import { useCallback, useEffect, useState } from "react";
import { BarChart3, Bot, Activity, Zap, Cpu, HardDrive, Layers, RefreshCw } from "lucide-react";

import { BACKEND } from "../../config";
import { useTheme } from "../../hooks/useTheme";
import { usePolling } from "../../hooks/usePolling";
import PaneHeader from "../../components/PaneHeader";
import { MetricCard, GaugeRow, InfoRow, Card, Section } from "../../components/widgets";

// A5.2 — dep-graph response shape
type DepNode = {
  id: string;
  kind: "module" | "external";
  label: string;
  file?: string;
  lines?: number;
  funcs?: number;
  classes?: number;
  in_degree: number;
  out_degree: number;
};
type DepGraph = {
  ts: number;
  duration_s: number;
  scope: string;
  summary: { modules: number; edges: number; files_scanned: number };
  nodes: DepNode[];
  edges: { source: string; target: string; kind: string }[];
};

export default function AnalyticsPane() {
  const { t } = useTheme();
  const [data, setData] = useState<Record<string, unknown>>({});
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  // A5.2 — Code dependency graph (lazy-load; ~2-4s to compute)
  const [dep, setDep] = useState<DepGraph | null>(null);
  const [depBusy, setDepBusy] = useState(false);
  const [depErr, setDepErr] = useState<string | null>(null);
  const loadDep = useCallback(async () => {
    setDepBusy(true); setDepErr(null);
    try {
      const r = await fetch(`${BACKEND}/analytics/dep-graph?scope=internal`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setDep(await r.json());
    } catch (e) {
      setDepErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDepBusy(false);
    }
  }, []);
  useEffect(() => { loadDep(); }, [loadDep]);

  const load = useCallback(() => {
    fetch(`${BACKEND}/analytics`)
      .then(r => r.json())
      .then(d => { setData(d); setLastUpdate(new Date()); })
      .catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 10_000);

  // Sort helpers — only computed when dep is present
  const fanIn = dep
    ? [...dep.nodes].filter(n => n.kind === "module")
        .sort((a, b) => b.in_degree - a.in_degree).slice(0, 8)
    : [];
  const fanOut = dep
    ? [...dep.nodes].filter(n => n.kind === "module")
        .sort((a, b) => b.out_degree - a.out_degree).slice(0, 8)
    : [];
  const biggest = dep
    ? [...dep.nodes].filter(n => n.kind === "module" && (n.lines ?? 0) > 0)
        .sort((a, b) => (b.lines ?? 0) - (a.lines ?? 0)).slice(0, 6)
    : [];
  const orphans = dep
    ? dep.nodes.filter(n => n.kind === "module" && n.in_degree === 0 && n.out_degree === 0).length
    : 0;

  const lm  = (data.lm     ?? {}) as Record<string, unknown>;
  const gpu = (data.gpu    ?? {}) as Record<string, unknown>;
  const sys = (data.system ?? {}) as Record<string, unknown>;
  const lat = (data.latency?? {}) as Record<string, unknown>;
  const tok = (data.tokens ?? {}) as Record<string, unknown>;
  const latHistory = (lat.history ?? []) as { ms: number }[];
  const maxLat = latHistory.length ? Math.max(...latHistory.map(x => x.ms), 1) : 1;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-6 space-y-6">
        <PaneHeader icon={<BarChart3 size={13} />} title="Analytics" lastRefreshed={lastUpdate} />

        <Section title="LM Studio">
          <div className="grid grid-cols-3 gap-3">
            <MetricCard label="LM Status"     val={(lm.connected ? "Online" : "Offline") as string} ok={!!lm.connected} icon={<Bot size={14} />} />
            <MetricCard label="Avg Latency"   val={`${lat.avg_ms ?? 0}ms`} ok icon={<Activity size={14} />} />
            <MetricCard label="P95 Latency"   val={`${lat.p95_ms ?? 0}ms`} ok icon={<Zap size={14} />} />
          </div>
        </Section>

        {latHistory.length > 0 && (
          <Card title="Latency History (last 20)">
            <div className="flex items-end gap-1 h-16">
              {latHistory.map((pt, i) => (
                <div key={i} className="flex-1 flex flex-col items-center justify-end gap-0.5" title={`${pt.ms}ms`}>
                  <div
                    className={`w-full rounded-sm ${t.accentBg}`}
                    style={{ height: `${(pt.ms / maxLat) * 100}%`, minHeight: "2px" }}
                  />
                </div>
              ))}
            </div>
            <div className="flex justify-between mt-1 text-[9px] text-[var(--text-subtle)]">
              <span>oldest</span><span>newest</span>
            </div>
          </Card>
        )}

        {gpu.name !== undefined && gpu.name !== null && (
          <Card title={`GPU — ${String(gpu.name)}`} actions={<Cpu size={11} className="text-[var(--text-subtle)]" />}>
            <div className="space-y-3">
              <GaugeRow label="VRAM Used" used={gpu.vram_used_mb as number} total={gpu.vram_total_mb as number} unit="MB" color="bg-amber-500" />
              <GaugeRow label="GPU Load"  used={gpu.utilization_pct as number} total={100} unit="%" color="bg-violet-500" />
              <InfoRow  label="Temperature" val={`${gpu.temp_c}°C`} ok={(gpu.temp_c as number) < 85} />
            </div>
          </Card>
        )}

        {sys.ram_total_gb !== undefined && (
          <Card title="System" actions={<HardDrive size={11} className="text-[var(--text-subtle)]" />}>
            <div className="space-y-3">
              <GaugeRow label="RAM Used" used={sys.ram_used_gb as number} total={sys.ram_total_gb as number} unit="GB" color="bg-cyan-500" />
              {sys.cpu_pct !== undefined && (
                <GaugeRow label="CPU" used={sys.cpu_pct as number} total={100} unit="%" color="bg-green-500" />
              )}
            </div>
          </Card>
        )}

        <Card title="Token Usage">
          <InfoRow label="Total tokens (all sessions)" val={String((tok.total ?? 0) as number)} />
          <InfoRow label="Active sessions" val={String(Object.keys((tok.sessions ?? {}) as object).length)} />
        </Card>

        <Card
          title="Code Dependency Graph"
          actions={
            <>
              <Layers size={11} className="text-[var(--text-subtle)]" />
              {dep && (
                <span className="text-[9px] text-[var(--text-subtle)] ml-2">
                  {dep.summary.modules} mods · {dep.summary.edges} edges · {dep.duration_s}s
                </span>
              )}
              <button onClick={loadDep} disabled={depBusy}
                className="ml-2 text-[10px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40 flex items-center gap-1"
                title="Re-scan project for imports">
                <RefreshCw size={10} className={depBusy ? "animate-spin" : ""} />
                {depBusy ? "Scanning…" : "Rescan"}
              </button>
            </>
          }
        >
        {depErr && (
          <p className="text-[10px] text-red-400 border border-red-400/20 bg-red-400/5 rounded p-2 mb-3">
            {depErr}
          </p>
        )}
        {!dep && !depErr && !depBusy && (
          <p className="text-[10px] text-[var(--text-subtle)]">Loading…</p>
        )}
        {dep && (
          <div className="grid grid-cols-2 gap-4">
            {/* Most-imported (fan-in) — load-bearing modules */}
            <div>
              <h4 className="text-[12px] text-[var(--text-muted)] font-medium mb-2">
                Most imported (fan-in)
              </h4>
              <div className="space-y-1 font-mono">
                {fanIn.map(n => (
                  <div key={n.id} className="flex items-baseline gap-2 text-[10px]">
                    <span className={`tabular-nums ${t.accent} w-6 text-right`}>{n.in_degree}</span>
                    <span className="text-[var(--text-muted)] truncate flex-1" title={n.file}>{n.id}</span>
                  </div>
                ))}
              </div>
            </div>
            {/* Most-importing (fan-out) — orchestrators */}
            <div>
              <h4 className="text-[12px] text-[var(--text-muted)] font-medium mb-2">
                Most importing (fan-out)
              </h4>
              <div className="space-y-1 font-mono">
                {fanOut.map(n => (
                  <div key={n.id} className="flex items-baseline gap-2 text-[10px]">
                    <span className={`tabular-nums ${t.accent} w-6 text-right`}>{n.out_degree}</span>
                    <span className="text-[var(--text-muted)] truncate flex-1" title={n.file}>{n.id}</span>
                  </div>
                ))}
              </div>
            </div>
            {/* Biggest by LOC */}
            <div className="col-span-2 pt-2 border-t border-white/[0.04]">
              <h4 className="text-[12px] text-[var(--text-muted)] font-medium mb-2">
                Largest files (lines of code)
              </h4>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono">
                {biggest.map(n => (
                  <div key={n.id} className="flex items-baseline gap-2 text-[10px]">
                    <span className={`tabular-nums ${t.accent} w-12 text-right`}>{n.lines}</span>
                    <span className="text-[var(--text-muted)] truncate flex-1" title={n.file}>{n.id}</span>
                  </div>
                ))}
              </div>
            </div>
            {orphans > 0 && (
              <div className="col-span-2 text-[10px] text-amber-400/70 pt-2 border-t border-white/[0.04]">
                {orphans} orphan module{orphans === 1 ? "" : "s"} (no incoming or outgoing imports — candidates for removal)
              </div>
            )}
          </div>
        )}
        </Card>
      </div>
    </div>
  );
}
