/**
 * B2 — Settings mode.
 *
 * Cards (top to bottom):
 *   - Appearance (theme picker — Apple Dark + 6 accents)
 *   - LM Studio status + Test Connection
 *   - Active model
 *   - Reasoning effort
 *   - Connections probe
 *   - Compare modes
 *   - Memory (RAG)
 *   - Backend
 */
import { useEffect, useState } from "react";
import { Settings, RefreshCw, Check, AlertTriangle } from "lucide-react";

import type { LMStatus, MemoryStats, Theme } from "../../types";
import { BACKEND } from "../../config";
import { useTheme } from "../../hooks/useTheme";
import PaneHeader from "../../components/PaneHeader";
import { Card, InfoRow, Section } from "../../components/widgets";

// ─── Theme picker ────────────────────────────────────────────────────────────

function ThemePicker() {
  const { theme, setTheme } = useTheme();
  // Curated set — see theme.ts for the rationale on the trim.
  const items: { key: Theme; dot: string; label: string }[] = [
    { key: "apple",  dot: "bg-[#0a84ff]",  label: "Apple Dark" },
    { key: "amber",  dot: "bg-amber-400",  label: "Amber" },
  ];
  return (
    <Card title="Appearance">
      <div className="grid grid-cols-4 gap-2">
        {items.map(({ key, dot, label }) => (
          <button
            key={key}
            onClick={() => setTheme(key)}
            className={`flex flex-col items-center gap-2 py-3 rounded-lg border transition-all ${
              theme === key
                ? "border-white/25 bg-white/[0.05]"
                : "border-white/[0.04] hover:border-white/10"
            }`}
            aria-pressed={theme === key}
          >
            <span className={`w-4 h-4 rounded-full ${dot}`} />
            <span className="text-[9px] text-[var(--text-muted)]">{label}</span>
          </button>
        ))}
      </div>
      <p className="mt-3 text-[10px] text-[var(--text-subtle)] leading-relaxed">
        Apple Dark uses system colors (blue for primary actions, red for danger,
        green for confirmation). The other themes recolor the accent only.
      </p>
    </Card>
  );
}

// ─── Settings view ───────────────────────────────────────────────────────────

interface Props {
  lm:        LMStatus;
  model:     string;
  setModel:  (m: string) => void;
  memStats:  MemoryStats;
  onRefresh: () => void;
}

interface ProbeResult { ok: boolean; latency_ms: number; detail: string }
interface Probes {
  lm_studio: ProbeResult;
  chromadb:  ProbeResult;
  audit_db:  ProbeResult;
}

interface CompareResp {
  direct: { ms: number; text: string; error: string | null };
  full:   { ms: number; text: string; error: string | null;
            breakdown?: Record<string, number> };
  delta_ms: number;
}

