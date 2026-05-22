/**
 * B2 — micro widgets used across panes.
 *
 * 2026-05-18 motion pass. Card / MetricCard / MetricTile mount via
 * Framer Motion stagger; Section is the parent that orchestrates the
 * cascade. MetricCard / MetricTile detect numeric values and tween
 * from old → new via `tweenNumber`. Status-dot dot in MetricTile
 * gets the `pulse-glow` class when status is "ok" so live tiles
 * read as alive.
 *
 * Primitives:
 * - MetricCard — labeled number, optional good/bad coloring, number tween
 * - GaugeRow   — labelled progress bar
 * - InfoRow    — key:value strip
 * - Card       — elevated surface, sentence-case title, stagger child
 * - StatRow    — slim icon+label+value row
 * - ActionBtn  — async-aware button with min-visible busy spinner
 * - NavBtn     — sidebar nav button (white-pill active)
 * - MetricTile — big-readout tile, number tween, pulse-glow on live
 * - Section    — page-section heading + stagger parent
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { useTheme } from "../hooks/useTheme";
import {
  useMotionPreset, tweenNumber, parseNumeric, formatNumeric,
} from "../utils/motion";

/**
 * Render a value string — if it parses as numeric, tween from
 * previous to next on prop change; otherwise render as-is.
 */
function AnimatedValue({ val, className }: { val: string; className?: string }) {
  const parsed = parseNumeric(val);
  const [display, setDisplay] = useState(val);
  const prevNumRef = useRef<number | null>(parsed?.num ?? null);

  useEffect(() => {
    const p = parseNumeric(val);
    if (!p) {
      setDisplay(val);
      prevNumRef.current = null;
      return;
    }
    const from = prevNumRef.current ?? p.num;
    prevNumRef.current = p.num;
    if (from === p.num) {
      setDisplay(val);
      return;
    }
    return tweenNumber(from, p.num, 400, n => {
      setDisplay(formatNumeric(n, val) + p.suffix);
    });
  }, [val]);

  return <span className={className}>{display}</span>;
}

export function MetricCard({ label, val, ok, icon }: { label: string; val: string; ok?: boolean; icon: ReactNode }) {
  const { t } = useTheme();
  const m = useMotionPreset();
  return (
    <motion.div
      variants={m.staggerChild}
      className="bg-[var(--surface-1)] rounded-2xl p-5 flex flex-col gap-2 apple-card-shadow"
    >
      <div className="flex items-center gap-2 text-[var(--text-subtle)]">
        {icon}<span className="text-[12px]">{label}</span>
      </div>
      <AnimatedValue
        val={val}
        className={`text-[28px] font-semibold leading-none ${
          ok === false ? "text-red-400" : ok === true ? t.accent : "text-[var(--text)]"
        }`}
      />
    </motion.div>
  );
}

