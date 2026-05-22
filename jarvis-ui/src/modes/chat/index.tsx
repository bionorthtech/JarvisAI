/**
 * B2 — Chat mode.
 *
 * Bundles every chat-only surface: ChatPane (top-level renderer) +
 * TopBar (model chip / project pill / LM status) + LMProgressStrip
 * (G6.3 live phase strip) + Bubble (message renderer) + ToolCard
 * (collapsed/expanded tool-call viewer) + StreamDots (placeholder
 * while a reply is still loading) + InputDock (prompt input + send/
 * stop). All chat state lives in <App /> and is passed in as props
 * — this file is purely presentational + LM progress SSE.
 */
import React, { useEffect, useRef, useState } from "react";
import {
  Bot, FolderOpen, Send, X, ChevronDown, ChevronRight,
  FileText, Play, Brain, Zap,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { motion } from "framer-motion";

import { BACKEND } from "../../config";
import type { LMStatus, Message, ToolCallEvent } from "../../types";
import { useTheme } from "../../hooks/useTheme";
import { fmtTime } from "../../utils/format";
import { easeStandard, durBase } from "../../utils/motion";

// ─── Tool-call helpers ────────────────────────────────────────────────────────

function toolIcon(name: string) {
  if (name === "read_file") return <FileText size={11} />;
  if (name === "write_file") return <FileText size={11} className="text-amber-400" />;
  if (name === "list_directory") return <FolderOpen size={11} />;
  if (name === "run_shell") return <Play size={11} />;
  if (name.startsWith("brain_")) return <Brain size={11} />;
  return <Zap size={11} />;
}
function toolLabel(name: string, args: Record<string, unknown>): string {
  if (name === "read_file") return `read  ${args.path ?? ""}`;
  if (name === "write_file") return `write ${args.path ?? ""}`;
  if (name === "list_directory") return `ls    ${args.path ?? ""}`;
  if (name === "run_shell") return `$ ${args.command ?? ""}`;
  return name;
}

// ─── LM Studio progress strip (G6.3) ──────────────────────────────────────────

type LMProgressEvent = {
  phase: "prompt_processing" | "thinking_start" | "thinking_done" | "request_complete";
  percent?: number;
  seconds?: number;
  n_tokens?: number;
  truncated?: boolean;
};

function LMProgressStrip({ active }: { active: boolean }) {
  const { t } = useTheme();
  const [phase, setPhase] = useState<string>("");
  const [pct, setPct] = useState<number | null>(null);
  const [reasonSec, setReasonSec] = useState<number | null>(null);
  const [thinkingT0, setThinkingT0] = useState<number | null>(null);
  const [, force] = useState(0);

  // Tick while in 'thinking' so the elapsed clock updates
  useEffect(() => {
    if (phase !== "thinking_start") return;
    const id = setInterval(() => force(n => n + 1), 250);
    return () => clearInterval(id);
  }, [phase]);

  useEffect(() => {
    if (!active) {
      setPhase(""); setPct(null); setReasonSec(null); setThinkingT0(null);
      return;
    }
    const es = new EventSource(`${BACKEND}/lm/progress/stream`);
    es.onmessage = (e) => {
      try {
        const ev: LMProgressEvent = JSON.parse(e.data);
        if (ev.phase === "prompt_processing") {
          setPhase("prompt_processing");
          setPct(ev.percent ?? null);
        } else if (ev.phase === "thinking_start") {
          setPhase("thinking_start");
          setPct(100);
          setThinkingT0(Date.now());
        } else if (ev.phase === "thinking_done") {
          setPhase("thinking_done");
          setReasonSec(ev.seconds ?? null);
          setThinkingT0(null);
        } else if (ev.phase === "request_complete") {
          setPhase("request_complete");
        }
      } catch { /* ignore */ }
    };
    es.onerror = () => { /* keep alive — backend may flap */ };
    return () => es.close();
  }, [active]);

  if (!active) return null;

  const liveReason = thinkingT0 ? ((Date.now() - thinkingT0) / 1000).toFixed(1) : null;

  return (
    <span className="flex items-center gap-2 text-[9px] font-mono">
      {phase === "prompt_processing" && pct !== null && (
        <span className={t.accentDim}>prompt {pct.toFixed(0)}%</span>
      )}
      {phase === "thinking_start" && liveReason && (
        <span className={`${t.accent} animate-pulse`}>reasoning {liveReason}s</span>
      )}
      {phase === "thinking_done" && reasonSec !== null && (
        <span className={t.accentDim}>reasoned {reasonSec.toFixed(1)}s · generating…</span>
      )}
      {phase === "request_complete" && (
        <span className="text-[var(--text-subtle)]">done</span>
      )}
      {!phase && (
        <span className={`${t.accentDim} animate-pulse`}>thinking…</span>
      )}
    </span>
  );
}

// ─── Top Bar ──────────────────────────────────────────────────────────────────

function TopBar({ lm, model, processing, project, onProjectChange }: {
  lm: LMStatus; model: string; processing: boolean;
  project: string; onProjectChange: (p: string) => void;
}) {
  const { t } = useTheme();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(project);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setDraft(project); }, [project]);
  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const commit = () => {
    const v = draft.trim() || "default";
    onProjectChange(v);
    setEditing(false);
  };

  return (
    <div className="shrink-0 flex items-center gap-3 px-5 py-2.5 bg-[var(--surface-2)]">
      <Bot size={13} className={`${t.accent} shrink-0`} />
      <span className="text-[11px] text-[var(--text-muted)]">{model || "no model loaded"}</span>
      <LMProgressStrip active={processing} />

      {/* Project indicator */}
      <div className="flex items-center gap-1 ml-3 border border-white/[0.06] rounded-md px-2 py-0.5 cursor-pointer hover:border-white/10"
           title="Click to change project/cwd for context injection"
           onClick={() => !editing && setEditing(true)}>
        <FolderOpen size={9} className="text-[var(--text-subtle)]" />
        {editing ? (
          <input
            ref={inputRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={e => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setDraft(project); setEditing(false); } }}
            className="bg-transparent text-[10px] text-[var(--text)] outline-none w-48 font-mono"
          />
        ) : (
          <span className="text-[10px] text-[var(--text-muted)] font-mono max-w-[180px] truncate">{project}</span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full ${lm.connected ? t.lmDot : "bg-red-500"}`} />
        <span className={`text-[10px] ${lm.connected ? t.accent : "text-red-400"}`}>
          {lm.connected ? `LM Studio · ${Math.round(lm.latency_ms)}ms` : "LM Studio Offline"}
        </span>
      </div>
    </div>
  );
}

// ─── StreamDots (in-flight reply placeholder) ────────────────────────────────

function StreamDots() {
  const { t } = useTheme();
  return (
    <span className="inline-flex items-center gap-0.5 ml-1">
      {[0, 150, 300].map(d => <span key={d} style={{ animationDelay: `${d}ms` }} className={`w-1 h-1 rounded-full animate-bounce ${t.statusDot}`} />)}
    </span>
  );
}

// ─── Tool Call Card ───────────────────────────────────────────────────────────

function ToolCard({ tc }: { tc: ToolCallEvent }) {
  const { t } = useTheme();
  const [expanded, setExpanded] = useState(false);
  const hasOutput = !!tc.output;
  const outputLines = tc.output ? tc.output.split("\n").length : 0;
  return (
    <div className={`rounded-lg border transition-all ${tc.done ? "bg-[var(--surface-1)] border-white/[0.04]" : `${t.accentBg} ${t.accentBorder} animate-pulse`}`}>
      <button onClick={() => hasOutput && setExpanded(v => !v)} className="w-full flex items-center gap-2 px-3 py-2 text-left">
        <span className={`shrink-0 ${tc.done ? "text-[var(--text-subtle)]" : t.accent}`}>{toolIcon(tc.name)}</span>
        <code className="text-[11px] text-[var(--text-muted)] flex-1 truncate font-mono">{toolLabel(tc.name, tc.args)}</code>
        {!tc.done && <span className={`text-[9px] ${t.accentDim} shrink-0 animate-pulse`}>running</span>}
        {tc.done && hasOutput && <span className="text-[9px] text-[var(--text-subtle)] shrink-0 flex items-center gap-1">{outputLines} lines {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}</span>}
      </button>
      {expanded && tc.output && (
        <div className="px-3 pb-3">
          <pre className="text-[10px] text-[var(--text-muted)] bg-[var(--surface-2)] border border-white/[0.03] rounded p-2 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap break-all">{tc.output}</pre>
        </div>
      )}
    </div>
  );
}

// ─── Message Bubble ───────────────────────────────────────────────────────────

function Bubble({ msg }: { msg: Message }) {
  const { t } = useTheme();
  const enter = { duration: durBase, ease: easeStandard };

  if (msg.role === "system") return (
    <motion.div
      className="flex items-center gap-3 my-1"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={enter}
    >
      <div className="h-px flex-1 bg-white/[0.03]" />
      <span className="text-[12px] text-[var(--text-subtle)]">{msg.content}</span>
      <div className="h-px flex-1 bg-white/[0.03]" />
    </motion.div>
  );
  if (msg.role === "user") return (
    <motion.div
      className="flex justify-end"
      initial={{ opacity: 0, x: 12, y: 8 }}
      animate={{ opacity: 1, x: 0, y: 0 }}
      transition={enter}
      layout
    >
      <div className="max-w-[72%]">
        <div className="text-[9px] text-[var(--text-subtle)] text-right mb-1 mr-1">YOU · {fmtTime(msg.ts)}</div>
        <div className={`${t.userBubbleBg} border ${t.userBubbleBorder} rounded-2xl rounded-tr-sm px-4 py-3 text-sm text-[var(--text)] leading-relaxed whitespace-pre-wrap`}>{msg.content}</div>
      </div>
    </motion.div>
  );
  return (
    <motion.div
      className="flex justify-start"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={enter}
      layout
    >
      <div className="max-w-[88%] w-full">
        <div className="flex items-center gap-1.5 text-[9px] text-[var(--text-subtle)] mb-1.5 ml-1">
          <span className={`${t.accentDim} font-bold`}>JARVIS</span><span>·</span><span>{fmtTime(msg.ts)}</span>
          {msg.isStreaming && !msg.content && (msg.toolCalls?.length ?? 0) === 0 && <StreamDots />}
        </div>
        {(msg.toolCalls?.length ?? 0) > 0 && (
          <div className="space-y-1.5 mb-3">{msg.toolCalls!.map(tc => <ToolCard key={tc.id} tc={tc} />)}</div>
        )}
        {(msg.content || msg.isStreaming) && (
          <div className="bg-[var(--surface-1)] rounded-2xl rounded-tl-sm px-4 py-3">
            <div className="prose prose-invert prose-sm max-w-none prose-p:text-slate-300 prose-p:leading-relaxed prose-p:my-1 prose-code:text-slate-200 prose-code:bg-black/40 prose-code:px-1 prose-code:rounded prose-code:text-xs prose-pre:bg-[#07070a] prose-pre:border prose-pre:border-white/[0.06] prose-pre:rounded-lg prose-pre:my-2 prose-headings:text-slate-100 prose-strong:text-slate-100 prose-li:text-slate-300 prose-li:my-0.5">
              <ReactMarkdown>{msg.content || (msg.isStreaming ? "▋" : "")}</ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </motion.div>
  );
}

// ─── Input Dock ───────────────────────────────────────────────────────────────

function InputDock({ input, setInput, onSend, onStop, processing, inputRef }: {
  input: string; setInput: (v: string) => void; onSend: () => void; onStop: () => void;
  processing: boolean; inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  const { t } = useTheme();
  return (
    <div className="shrink-0 px-5 pb-4 pt-2 border-t border-white/[0.04]">
      <div className={`flex items-center gap-2 bg-[var(--surface-1)] border rounded-xl px-4 py-1 transition-all ${processing ? t.inputProcessing : `border-white/[0.06] ${t.inputFocus}`}`}>
        <span className={`text-[9px] ${t.accentDim} opacity-40 shrink-0 select-none font-mono`}>jarvis:~$</span>
        <input ref={inputRef} value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); } }}
          placeholder={processing ? "Working..." : "Ask JARVIS anything, or give it a task..."}
          disabled={processing}
          className="flex-1 bg-transparent py-3 text-sm text-[var(--text)] placeholder:text-[var(--text-subtle)] outline-none" autoFocus />
        {processing ? (
          <button onClick={onStop} className="shrink-0 p-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 transition-colors"><X size={15} /></button>
        ) : (
          <button onClick={onSend} disabled={!input.trim()} className={`shrink-0 p-2 rounded-lg ${t.btnBg} ${t.btnHoverBg} ${t.accent} disabled:opacity-20 transition-colors`}><Send size={15} /></button>
        )}
      </div>
      <p className="text-[9px] text-[var(--text-faint)] mt-1 ml-1">Enter to send · Stop to interrupt · DANGER tools require confirmation</p>
    </div>
  );
}

// ─── ChatPane (top-level export) ──────────────────────────────────────────────

export default function ChatPane({
  messages, endRef,
  input, setInput,
  onSend, onStop, processing,
  inputRef,
  lm, model,
  currentProject, onProjectChange,
}: {
  messages: Message[];
  endRef: React.RefObject<HTMLDivElement | null>;
  input: string; setInput: (v: string) => void;
  onSend: () => void; onStop: () => void; processing: boolean;
  inputRef: React.RefObject<HTMLInputElement | null>;
  lm: LMStatus; model: string;
  currentProject: string; onProjectChange: (p: string) => void;
}) {
  return (
    <>
      <TopBar lm={lm} model={model} processing={processing}
              project={currentProject} onProjectChange={onProjectChange} />
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-6 py-5 space-y-4">
          {messages.map(m => <Bubble key={m.id} msg={m} />)}
          <div ref={endRef} />
        </div>
      </div>
      <div className="max-w-4xl w-full mx-auto">
        <InputDock input={input} setInput={setInput} onSend={onSend} onStop={onStop}
                   processing={processing} inputRef={inputRef} />
      </div>
    </>
  );
}
