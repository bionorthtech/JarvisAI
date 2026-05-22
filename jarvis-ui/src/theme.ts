/**
 * Theme map — two curated themes.
 *
 * The earlier set of 7 accent variants (cyan/green/violet/rose/blue +
 * amber + apple) was carrying its weight only on a "show off a picker"
 * basis; every screenshot and the design system are built around Apple
 * Dark. Trimmed to:
 *
 *   apple  — the default, semantic system colors (macOS Big Sur+).
 *   amber  — warm alternative for users who prefer warmer accents.
 *
 * Existing users with a removed theme key in `localStorage["jarvis-theme"]`
 * fall back to "apple" via the `saved in THEMES` guard in App.tsx.
 *
 * The React context + hook live in `./hooks/useTheme.ts`.
 */
import type { Theme, ThemeConfig } from "./types";

export const THEMES: Record<Theme, ThemeConfig> = {
  apple: {
    label: "Apple Dark",
    accent: "text-[#0a84ff]",
    accentDim: "text-[#0a84ff]/70",
    accentHover: "hover:text-[#409cff]",
    accentBg: "bg-[#0a84ff]/12",
    accentBgHover: "hover:bg-[#0a84ff]/22",
    accentBorder: "border-[#0a84ff]/25",
    userBubbleBg: "bg-[#1c1c1e]",
    userBubbleBorder: "border-[#38383a]",
    statusDot: "bg-[#30d158] shadow-[0_0_8px_rgba(48,209,88,0.55)]",
    lmDot: "bg-[#0a84ff] shadow-[0_0_5px_rgba(10,132,255,0.5)]",
    navActive: "bg-[#0a84ff]/15",
    navActiveText: "text-[#0a84ff]",
    navActiveBorder: "border-[#0a84ff]/30",
    inputProcessing: "border-[#ff9f0a]/40",
    inputFocus: "focus-within:border-[#0a84ff]/40",
    btnBg: "bg-[#0a84ff]/12",
    btnHoverBg: "hover:bg-[#0a84ff]/22",
    confirmAllow: "bg-[#30d158]/15 border-[#30d158]/35 text-[#30d158]",
    confirmAllowHover: "hover:bg-[#30d158]/25",
    glow: "shadow-[0_0_40px_rgba(10,132,255,0.10)]",
  },
  amber: {
    label: "Amber", accent: "text-amber-400", accentDim: "text-amber-600/80",
    accentHover: "hover:text-amber-400", accentBg: "bg-amber-500/10", accentBgHover: "hover:bg-amber-500/20",
    accentBorder: "border-amber-500/20", userBubbleBg: "bg-amber-500/[0.07]", userBubbleBorder: "border-amber-500/[0.12]",
    statusDot: "bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]", lmDot: "bg-amber-400 shadow-[0_0_5px_rgba(251,191,36,0.5)]",
    navActive: "bg-amber-500/[0.08]", navActiveText: "text-amber-400", navActiveBorder: "border-amber-500/[0.12]",
    inputProcessing: "border-amber-500/20", inputFocus: "focus-within:border-amber-500/25",
    btnBg: "bg-amber-500/10", btnHoverBg: "hover:bg-amber-500/20",
    confirmAllow: "bg-amber-500/15 border-amber-500/30 text-amber-300", confirmAllowHover: "hover:bg-amber-500/25",
    glow: "shadow-[0_0_40px_rgba(251,191,36,0.08)]",
  },
};