export function GaugeRow({ label, used, total, unit, color }: { label: string; used: number; total: number; unit: string; color: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-[12px]">
        <span className="text-[var(--text-muted)]">{label}</span>
        <span className="text-[var(--text-subtle)] tabular-nums">{used}{unit} / {total}{unit} ({pct}%)</span>
      </div>
      <div className="h-1.5 bg-white/[0.04] rounded-full overflow-hidden">
        <motion.div
          className={`h-full rounded-full ${color} ${pct > 85 ? "opacity-100" : "opacity-70"}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.4, ease: [0.4, 0, 0.2, 1] }}
        />
      </div>
    </div>
  );
}

export function InfoRow({ label, val, ok }: { label: string; val: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between py-1">
      <span className="text-[12px] text-[var(--text-muted)]">{label}</span>
      <span className={`text-[12px] tabular-nums ${
        ok === true ? "text-emerald-400" : ok === false ? "text-red-400" : "text-[var(--text)]"
      }`}>{val}</span>
    </div>
  );
}

export function Card({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  const m = useMotionPreset();
  return (
    <motion.div
      variants={m.staggerChild}
      className="bg-[var(--surface-1)] rounded-2xl p-6 apple-card-shadow"
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm text-[var(--text-muted)] font-medium">{title}</h3>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </motion.div>
  );
}

/**
 * Big-readout tile. Used in Welcome / Dashboard / Analytics shell
 * surfaces for status rows. Status="ok" gets a glowing live dot.
 */
export function MetricTile({ label, value, sublabel, status, icon }: {
  label: string;
  value: string;
  sublabel?: string;
  status?: "ok" | "warn" | "bad" | "muted";
  icon?: ReactNode;
}) {
  const { t } = useTheme();
  const m = useMotionPreset();
  const dotColor =
    status === "ok"   ? t.statusDot :
    status === "warn" ? "bg-amber-500" :
    status === "bad"  ? "bg-red-500"   :
                        "bg-[var(--text-faint)]";
  const valueColor =
    status === "bad"  ? "text-red-300" :
    status === "warn" ? "text-amber-300" :
    status === "ok"   ? t.accent :
                        "text-[var(--text)]";
  return (
    <motion.div
      variants={m.staggerChild}
      className="bg-[var(--surface-1)] rounded-2xl p-6 flex flex-col gap-3 min-h-[120px] apple-card-shadow"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2 text-[var(--text-subtle)]">
          {icon}
          <span className="text-[12px]">{label}</span>
        </div>
        {status && (
          <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor} ${status === "ok" ? "pulse-glow" : ""}`} />
        )}
      </div>
      <AnimatedValue
        val={value}
        className={`text-[32px] font-semibold leading-none tabular-nums ${valueColor}`}
      />
      {sublabel && <div className="text-[12px] text-[var(--text-subtle)]">{sublabel}</div>}
    </motion.div>
  );
}

/**
 * Page-section heading. Renders a large sentence-case title above its
 * children and orchestrates a stagger cascade for any Card /
 * MetricCard / MetricTile inside.
 */
export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  const m = useMotionPreset();
  return (
    <motion.section
      className="space-y-4"
      variants={m.staggerParent}
      initial="hidden"
      animate="visible"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-[var(--text)]">{title}</h2>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </motion.section>
  );
}

export function StatRow({ icon, label, val, ok = true }: { icon: ReactNode; label: string; val: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2 text-[var(--text-muted)]">{icon}<span className="text-[12px]">{label}</span></div>
      <span className={`text-[12px] tabular-nums font-medium ${ok ? "text-[var(--text)]" : "text-red-400"}`}>{val}</span>
    </div>
  );
}

export function ActionBtn({ icon, label, onClick, danger = false }: { icon: ReactNode; label: string; onClick: () => void | Promise<void>; danger?: boolean }) {
  // G5.3 — observable busy state so the user knows a click landed.
  const [busy, setBusy] = useState(false);
  const handle = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await onClick();
    } finally {
      // Show busy for ≥250ms so quick fetches aren't invisible.
      setTimeout(() => setBusy(false), 250);
    }
  };
  return (
    <button
      onClick={handle}
      disabled={busy}
      className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] rounded-lg transition-colors disabled:opacity-50 ${
        danger
          ? "bg-white/[0.04] text-[var(--text-muted)] hover:bg-red-500/15 hover:text-red-400"
          : "bg-white/[0.04] text-[var(--text-muted)] hover:bg-white/[0.08] hover:text-[var(--text)]"
      }`}
    >
      <span className={busy ? "animate-spin" : ""}>{icon}</span>{label}
    </button>
  );
}

/**
 * Compact chip-style button used in card headers, list rows, and
 * filter strips. Replaces the repeated `text-[10px] px-2 py-0.5
 * border ... rounded text-[var(--text-muted)] hover:text-[var(--text)]`
 * pattern. Not styled as a "primary action" — that's the accent
 * button in the theme. Just a quiet, themed chip.
 */
export function ChipBtn({ icon, label, onClick, disabled = false, active = false, title }: {
  icon?: ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition-colors disabled:opacity-40 ${
        active
          ? "bg-white/[0.08] text-[var(--text)]"
          : "text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-white/[0.04]"
      }`}
    >
      {icon}{label}
    </button>
  );
}

export function NavBtn({ icon, label, active, onClick }: { icon: ReactNode; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-[13px] transition-colors ${
        active
          ? "bg-white text-black font-medium"
          : "text-[var(--text-subtle)] hover:text-[var(--text)] hover:bg-white/[0.04]"
      }`}
    >
      {icon}{label}
    </button>
  );
}
