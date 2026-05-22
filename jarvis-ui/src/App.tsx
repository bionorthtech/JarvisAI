import { useState, useRef, useEffect, useCallback, lazy, Suspense } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useMotionPreset } from "./utils/motion";
// Per-mode files own their own icon + UI imports; App.tsx itself doesn't
// render any icons or chrome since the B2.7–B2.11 split.
import { listen } from "@tauri-apps/api/event";

// ─── Types ────────────────────────────────────────────────────────────────────
// Moved to ./types.ts as part of the B2 frontend split. Re-exported here
// so existing JSX inside this file keeps working without changes.

import type {
  AppMode, Theme, Message, LMStatus,
  MemoryStats, ConfirmPending, Toast,
} from "./types";
import { BACKEND, WS_BACKEND, POLL_MS } from "./config";

// ─── Theme system ─────────────────────────────────────────────────────────────
// THEMES map, ThemeCtx, and useTheme moved out for the B2 split:
//   THEMES   → ./theme.ts          (static data)
//   ThemeCtx → ./hooks/useTheme.ts (context + hook)
// Both are imported here. App.tsx still owns provider instantiation.

import { THEMES } from "./theme";
import { ThemeCtx } from "./hooks/useTheme";

// ─── Constants ────────────────────────────────────────────────────────────────

// BACKEND / WS_BACKEND / POLL_MS moved to ./config.ts — imported at the top.


// uid, fmtTime, fmtDate, fmtAgo moved to ./utils/format.ts (B2).
import { uid } from "./utils/format";

// usePolling moved to ./hooks/usePolling.ts (B2). Imported per-mode now.

// toolIcon + toolLabel moved with ChatPane to ./modes/chat (B2).

// WelcomeScreen moved to ./modes/welcome/index.tsx (B2 — first per-mode split).
import WelcomeScreen from "./modes/welcome";

// ChatPane (+ TopBar / Bubble / ToolCard / InputDock / StreamDots /
// LMProgressStrip / toolIcon / toolLabel) moved to ./modes/chat (B2).
const ChatPane = lazy(() => import("./modes/chat"));
import OnboardingBanner, { type OnboardingState } from "./components/OnboardingBanner";
import LMBlockedBanner from "./components/LMBlockedBanner";

// DashboardPane (+ inline ReplayPane + helpers + useLiveWS hook) moved
// to ./modes/dashboard/index.tsx (B2).
const DashboardPane = lazy(() => import("./modes/dashboard"));


// AnalyticsPane moved to ./modes/analytics/index.tsx (B2).
const AnalyticsPane = lazy(() => import("./modes/analytics"));

// MetricCard, GaugeRow, InfoRow, Card, StatRow, ActionBtn, NavBtn moved
// to ./components/widgets.tsx (B2). Consumed by per-mode files directly now.

// ─── Logs Pane ────────────────────────────────────────────────────────────────

// LogsPane moved to ./modes/logs/index.tsx (B2).
const LogsPane = lazy(() => import("./modes/logs"));

// SettingsView (+ ThemePicker) moved to ./modes/settings (B2).
const SettingsView = lazy(() => import("./modes/settings"));

// TheaterPane moved to ./modes/theater/index.tsx (B2).
const TheaterPane = lazy(() => import("./modes/theater"));

// AppsPane moved to ./modes/apps/index.tsx (B2).
const AppsPane = lazy(() => import("./modes/apps"));

const BotsPane = lazy(() => import("./modes/bots"));

// Security mode REMOVED 2026-05-15 per JARVIS-as-AI-assistant pivot.
// Agent-safety primitives (sandbox/audit/verifier/council/confirmations)
// stay; security monitoring is gone.

// ─── Brain Pane ────────────────────────────────────────────────────────────────
// BrainPane + BrainGraph viewer moved to ./modes/brain/index.tsx (B2).
const BrainPane = lazy(() => import("./modes/brain"));

