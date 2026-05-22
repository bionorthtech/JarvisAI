/**
 * D10 — Onboarding action banner.
 *
 * Surfaces when App.tsx detects a new project directory. Shows the
 * propose() signals (file count, markers, top extensions) and three
 * actionable buttons: Summarize / Ingest / Dismiss. Each button
 * triggers a callback the parent (App.tsx) wires to the appropriate
 * endpoint. The banner clears on Dismiss; Summarize / Ingest update
 * the banner inline with their result.
 */
import { useTheme } from "../hooks/useTheme";

export interface OnboardingState {
  path: string;
  fileCount: number;
  markers: string[];
  languages: Array<[string, number]>;
  summary?: string;
  summarizing?: boolean;
  ingesting?: boolean;
  ingestMsg?: string;
}

export default function OnboardingBanner({
  state, onSummarize, onIngest, onDismiss,
}: {
  state: OnboardingState;
  onSummarize: () => void;
  onIngest: () => void;
  onDismiss: () => void;
}) {
  const { t } = useTheme();
  const topLangs = state.languages.slice(0, 3)
    .map(([ext, n]) => `${ext}(${n})`)
    .join(", ");
  const markers = state.markers.length ? state.markers.join(", ") : "no markers";
  return (
    <div className={`shrink-0 border-b ${t.accentBorder} bg-white/[0.02] px-5 py-3`}>
      <div className="flex items-baseline gap-3 mb-2 flex-wrap">
        <span className={`text-[9px] ${t.accentDim} uppercase tracking-widest font-bold`}>
          New project
        </span>
        <code className="text-[10px] text-[var(--text)] font-mono truncate max-w-[420px]"
              title={state.path}>
          {state.path}
        </code>
        <span className="text-[10px] text-[var(--text-subtle)]">
          · {state.fileCount} files · {markers} · top: {topLangs || "—"}
        </span>
        <button onClick={onDismiss}
          className="ml-auto text-[10px] text-[var(--text-subtle)] hover:text-[var(--text)]"
          title="Dismiss (marks the path as seen)">
          ✕
        </button>
      </div>

      {state.summary && (
        <div className="text-[11px] text-[var(--text)] leading-relaxed mb-2 italic">
          {state.summary}
        </div>
      )}
      {state.ingestMsg && (
        <div className={`text-[10px] mb-2 ${state.ingestMsg.startsWith("✓") ? "text-emerald-400" : "text-amber-300"}`}>
          {state.ingestMsg}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button onClick={onSummarize} disabled={state.summarizing}
          className={`text-[10px] px-2.5 py-1 rounded border ${t.accentBorder} ${t.accent} ${t.accentBg} disabled:opacity-40`}>
          {state.summarizing ? "Summarizing…" : state.summary ? "Re-summarize" : "Summarize"}
        </button>
        <button onClick={onIngest} disabled={state.ingesting}
          className="text-[10px] px-2.5 py-1 rounded border border-white/[0.08] text-[var(--text)] hover:bg-white/[0.04] disabled:opacity-40">
          {state.ingesting ? "Ingesting…" : "Ingest into memory"}
        </button>
        <button onClick={onDismiss}
          className="text-[10px] px-2.5 py-1 rounded border border-white/[0.04] text-[var(--text-muted)] hover:text-[var(--text)]">
          Dismiss
        </button>
        <span className="ml-auto text-[9px] text-[var(--text-subtle)]">
          Buttons mark this path as seen — JARVIS won't offer again.
        </span>
      </div>
    </div>
  );
}
