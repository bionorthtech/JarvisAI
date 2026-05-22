/**
 * B2 — theme context + hook.
 *
 * `App.tsx` instantiates the provider and feeds it `{theme, t, setTheme}`.
 * Every other component reads via `const { t } = useTheme()`.
 *
 * Keeping the context here (not in `App.tsx`) means per-mode files can
 * import the hook without circular dependencies on the app shell.
 */
import { createContext, useContext } from "react";
import type { Theme, ThemeConfig } from "../types";
import { THEMES } from "../theme";

export interface ThemeCtxValue {
  theme: Theme;
  t: ThemeConfig;
  setTheme: (th: Theme) => void;
}

export const ThemeCtx = createContext<ThemeCtxValue>({
  theme: "apple",
  t: THEMES.apple,
  setTheme: () => {},
});

export function useTheme(): ThemeCtxValue {
  return useContext(ThemeCtx);
}
