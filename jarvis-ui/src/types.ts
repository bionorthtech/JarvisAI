/**
 * B2 — shared UI types.
 *
 * Extracted from App.tsx so per-mode files (still under jarvis-ui/src/modes/
 * once that split lands) and shared components can import them
 * without circular dependencies on App.tsx itself.
 */

export type AppMode =
  | "welcome" | "chat" | "terminal" | "coder" | "dashboard"
  | "analytics" | "logs" | "apps" | "brain" | "bots"
  | "theater" | "settings";

export type Theme =
  | "apple"      // Apple Dark, system colors. Default.
  | "amber";     // warm alternative

export type MsgRole = "user" | "assistant" | "system";

export interface ToolCallEvent {
  id: string;
  name: string;
  args: Record<string, unknown>;
  output?: string;
  done: boolean;
}

export interface Message {
  id: string;
  role: MsgRole;
  content: string;
  ts: Date;
  toolCalls?: ToolCallEvent[];
  isStreaming?: boolean;
}

export interface LMStatus {
  connected: boolean;
  models: string[];
  latency_ms: number;
  error?: string | null;
  blocked?: boolean;
  blocked_hint?: string | null;
}

export interface MemoryStats {
  file_chunks: number;
  chat_turns: number;
  project: string;
}

export interface ConfirmPending {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  tier: string;
  description: string;
}

export interface FsEntry {
  name: string;
  type: "file" | "dir";
  size?: number | null;
}

export interface Toast {
  id: string;
  message: string;
  type: "info" | "warn" | "error";
}

/**
 * ThemeConfig — what each entry in THEMES looks like. Kept here so
 * downstream files can type the `t` prop without re-deriving it.
 */
export interface ThemeConfig {
  label: string;
  accent: string;
  accentDim: string;
  accentHover: string;
  accentBg: string;
  accentBgHover: string;
  accentBorder: string;
  userBubbleBg: string;
  userBubbleBorder: string;
  statusDot: string;
  lmDot: string;
  navActive: string;
  navActiveText: string;
  navActiveBorder: string;
  inputProcessing: string;
  inputFocus: string;
  btnBg: string;
  btnHoverBg: string;
  confirmAllow: string;
  confirmAllowHover: string;
  glow: string;
}
