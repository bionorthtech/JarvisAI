/**
 * B2 — DANGER/CRITICAL/CAUTION confirmation modal.
 *
 * Fetches `/confirm/preview` on mount and renders the impact
 * analysis (affected paths, reversibility, simulated command,
 * dry-run analog) above the allow/deny buttons. App passes the
 * ConfirmPending request + approve/deny callbacks.
 */
import { useEffect, useState } from "react";
import { Shield, X, Check } from "lucide-react";
import { motion } from "framer-motion";

import { BACKEND } from "../config";
import type { ConfirmPending } from "../types";
import { useTheme } from "../hooks/useTheme";
import { easeStandard, durBase } from "../utils/motion";

interface ConfirmImpact {
  summary: string;
  simulated_command: string;
  dry_run: string;
  affected: string;
  reversible: string;
}

export default function ConfirmModal({ req, onAllow, onDeny }: { req: ConfirmPending; onAllow: () => void; onDeny: () => void }) {
  const { t } = useTheme();
  const [impact, setImpact] = useState<ConfirmImpact | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const tierColor: Record<string, string> = {
    DANGER: "text-red-400 bg-red-500/10 border-red-500/20",
    CRITICAL: "text-red-300 bg-red-500/15 border-red-500/30",
    CAUTION: "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
    SAFE: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  };
  const reversibleColor = (r: string) =>
    r.startsWith("yes") ? "text-emerald-400" :
    r.startsWith("no")  ? "text-red-400"     :
    r.startsWith("partial") ? "text-yellow-400" : "text-[var(--text-muted)]";

  useEffect(() => {
    let cancelled = false;
    setPreviewLoading(true); setPreviewError(null); setImpact(null);
    fetch(`${BACKEND}/confirm/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_name: req.tool, args: req.args }),
    })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((data: { impact: ConfirmImpact }) => { if (!cancelled) setImpact(data.impact); })
      .catch((e: Error) => { if (!cancelled) setPreviewError(e.message); })
      .finally(() => { if (!cancelled) setPreviewLoading(false); });
    return () => { cancelled = true; };
  }, [req.id, req.tool, req.args]);

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
    >
      <motion.div
        className="bg-[var(--surface-1)] rounded-2xl p-6 w-[480px] apple-card-shadow"
        initial={{ opacity: 0, scale: 0.95, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97 }}
        transition={{ duration: durBase, ease: easeStandard }}
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="w-8 h-8 rounded-full bg-red-500/10 border border-red-500/20 flex items-center justify-center"><Shield size={14} className="text-red-400" /></div>
          <div>
            <h3 className="text-sm text-[var(--text)] font-bold">Action Requires Approval</h3>
            <p className="text-[10px] text-[var(--text-subtle)]">JARVIS wants to execute a privileged action</p>
          </div>
          <span className={`ml-auto text-[9px] font-bold px-2 py-0.5 rounded border ${tierColor[req.tier] ?? tierColor.CAUTION}`}>{req.tier}</span>
        </div>
        <div className="bg-[var(--surface-2)] border border-white/[0.04] rounded-xl p-4 mb-3">
          <p className="text-[12px] text-[var(--text-muted)] mb-1">Tool</p>
          <code className="text-[12px] text-[var(--text)] font-mono">{req.tool}</code>
          <p className="text-[12px] text-[var(--text-muted)] mt-3 mb-1">Action</p>
          <p className="text-[12px] text-[var(--text)]">{req.description}</p>
          {Object.keys(req.args).length > 0 && (<>
            <p className="text-[12px] text-[var(--text-muted)] mt-3 mb-1">Arguments</p>
            <pre className="text-[10px] text-[var(--text-muted)] whitespace-pre-wrap break-all">{JSON.stringify(req.args, null, 2)}</pre>
          </>)}
        </div>
        <div className="bg-[var(--surface-2)] border border-white/[0.04] rounded-xl p-4 mb-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[12px] text-[var(--text-muted)]">Impact preview</p>
            {previewLoading && <span className="text-[9px] text-[var(--text-subtle)]">analyzing…</span>}
          </div>
          {previewError && <p className="text-[10px] text-red-400">preview unavailable: {previewError}</p>}
          {impact && (
            <div className="space-y-2 text-[11px]">
              <div><span className="text-[var(--text-subtle)]">affected: </span><span className="text-[var(--text)] font-mono">{impact.affected}</span></div>
              <div><span className="text-[var(--text-subtle)]">reversible: </span><span className={`font-bold ${reversibleColor(impact.reversible)}`}>{impact.reversible}</span></div>
              {impact.simulated_command && (
                <div>
                  <p className="text-[var(--text-subtle)] mb-0.5">command</p>
                  <code className="block text-[var(--text)] font-mono break-all whitespace-pre-wrap">{impact.simulated_command}</code>
                </div>
              )}
              <div>
                <p className="text-[var(--text-subtle)] mb-0.5">dry-run analog</p>
                <code className="block text-[var(--text-muted)] font-mono break-all whitespace-pre-wrap">{impact.dry_run}</code>
              </div>
            </div>
          )}
        </div>
        <div className="flex gap-3">
          <button onClick={onDeny} className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg border border-white/[0.06] text-[12px] text-[var(--text-muted)] hover:text-red-400 hover:border-red-500/20 transition-all"><X size={13} /> Deny</button>
          <button onClick={onAllow} className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg border text-[12px] transition-all ${t.confirmAllow} ${t.confirmAllowHover}`}><Check size={13} /> Allow</button>
        </div>
      </motion.div>
    </motion.div>
  );
}