export default function SettingsView({ lm, model, setModel, memStats, onRefresh }: Props) {
  const { t } = useTheme();

  // G5.2 — visible test-connection feedback
  const [testing, setTesting] = useState(false);
  const [lastTestedAt, setLastTestedAt] = useState<Date | null>(null);
  const [lastTestResult, setLastTestResult] = useState<"ok" | "fail" | null>(null);

  // G6.6 — reasoning effort
  const [effort, setEffort] = useState<string>("none");
  useEffect(() => {
    fetch(`${BACKEND}/perf/reasoning-effort`)
      .then(r => r.json())
      .then(d => setEffort(d.reasoning_effort ?? "none"))
      .catch(() => {});
  }, []);
  const setEffortAndSave = async (value: string) => {
    setEffort(value);
    await fetch(`${BACKEND}/perf/reasoning-effort`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ effort: value }),
    }).catch(() => {});
  };

  // G5.2 — unified probe
  const [probes, setProbes] = useState<Probes | null>(null);
  const [probesBusy, setProbesBusy] = useState(false);
  const [probesAt, setProbesAt] = useState<Date | null>(null);
  const runProbes = async () => {
    if (probesBusy) return;
    setProbesBusy(true);
    try {
      const r = await fetch(`${BACKEND}/probe/all`);
      if (r.ok) {
        setProbes(await r.json());
        setProbesAt(new Date());
      }
    } catch { /* ignore */ } finally {
      setProbesBusy(false);
    }
  };
  useEffect(() => { runProbes(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // G4.2 — compare
  const [comparePrompt, setComparePrompt] = useState("what's 2+2?");
  const [compareBusy, setCompareBusy] = useState(false);
  const [compareResult, setCompareResult] = useState<CompareResp | null>(null);
  const runCompare = async () => {
    if (!comparePrompt.trim() || compareBusy) return;
    setCompareBusy(true);
    setCompareResult(null);
    try {
      const r = await fetch(`${BACKEND}/perf/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: comparePrompt }),
      });
      if (r.ok) setCompareResult(await r.json());
    } catch { /* ignore */ } finally {
      setCompareBusy(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setLastTestResult(null);
    try {
      const r = await fetch(`${BACKEND}/health`, { signal: AbortSignal.timeout(4000) });
      if (r.ok) { await r.json(); setLastTestResult("ok"); }
      else      { setLastTestResult("fail"); }
    } catch {
      setLastTestResult("fail");
    } finally {
      setLastTestedAt(new Date());
      setTesting(false);
      onRefresh();
    }
  };

  return (
    <div className="flex-1 overflow-y-auto">
     <div className="max-w-3xl mx-auto p-8 space-y-8">
      <PaneHeader icon={<Settings size={13} />} title="Settings" />

      <Section title="Appearance">
        <ThemePicker />
      </Section>

      <Section title="LM Studio">
        <Card title="LM Studio">
          <InfoRow label="Base URL" val="http://localhost:1234/v1" />
          <InfoRow label="Status" val={lm.connected ? "● Connected" : "○ Disconnected"} ok={lm.connected} />
          {lm.connected && <InfoRow label="Latency" val={`${Math.round(lm.latency_ms)}ms`} />}
          {lm.error && (
            <div className="flex gap-2 mt-3 p-3 bg-red-500/5 border border-red-500/10 rounded-lg text-[11px] text-red-400">
              <AlertTriangle size={12} className="shrink-0 mt-0.5" /><span>{lm.error}</span>
            </div>
          )}
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={runTest}
              disabled={testing}
              className={`flex items-center gap-2 text-[11px] ${t.accentDim} ${t.accentHover} transition-colors disabled:opacity-40`}
            >
              <RefreshCw size={11} className={testing ? "animate-spin" : ""} />
              {testing ? "Testing…" : "Test Connection"}
            </button>
            {lastTestedAt && !testing && (
              <span className="text-[10px] text-[var(--text-subtle)]">
                {lastTestResult === "ok"
                  ? <span className="text-green-500">✓</span>
                  : <span className="text-red-400">✗</span>}{" "}
                tested {lastTestedAt.toLocaleTimeString()}
                {lastTestResult === "ok" && lm.connected && ` · ${Math.round(lm.latency_ms)}ms`}
              </span>
            )}
          </div>
        </Card>

        <Card title="Active Model">
          {lm.models.length > 0 ? (
            <div className="space-y-1.5">
              {lm.models.map(m => (
                <button
                  key={m}
                  onClick={() => setModel(m)}
                  className={`w-full flex items-center justify-between px-3 py-2 rounded-lg border text-[11px] transition-all ${
                    model === m
                      ? `${t.navActive} ${t.navActiveText} ${t.navActiveBorder}`
                      : "border-white/[0.04] text-[var(--text-subtle)] hover:text-[var(--text)] hover:border-white/10"
                  }`}
                >
                  <span className="font-mono">{m}</span>{model === m && <Check size={11} />}
                </button>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-[var(--text-subtle)]">No models loaded in LM Studio.</p>
          )}
        </Card>

      </Section>

      <Section title="Performance">
        <Card title="Reasoning effort">
          <p className="text-[10px] text-[var(--text-subtle)] mb-2">
            Controls how much the model thinks before answering. Lower = faster
            but more reliant on JARVIS's own planner. Doesn't disable thinker,
            memory, or tools.
          </p>
          <div className="grid grid-cols-4 gap-2">
            {(["none", "low", "medium", "high"] as const).map(level => (
              <button
                key={level}
                onClick={() => setEffortAndSave(level)}
                className={`px-2 py-1.5 rounded-lg border text-[10px] uppercase tracking-widest transition-all ${
                  effort === level
                    ? `${t.navActive} ${t.navActiveText} ${t.navActiveBorder}`
                    : "border-white/[0.04] text-[var(--text-subtle)] hover:text-[var(--text)] hover:border-white/10"
                }`}
              >
                {level}
              </button>
            ))}
          </div>
          <p className="text-[9px] text-[var(--text-subtle)] mt-2">
            Current: <span className={t.accent}>{effort}</span> · only applies
            to reasoning-capable models (qwen2.5-coder ignores this; gemma-3-
            thinking variants use it).
          </p>
        </Card>


      </Section>

      <Section title="Diagnostics">
        <Card title="Connections">
          <div className="flex items-center gap-2 mb-2">
            <p className="text-[10px] text-[var(--text-subtle)] flex-1">
              Live probe of every connector JARVIS depends on.
              {probesAt && ` Tested ${probesAt.toLocaleTimeString()}.`}
            </p>
            <button
              onClick={runProbes}
              disabled={probesBusy}
              className="text-[10px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40 flex items-center gap-1"
            >
              <RefreshCw size={10} className={probesBusy ? "animate-spin" : ""} /> Test all
            </button>
          </div>
          {probes && (
            <div className="space-y-1.5">
              {([
                ["lm_studio", "LM Studio"],
                ["chromadb",  "ChromaDB"],
                ["audit_db",  "Audit DB"],
              ] as const).map(([key, label]) => {
                const p = probes[key];
                return (
                  <div key={key} className="flex items-center gap-2 text-[10px]">
                    <span className={p.ok ? "text-green-500" : "text-red-400"}>{p.ok ? "●" : "○"}</span>
                    <span className="text-[var(--text-muted)] w-20">{label}</span>
                    <span className="text-[var(--text-subtle)]">{p.detail}</span>
                    <span className={`ml-auto ${t.accent}`}>{p.latency_ms}ms</span>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="Compare modes">
          <p className="text-[10px] text-[var(--text-subtle)] mb-2">
            Fan one prompt out to (a) raw LM Studio and (b) the full JARVIS
            pipeline. Side-by-side so you can see exactly what JARVIS adds.
          </p>
          <div className="flex gap-2 mb-2">
            <input
              value={comparePrompt}
              onChange={e => setComparePrompt(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") runCompare(); }}
              placeholder="prompt to compare"
              className="flex-1 bg-[var(--surface-2)] border border-white/[0.04] rounded px-2 py-1 text-[11px] outline-none focus:border-white/10 font-mono"
            />
            <button
              onClick={runCompare}
              disabled={!comparePrompt.trim() || compareBusy}
              className={`text-[10px] px-3 py-1 rounded ${t.accentBg} ${t.accent} disabled:opacity-40`}
            >
              {compareBusy ? "Running…" : "Run"}
            </button>
          </div>
          {compareResult && (
            <div className="space-y-2 mt-3">
              <div className="bg-[var(--surface-2)] border border-white/[0.04] rounded p-2">
                <div className="flex justify-between text-[10px] mb-1">
                  <span className="text-[var(--text-muted)] uppercase tracking-widest">Direct LM Studio</span>
                  <span className={t.accent}>{compareResult.direct.ms} ms</span>
                </div>
                {compareResult.direct.error
                  ? <p className="text-[10px] text-red-400">{compareResult.direct.error}</p>
                  : <p className="text-[10px] text-[var(--text-muted)] line-clamp-2">{compareResult.direct.text || "(empty)"}</p>}
              </div>
              <div className="bg-[var(--surface-2)] border border-white/[0.04] rounded p-2">
                <div className="flex justify-between text-[10px] mb-1">
                  <span className="text-[var(--text-muted)] uppercase tracking-widest">JARVIS full pipeline</span>
                  <span className={t.accent}>{compareResult.full.ms} ms</span>
                </div>
                {compareResult.full.error
                  ? <p className="text-[10px] text-red-400">{compareResult.full.error}</p>
                  : <p className="text-[10px] text-[var(--text-muted)] line-clamp-2">{compareResult.full.text || "(empty)"}</p>}
                {compareResult.full.breakdown && (
                  <div className="mt-2 grid grid-cols-3 gap-1 text-[9px] text-[var(--text-subtle)]">
                    {Object.entries(compareResult.full.breakdown).map(([k, v]) => (
                      <div key={k}>
                        <span className="text-[var(--text-subtle)]">{k.replace(/_ms$/, "")}</span>{" "}
                        {Math.round(v as number)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <p className="text-[10px] text-[var(--text-muted)]">
                JARVIS overhead:{" "}
                <span className={compareResult.delta_ms > 1000 ? "text-amber-400" : "text-[var(--text-muted)]"}>
                  +{compareResult.delta_ms} ms
                </span>
                {compareResult.full.breakdown
                  ? ` · thinker ${Math.round(compareResult.full.breakdown.thinker_ms || 0)}ms · memory ${Math.round(compareResult.full.breakdown.memory_ms || 0)}ms`
                  : ""}
              </p>
            </div>
          )}
        </Card>

      </Section>

      <Section title="Memory & Backend">
        <Card title="Memory (RAG)">
          <InfoRow label="File chunks" val={String(memStats.file_chunks)} ok={memStats.file_chunks > 0} />
          <InfoRow label="Chat turns" val={String(memStats.chat_turns)} ok={memStats.chat_turns > 0} />
          <InfoRow label="Project" val={memStats.project} />
        </Card>

        <Card title="Backend">
          <InfoRow label="API" val="127.0.0.1:8000" />
          <InfoRow label="Terminal" val="ws://127.0.0.1:8000/ws/terminal" />
          <div className="mt-3 p-3 bg-[var(--surface-2)] border border-white/[0.03] rounded-lg">
            <p className="text-[10px] text-[var(--text-subtle)] mb-2">Start backend:</p>
            <code className="text-[10px] text-[var(--text-muted)] block">cd ~/jarvis</code>
            <code className={`text-[10px] ${t.accentDim} block`}>venv/bin/python3 main.py</code>
          </div>
        </Card>
      </Section>
     </div>
    </div>
  );
}