// ─── Root-level components ────────────────────────────────────────────────────
// Sidebar, MoodRibbon, ToastContainer, GreetingPopup, ConfirmModal moved
// to ./components/ (B2.11). App.tsx now just wires them in.
import Sidebar from "./components/Sidebar";
import MoodRibbon from "./components/MoodRibbon";
import ToastContainer from "./components/ToastContainer";
import GreetingPopup from "./components/GreetingPopup";
import ConfirmModal from "./components/ConfirmModal";

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  // B2 — Apple Dark is the new default. Existing users keep whatever they
  // had saved in localStorage; the change only affects fresh installs.
  const [theme, setThemeState] = useState<Theme>(() => {
    const saved = localStorage.getItem("jarvis-theme") as Theme | null;
    return saved && saved in THEMES ? saved : "apple";
  });
  const t = THEMES[theme];
  const setTheme = (th: Theme) => { setThemeState(th); localStorage.setItem("jarvis-theme", th); };

  const [mode, setMode] = useState<AppMode>("welcome");
  const [messages, setMessages] = useState<Message[]>([{ id: uid(), role: "system", content: "JARVIS ONLINE · LOCAL CODING AGENT · LM STUDIO", ts: new Date() }]);
  const [input, setInput] = useState("");
  const [processing, setProcessing] = useState(false);
  const [sessionId] = useState(() => `s-${uid()}`);
  const [lm, setLm] = useState<LMStatus>({ connected: false, models: [], latency_ms: 0 });
  const [model, setModel] = useState("");
  const [currentProject, setCurrentProject] = useState<string>("default");
  const [confirmPending, setConfirmPending] = useState<ConfirmPending | null>(null);
  const [memStats, setMemStats] = useState<MemoryStats>({ file_chunks: 0, chat_turns: 0, project: "default" });
  const [greetingOpen, setGreetingOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 1C.2 — track the current turn_id (emitted by gateway as the first
  // SSE event) so reaction signals (stop, copy, dismissed) can attach.
  const turnIdRef = useRef<string | null>(null);

  const addToast = useCallback((message: string, type: Toast["type"] = "info") => {
    const id = uid();
    setToasts(p => [...p.slice(-4), { id, message, type }]);
    setTimeout(() => setToasts(p => p.filter(t => t.id !== id)), 6000);
  }, []);

  // Tauri hotkey listener
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen("jarvis://greet", () => setGreetingOpen(true)).then(fn => { unlisten = fn; });
    return () => { unlisten?.(); };
  }, []);

  // Notification SSE — in-app toasts + native Tauri notifications
  useEffect(() => {
    let es: EventSource | null = null;
    const notify = async (title: string, body: string) => {
      try {
        const { sendNotification } = await import("@tauri-apps/plugin-notification");
        await sendNotification({ title, body });
      } catch {}
    };
    try {
      es = new EventSource(`${BACKEND}/notifications/stream`);
      es.onmessage = (e) => {
        const d = JSON.parse(e.data);
        if (d.type === "drive_alert") { addToast(d.message, "warn"); notify("JARVIS", d.message); }
        if (d.type === "error")       { addToast(d.message, "error"); notify("JARVIS Alert", d.message); }
      };
    } catch {}
    return () => es?.close();
  }, [addToast]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const poll = useCallback(async () => {
    try {
      const r = await fetch(`${BACKEND}/health`, { signal: AbortSignal.timeout(4000) });
      if (r.ok) {
        const d = await r.json();
        const s: LMStatus = d.lm_studio;
        setLm(s);
        if (!model && s.models?.length) setModel(s.models[0]);
      } else setLm(p => ({ ...p, connected: false }));
    } catch { setLm(p => ({ ...p, connected: false })); }
    try {
      const mr = await fetch(`${BACKEND}/memory/stats`, { signal: AbortSignal.timeout(3000) });
      if (mr.ok) setMemStats(await mr.json());
    } catch {}
  }, [model]);

  useEffect(() => { poll(); const iv = setInterval(poll, POLL_MS); return () => clearInterval(iv); }, [poll]);

  // D10 — self-onboarding banner state. On new project, populate
  // `onboarding`; the banner above the chat pane offers Summarize /
  // Ingest / Dismiss buttons. onboardedRef makes the offer one-shot
  // per session per path even if the user toggles back and forth.
  const [onboarding, setOnboarding] = useState<OnboardingState | null>(null);
  const onboardedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const proj = currentProject;
    if (!proj || proj === "default") return;
    if (onboardedRef.current.has(proj)) return;
    onboardedRef.current.add(proj);
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${BACKEND}/onboarding/check`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: proj }),
        });
        if (cancelled) return;
        const d = await r.json();
        if (!d.ok || !d.is_new) return;
        setOnboarding({
          path:       d.path,
          fileCount:  d.file_count,
          markers:    d.markers || [],
          languages:  Object.entries(d.language_hist || {}) as Array<[string, number]>,
        });
      } catch { /* swallow */ }
    })();
    return () => { cancelled = true; };
  }, [currentProject]);

  const handleOnboardingSummarize = async () => {
    if (!onboarding || onboarding.summarizing) return;
    setOnboarding(o => o && { ...o, summarizing: true });
    try {
      const r = await fetch(`${BACKEND}/onboarding/summarize`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: onboarding.path }),
      });
      const d = await r.json();
      setOnboarding(o => o && {
        ...o, summarizing: false,
        summary: d.ok ? d.summary : `LM unavailable — ${d.error ?? "no summary"}`,
      });
      // Mark seen — the user engaged.
      fetch(`${BACKEND}/onboarding/seen`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: onboarding.path }),
      }).catch(() => {});
    } catch {
      setOnboarding(o => o && { ...o, summarizing: false,
        summary: "Request failed — backend offline?" });
    }
  };

  const handleOnboardingIngest = async () => {
    if (!onboarding || onboarding.ingesting) return;
    setOnboarding(o => o && { ...o, ingesting: true, ingestMsg: undefined });
    try {
      const r = await fetch(`${BACKEND}/brain/ingest/dir`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ directory: onboarding.path }),
      });
      const d = await r.json();
      const msg = d.ok
        ? `✓ ingested ${d.ingested_count ?? "?"} files (${d.skipped_count ?? 0} skipped)`
        : `✗ ${d.error ?? "ingest failed"}`;
      setOnboarding(o => o && { ...o, ingesting: false, ingestMsg: msg });
      fetch(`${BACKEND}/onboarding/seen`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: onboarding.path }),
      }).catch(() => {});
    } catch (e) {
      setOnboarding(o => o && { ...o, ingesting: false,
        ingestMsg: `✗ ${e}` });
    }
  };

  const handleOnboardingDismiss = () => {
    if (!onboarding) return;
    const path = onboarding.path;
    setOnboarding(null);
    fetch(`${BACKEND}/onboarding/seen`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }).catch(() => {});
  };

  const push = (msg: Omit<Message, "id" | "ts">): string => {
    const m: Message = { ...msg, id: uid(), ts: new Date() };
    setMessages(p => [...p, m]);
    return m.id;
  };
  const patch = (id: string, updates: Partial<Message>) =>
    setMessages(p => p.map(m => (m.id === id ? { ...m, ...updates } : m)));

  const handleConfirm = async (approved: boolean) => {
    if (!confirmPending) return;
    const id = confirmPending.id;
    setConfirmPending(null);
    try { await fetch(`${BACKEND}/confirm/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved }) }); }
    catch {}
  };

  const doEvents = async (text: string) => {
    const msgId = push({ role: "assistant", content: "", toolCalls: [], isStreaming: true });
    abortRef.current = new AbortController();
    try {
      const r = await fetch(`${BACKEND}/chat/events`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId, model: model || undefined, project: currentProject !== "default" ? currentProject : undefined }),
        signal: abortRef.current.signal,
      });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === "turn_id") {
              turnIdRef.current = evt.turn_id;
            }
            if (evt.type === "tool_call") {
              setMessages(p => p.map(m => {
                if (m.id !== msgId) return m;
                const existing = m.toolCalls ?? [];
                if (existing.find(tc => tc.id === evt.id)) return m;
                return { ...m, toolCalls: [...existing, { id: evt.id, name: evt.name, args: evt.args, done: false }] };
              }));
            }
            if (evt.type === "tool_result") {
              setMessages(p => p.map(m => {
                if (m.id !== msgId) return m;
                return { ...m, toolCalls: (m.toolCalls ?? []).map(tc => tc.id === evt.id ? { ...tc, output: evt.output, done: true } : tc) };
              }));
            }
            if (evt.type === "confirm") setConfirmPending({ id: evt.id, tool: evt.tool, args: evt.args, tier: evt.tier, description: evt.description });
            if (evt.type === "text_delta") {
              setMessages(p => p.map(m => m.id === msgId
                ? { ...m, content: (m.content ?? "") + (evt.content ?? "") }
                : m));
            }
            if (evt.type === "text") patch(msgId, { content: evt.content, isStreaming: false });
            if (evt.type === "error") { patch(msgId, { content: `**[ERROR]** ${evt.message}`, isStreaming: false }); addToast(evt.message, "error"); }
            if (evt.type === "done") patch(msgId, { isStreaming: false });
          } catch {}
        }
      }
      patch(msgId, { isStreaming: false });
      // Save session
      fetch(`${BACKEND}/sessions/${sessionId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ last_message: text, message_count: messages.length + 2 }) }).catch(() => {});
    } catch (e: unknown) {
      const err = e as Error;
      if (err.name === "AbortError") patch(msgId, { content: "_(stopped)_", isStreaming: false });
      else patch(msgId, { content: "**[BACKEND OFFLINE]**\n\n```bash\ncd ~/jarvis\nvenv/bin/python3 main.py\n```", isStreaming: false });
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || processing) return;
    setInput(""); setProcessing(true);
    push({ role: "user", content: text });
    await doEvents(text);
    setProcessing(false);
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const clearChat = async () => {
    setMessages([{ id: uid(), role: "system", content: "SESSION CLEARED · READY", ts: new Date() }]);
    try { await fetch(`${BACKEND}/session/${sessionId}`, { method: "DELETE" }); } catch {}
  };

  const showSidebar = mode !== "welcome";
  const motionPreset = useMotionPreset();

  const renderMode = () => {
    switch (mode) {
      case "welcome":
        return (
          <WelcomeScreen lm={lm} memStats={memStats}
            onNav={m => setMode(m)}
            onQuickAsk={text => { setMode("chat"); setProcessing(true); push({ role: "user", content: text }); doEvents(text).finally(() => setProcessing(false)); }}
          />
        );
      case "chat":
        return (
          <>
            {onboarding && (
              <OnboardingBanner
                state={onboarding}
                onSummarize={handleOnboardingSummarize}
                onIngest={handleOnboardingIngest}
                onDismiss={handleOnboardingDismiss}
              />
            )}
            <ChatPane
              messages={messages} endRef={endRef}
              input={input} setInput={setInput}
              onSend={send} onStop={() => {
                // 1C.2 — record the stop signal *before* aborting so
                // the learner sees it. Fire-and-forget; failure is
                // fine (the backend may already be down).
                const tid = turnIdRef.current;
                if (tid) {
                  fetch(`${BACKEND}/feedback/turn/${tid}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ kind: "stop" }),
                  }).catch(() => {});
                }
                abortRef.current?.abort();
              }}
              processing={processing} inputRef={inputRef}
              lm={lm} model={model}
              currentProject={currentProject} onProjectChange={setCurrentProject}
            />
          </>
        );
      case "terminal":  return <TerminalPane wsUrl={`${WS_BACKEND}/ws/terminal`} />;
      case "coder":     return <CoderPane onDirChange={setCurrentProject} />;
      case "dashboard": return <DashboardPane lm={lm} processing={processing} memStats={memStats} />;
      case "analytics": return <AnalyticsPane />;
      case "logs":      return <LogsPane />;
      case "apps":      return <AppsPane />;
      case "bots":      return <BotsPane />;
      case "brain":     return <BrainPane />;
      case "theater":   return <TheaterPane />;
      case "settings":  return <SettingsView lm={lm} model={model} setModel={setModel} memStats={memStats} onRefresh={poll} />;
      default: return null;
    }
  };

  return (
    <ThemeCtx.Provider value={{ theme, t, setTheme }}>
      <div className="flex h-screen bg-[var(--bg)] text-[var(--text)] overflow-hidden">
        {showSidebar && (
          <Sidebar mode={mode} setMode={setMode} lm={lm} model={model} onClear={clearChat} onRefresh={poll} />
        )}

        <div className="flex-1 flex flex-col min-w-0">
          <LMBlockedBanner lm={lm} />
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={mode}
              variants={motionPreset.fadeUp}
              initial="hidden"
              animate="visible"
              exit="hidden"
              className="flex-1 flex flex-col min-h-0"
            >
              <Suspense fallback={
                <div className="flex-1 flex items-center justify-center text-[var(--text-subtle)] text-sm">
                  Loading…
                </div>
              }>
                {renderMode()}
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </div>

        <MoodRibbon />
        <AnimatePresence>
          {confirmPending && <ConfirmModal key="confirm" req={confirmPending} onAllow={() => handleConfirm(true)} onDeny={() => handleConfirm(false)} />}
          {greetingOpen && (
            <GreetingPopup
              key="greet"
              onSubmit={text => { setMode("chat"); setProcessing(true); push({ role: "user", content: text }); doEvents(text).finally(() => setProcessing(false)); }}
              onClose={() => setGreetingOpen(false)}
            />
          )}
        </AnimatePresence>
        <ToastContainer toasts={toasts} dismiss={id => setToasts(p => p.filter(t => t.id !== id))} />
      </div>
    </ThemeCtx.Provider>
  );
}

// AirGapBanner REMOVED 2026-05-15 (security pivot — air-gap mode was
// part of the security suite, not an assistant guardrail).






// TerminalPane moved to ./modes/terminal/index.tsx (B2).
const TerminalPane = lazy(() => import("./modes/terminal"));

// CoderPane (+ CodeEditor + FileTreeNode) moved to ./modes/coder/index.tsx (B2).
const CoderPane = lazy(() => import("./modes/coder"));



// ─── Shared components ────────────────────────────────────────────────────────

// PaneHeader moved to ./components/PaneHeader.tsx (B2). Imported per-mode.









