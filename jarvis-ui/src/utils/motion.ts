/**
 * Shared motion tokens + helpers (2026-05-18 motion pass).
 *
 * One source of truth for easings, durations, and variants so every
 * mode plays the same beat. All variant-returning helpers respect
 * `prefers-reduced-motion` via `useMotionPreset`.
 */
import { useReducedMotion, type Variants } from "framer-motion";

// Material-standard ease — fast at start, decelerates into rest.
export const easeStandard = [0.4, 0, 0.2, 1] as const;

export const durFast = 0.15;
export const durBase = 0.22;
export const durSlow = 0.32;

const noop: Variants = {
  hidden:  { opacity: 1 },
  visible: { opacity: 1 },
};

const fadeUp: Variants = {
  hidden:  { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: durBase, ease: easeStandard } },
};

const fadeIn: Variants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: { duration: durBase, ease: easeStandard } },
};

const scaleIn: Variants = {
  hidden:  { opacity: 0, scale: 0.95 },
  visible: { opacity: 1, scale: 1, transition: { duration: durBase, ease: easeStandard } },
  exit:    { opacity: 0, scale: 0.97, transition: { duration: durFast, ease: easeStandard } },
};

const slideInRight: Variants = {
  hidden:  { opacity: 0, x: 16 },
  visible: { opacity: 1, x: 0, transition: { duration: 0.18, ease: easeStandard } },
  exit:    { opacity: 0, x: -8, transition: { duration: 0.12, ease: easeStandard } },
};

const staggerParent: Variants = {
  hidden:  { opacity: 1 },
  visible: { opacity: 1, transition: { staggerChildren: 0.04, delayChildren: 0.02 } },
};

const staggerChild: Variants = {
  hidden:  { opacity: 0, y: 6 },
  visible: { opacity: 1, y: 0, transition: { duration: durBase, ease: easeStandard } },
};

/**
 * Returns the project's motion variants — or no-op variants if the
 * user has `prefers-reduced-motion: reduce`. Call from any component
 * that uses Framer Motion variants so the a11y gate is uniform.
 */
export function useMotionPreset() {
  const reduce = useReducedMotion();
  if (reduce) {
    return {
      reduce: true,
      fadeUp: noop,
      fadeIn: noop,
      scaleIn: noop,
      slideInRight: noop,
      staggerParent: noop,
      staggerChild: noop,
    };
  }
  return {
    reduce: false,
    fadeUp,
    fadeIn,
    scaleIn,
    slideInRight,
    staggerParent,
    staggerChild,
  };
}

/**
 * Imperative number tween used by MetricCard / MetricTile so polled
 * values glide instead of jumping. Returns a cancel fn. Respects
 * `prefers-reduced-motion` (jumps straight to `to`).
 */
export function tweenNumber(
  from: number,
  to: number,
  durMs: number,
  setter: (n: number) => void,
): () => void {
  if (typeof window !== "undefined") {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce || from === to || !isFinite(from) || !isFinite(to)) {
      setter(to);
      return () => {};
    }
  }
  let raf = 0;
  let cancelled = false;
  const start = performance.now();
  const delta = to - from;
  // ease-out cubic — feels right for "settling on a number"
  const ease = (t: number) => 1 - Math.pow(1 - t, 3);
  const tick = (now: number) => {
    if (cancelled) return;
    const t = Math.min(1, (now - start) / durMs);
    setter(from + delta * ease(t));
    if (t < 1) raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return () => { cancelled = true; cancelAnimationFrame(raf); };
}

/**
 * Parse the leading number out of a value string like "42%" / "1.2GB"
 * / "Online". Returns { num, suffix } or null if non-numeric.
 */
const NUM_RE = /^(-?\d[\d,.]*)(\s*[a-zA-Z%°]+(?:\s+\w+)?)?$/;
export function parseNumeric(val: string): { num: number; suffix: string } | null {
  const m = NUM_RE.exec(val.trim());
  if (!m) return null;
  const raw = m[1].replace(/,/g, "");
  const num = parseFloat(raw);
  if (!isFinite(num)) return null;
  return { num, suffix: m[2] ?? "" };
}

/**
 * Format a tweened number back into display form. Picks decimal
 * places to match the original value's precision so we don't show
 * "42.000000" when the input was "42".
 */
export function formatNumeric(n: number, original: string): string {
  const m = NUM_RE.exec(original.trim());
  if (!m) return String(n);
  const raw = m[1];
  const dot = raw.indexOf(".");
  const decimals = dot >= 0 ? raw.length - dot - 1 : 0;
  return n.toFixed(decimals);
}
