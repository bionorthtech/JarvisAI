/**
 * B2 — Coder mode.
 *
 * Three columns: file tree (lazy-expanded), CodeMirror editor, and a
 * side-chat that posts to /chat/events with the current file as
 * context. Save commits via /fs/write.
 *
 * Bundles CodeEditor + FileTreeNode + CoderPane in one file — they
 * compose tightly and aren't reused elsewhere.
 */
import { useEffect, useRef, useState } from "react";
import {
  FolderOpen, FileText, ChevronDown, ChevronRight, Code2, Bot,
  Save, SplitSquareHorizontal, Send, RefreshCw,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { EditorView, basicSetup } from "codemirror";
import { EditorState } from "@codemirror/state";
import { oneDark } from "@codemirror/theme-one-dark";
import { fimInline } from "./fim_inline";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { rust } from "@codemirror/lang-rust";
import { css as cssLang } from "@codemirror/lang-css";
import { html as htmlLang } from "@codemirror/lang-html";
import { json as jsonLang } from "@codemirror/lang-json";
import { markdown as mdLang } from "@codemirror/lang-markdown";

import type { FsEntry } from "../../types";
import { BACKEND } from "../../config";
import { useTheme } from "../../hooks/useTheme";

interface OpenFile { path: string; content: string }
interface CoderMsg { role: "user" | "assistant"; text: string }

const CM_EXT_LANG: Record<string, string> = {
  py: "python", pyw: "python",
  js: "javascript", mjs: "javascript", cjs: "javascript",
  ts: "typescript", tsx: "tsx", jsx: "jsx",
  rs: "rust",
  css: "css", scss: "css",
  html: "html", htm: "html",
  json: "json", jsonc: "json",
  md: "markdown", mdx: "markdown",
};

function CodeEditor({
  content, ext, onChange, viewRef,
}: {
  content: string; ext: string; onChange: (v: string) => void;
  viewRef?: { current: EditorView | null };
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const lang = CM_EXT_LANG[ext] ?? "";
    const langExt =
      lang === "python"     ? python() :
      lang === "typescript" ? javascript({ typescript: true }) :
      lang === "tsx"        ? javascript({ typescript: true, jsx: true }) :
      lang === "jsx"        ? javascript({ jsx: true }) :
      lang === "javascript" ? javascript() :
      lang === "rust"       ? rust() :
      lang === "css"        ? cssLang() :
      lang === "html"       ? htmlLang() :
      lang === "json"       ? jsonLang() :
      lang === "markdown"   ? mdLang() :
      [];
    const exts = [
      basicSetup,
      oneDark,
      EditorView.theme({
        "&":            { height: "100%", fontSize: "12px", fontFamily: "JetBrains Mono, Fira Code, monospace" },
        ".cm-scroller": { overflow: "auto" },
        ".cm-content":  { caretColor: "#fff" },
      }),
      EditorView.updateListener.of(u => { if (u.docChanged) onChange(u.state.doc.toString()); }),
      ...(Array.isArray(langExt) ? langExt : [langExt]),
      fimInline(BACKEND),
    ];
    const view = new EditorView({
      state: EditorState.create({ doc: content, extensions: exts }),
      parent: ref.current,
    });
    if (viewRef) viewRef.current = view;
    return () => {
      if (viewRef && viewRef.current === view) viewRef.current = null;
      view.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // keyed by file path — remounts on file change

  return <div ref={ref} className="flex-1 overflow-hidden" style={{ minHeight: 0 }} />;
}

function FileTreeNode({ path, name, isDir, depth, onOpen, treeVersion }: { path: string; name: string; isDir: boolean; depth: number; onOpen: (path: string, isDir: boolean) => void; treeVersion: number }) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<FsEntry[]>([]);
  const [childPath, setChildPath] = useState<string>("");
  const [loading, setLoading] = useState(false);

  const loadChildren = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${BACKEND}/fs/ls?path=${encodeURIComponent(path)}`);
      const d = await r.json();
      if (d.entries) { setChildren(d.entries); setChildPath(d.path ?? path); }
    } catch { /* ignore */ }
    setLoading(false);
  };

  // Re-fetch open subdirs when the tree is refreshed.
  useEffect(() => {
    if (isDir && expanded && treeVersion > 0) loadChildren();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeVersion]);

  const handleClick = async () => {
    if (!isDir) { onOpen(path, false); return; }
    if (!expanded && children.length === 0 && !loading) await loadChildren();
    setExpanded(v => !v);
  };
  return (
    <div>
      <button
        onClick={handleClick}
        className={`w-full flex items-center gap-1.5 py-1 text-left rounded hover:bg-white/[0.03] transition-colors ${
          name.startsWith(".") ? "opacity-40" : ""
        }`}
        style={{ paddingLeft: `${depth * 12 + 10}px`, paddingRight: "8px" }}
      >
        <span className="shrink-0 text-[var(--text-subtle)]">
          {isDir
            ? (expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />)
            : <FileText size={10} />}
        </span>
        <span className={`text-[11px] truncate font-mono ${isDir ? "text-[var(--text-muted)]" : "text-[var(--text-muted)] hover:text-[var(--text)]"}`}>
          {name}
        </span>
        {loading && <span className="text-[9px] text-[var(--text-subtle)] ml-auto animate-pulse">…</span>}
      </button>
      {expanded && !loading && children.length > 0 && (
        <div>
          {children.map(child => (
            <FileTreeNode
              key={child.name}
              path={`${childPath}/${child.name}`}
              name={child.name}
              isDir={child.type === "dir"}
              depth={depth + 1}
              onOpen={onOpen}
              treeVersion={treeVersion}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function CoderPane({ onDirChange }: { onDirChange?: (dir: string) => void }) {
  const { t } = useTheme();
  const [rootPath, setRootPath]           = useState("");
  const [rootEntries, setRootEntries]     = useState<FsEntry[]>([]);
  const [activeFile, setActiveFile]       = useState<OpenFile | null>(null);
  const [editorContent, setEditorContent] = useState("");
  const [dirty, setDirty]                 = useState(false);
  const [saving, setSaving]               = useState(false);
  const [msgs, setMsgs]                   = useState<CoderMsg[]>([]);
  const [ask, setAsk]                     = useState("");
  const [busy, setBusy]                   = useState(false);
  const [treeVersion, setTreeVersion]     = useState(0);
  const [refreshing, setRefreshing]       = useState(false);
  const msgsEndRef = useRef<HTMLDivElement>(null);

  // C15.1 — FIM completion
  const viewRef = useRef<EditorView | null>(null);
  const [completing, setCompleting] = useState(false);
  const [completeMsg, setCompleteMsg] = useState<string | null>(null);
  const handleComplete = async () => {
    if (!viewRef.current || completing) return;
    setCompleting(true);
    setCompleteMsg(null);
    const view = viewRef.current;
    const cursor = view.state.selection.main.head;
    const doc = view.state.doc.toString();
    const prefix = doc.slice(0, cursor);
    const suffix = doc.slice(cursor);
    try {
      const r = await fetch(`${BACKEND}/coder/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prefix, suffix, max_tokens: 80 }),
      });
      const d = await r.json();
      if (!d.ok) {
        setCompleteMsg(`✗ ${d.error ?? "completion failed"}`);
      } else if (!d.completion) {
        setCompleteMsg("✗ empty completion");
      } else {
        const insert = d.completion as string;
        // Re-fetch the live view in case the user moved during the call.
        const live = viewRef.current;
        if (live) {
          const pos = live.state.selection.main.head;
          live.dispatch({
            changes: { from: pos, to: pos, insert },
            selection: { anchor: pos + insert.length },
          });
          setCompleteMsg(`✓ ${insert.length} chars · ${Math.round(d.latency_ms ?? 0)}ms`);
        }
      }
    } catch (e) {
      setCompleteMsg(`✗ ${e}`);
    } finally {
      setCompleting(false);
    }
  };

  const loadRoot = async (target?: string) => {
    try {
      const r = await fetch(`${BACKEND}/fs/ls?path=${encodeURIComponent(target ?? rootPath ?? "~")}`);
      const d = await r.json();
      if (d.entries) {
        setRootEntries(prev => {
          const same = prev.length === d.entries.length &&
            prev.every((e, i) => e.name === d.entries[i].name && e.type === d.entries[i].type);
          return same ? prev : d.entries;
        });
        const p = d.path ?? target ?? "~";
        setRootPath(p);
        onDirChange?.(p);
      }
    } catch { /* ignore */ }
  };

  useEffect(() => { loadRoot("~"); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [onDirChange]);

  // Live-tracking: re-poll root every 8s so external file changes show up.
  useEffect(() => {
    const id = setInterval(() => { loadRoot(); }, 8000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rootPath]);

  const handleRefreshTree = async () => {
    if (refreshing) return;
    setRefreshing(true);
    await loadRoot();
    setTreeVersion(v => v + 1);
    setRefreshing(false);
  };

  useEffect(() => { msgsEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const handleOpen = async (path: string, isDir: boolean) => {
    if (isDir) return;
    try {
      const r = await fetch(`${BACKEND}/fs/cat?path=${encodeURIComponent(path)}`);
      const d = await r.json();
      if (d.content !== undefined) {
        setActiveFile({ path: d.path ?? path, content: d.content });
        setEditorContent(d.content);
        setDirty(false);
      }
    } catch { /* ignore */ }
  };

  const handleSave = async () => {
    if (!activeFile || !dirty) return;
    setSaving(true);
    try {
      await fetch(`${BACKEND}/fs/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: activeFile.path, content: editorContent }),
      });
      setDirty(false);
    } catch { /* ignore */ }
    setSaving(false);
  };

  const handleAsk = async (text: string) => {
    if (!text.trim() || busy) return;
    setBusy(true);
    const ctx = activeFile
      ? (() => {
          const lines = editorContent.split("\n");
          const preview = lines.slice(0, 200).join("\n");
          const truncNote = lines.length > 200
            ? `\n(showing first 200/${lines.length} lines — call read_file for the rest)`
            : "";
          return (
            `[File open in Coder: ${activeFile.path}]\n` +
            `\`\`\`\n${preview}\n\`\`\`${truncNote}\n\n` +
            `You have read_file / write_file tools. Use write_file when ` +
            `the user asks for an edit; do not just paste the new version.\n\n`
          );
        })()
      : "";
    setMsgs(p => [...p, { role: "user", text }]);
    setMsgs(p => [...p, { role: "assistant", text: "…" }]);

    let reply = "";
    try {
      const resp = await fetch(`${BACKEND}/chat/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: ctx + text, session_id: "coder-ai" }),
      });
      const reader = resp.body?.getReader();
      if (reader) {
        const dec = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const line of dec.decode(value).split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              const ev = JSON.parse(line.slice(6));
              if (ev.type === "text_delta") {
                reply += ev.content;
                setMsgs(p => [...p.slice(0, -1), { role: "assistant", text: reply }]);
              }
              if (ev.type === "done" || ev.type === "text_done") {
                setMsgs(p => [...p.slice(0, -1), { role: "assistant", text: reply || "Done." }]);
              }
            } catch { /* skip malformed */ }
          }
        }
      }
    } catch { /* ignore */ }
    if (!reply) setMsgs(p => [...p.slice(0, -1), { role: "assistant", text: "Error — check backend." }]);
    setBusy(false);
  };

  const ext = activeFile?.path.split(".").pop()?.toLowerCase() ?? "";
  const langLabel = CM_EXT_LANG[ext] ?? ext ?? "text";

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* File tree */}
      <div className="w-52 shrink-0 border-r border-white/[0.04] flex flex-col">
        <div className="shrink-0 flex items-center gap-2 px-3 py-2.5 border-b border-white/[0.04]">
          <FolderOpen size={12} className={t.accent} />
          <span className="text-[12px] text-[var(--text-subtle)] truncate flex-1">{rootPath || "~"}</span>
          <button
            onClick={handleRefreshTree}
            disabled={refreshing}
            title="Refresh file tree"
            className={`shrink-0 ${refreshing ? "opacity-40" : "hover:text-[var(--text)] text-[var(--text-subtle)]"}`}
          >
            <RefreshCw size={10} className={refreshing ? "animate-spin" : ""} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {rootEntries.map(e => (
            <FileTreeNode
              key={e.name}
              path={`${rootPath}/${e.name}`}
              name={e.name}
              isDir={e.type === "dir"}
              depth={0}
              onOpen={handleOpen}
              treeVersion={treeVersion}
            />
          ))}
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 flex flex-col min-w-0 border-r border-white/[0.04]">
        <div className="shrink-0 flex items-center gap-2 px-3 py-1.5 border-b border-white/[0.04] bg-[var(--surface-2)]">
          <SplitSquareHorizontal size={11} className="text-[var(--text-subtle)] shrink-0" />
          <span className="text-[11px] font-mono text-[var(--text-muted)] flex-1 truncate">
            {activeFile?.path ?? "← open a file"}
          </span>
          {activeFile && <span className="text-[9px] text-[var(--text-subtle)] uppercase shrink-0">{langLabel}</span>}
          {dirty && <span className="text-[9px] text-amber-500 shrink-0">● unsaved</span>}
          {completeMsg && (
            <span className={`text-[9px] shrink-0 truncate max-w-[180px] ${
              completeMsg.startsWith("✓") ? "text-emerald-400" : "text-red-400"
            }`} title={completeMsg}>
              {completeMsg}
            </span>
          )}
          <button
            onClick={handleComplete}
            disabled={!activeFile || completing}
            title="FIM complete at cursor (C15.1)"
            className={`shrink-0 flex items-center gap-1 px-2 py-1 rounded text-[10px] transition-colors ${
              activeFile && !completing
                ? `${t.btnBg} ${t.btnHoverBg} ${t.accent}`
                : "opacity-30 text-[var(--text-subtle)] cursor-not-allowed"
            }`}
          >
            {completing ? "completing…" : "complete"}
          </button>
          <button
            onClick={handleSave}
            disabled={!dirty || saving}
            className={`shrink-0 flex items-center gap-1 px-2 py-1 rounded text-[10px] transition-colors ${
              dirty ? `${t.btnBg} ${t.btnHoverBg} ${t.accent}` : "opacity-30 text-[var(--text-subtle)] cursor-not-allowed"
            }`}
          >
            <Save size={10} />{saving ? "saving…" : "save"}
          </button>
        </div>

        {activeFile ? (
          <CodeEditor
            key={activeFile.path}
            content={editorContent}
            ext={ext}
            onChange={v => { setEditorContent(v); setDirty(true); }}
            viewRef={viewRef}
          />
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-3">
            <Code2 size={28} className="text-[var(--text-faint)]" />
            <p className="text-[11px] text-[var(--text-subtle)]">Select a file from the tree to edit</p>
          </div>
        )}
      </div>

      {/* AI chat */}
      <div className="w-80 shrink-0 flex flex-col">
        <div className="shrink-0 flex items-center gap-2 px-3 py-2.5 border-b border-white/[0.04]">
          <Bot size={12} className={t.accent} />
          <span className="text-[12px] text-[var(--text-subtle)]">JARVIS — Code AI</span>
          {busy && <span className={`text-[9px] ${t.accentDim} animate-pulse ml-auto`}>thinking…</span>}
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          {msgs.length === 0 && (
            <p className="text-[11px] text-[var(--text-subtle)] mt-4 text-center">
              Open a file and ask JARVIS to explain, refactor, or debug it.
            </p>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={m.role === "user" ? `${t.accentBg} ${t.accentBorder} border rounded-lg px-3 py-2` : "px-1"}>
              {m.role === "user"
                ? <p className={`text-[11px] ${t.accent}`}>{m.text}</p>
                : <div className="text-[11px] text-[var(--text-muted)] leading-relaxed"><ReactMarkdown>{m.text}</ReactMarkdown></div>}
            </div>
          ))}
          <div ref={msgsEndRef} />
        </div>

        <div className={`shrink-0 border-t border-white/[0.06] flex items-center gap-2 px-3 py-2 ${t.inputFocus}`}>
          <input
            value={ask}
            onChange={e => setAsk(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && ask.trim()) { handleAsk(ask.trim()); setAsk(""); } }}
            placeholder={activeFile ? "Ask about this file…" : "Open a file first…"}
            disabled={busy}
            className="flex-1 bg-transparent text-[11px] font-mono text-[var(--text)] placeholder:text-[var(--text-subtle)] outline-none"
          />
          <button
            onClick={() => { if (ask.trim()) { handleAsk(ask.trim()); setAsk(""); } }}
            disabled={busy || !ask.trim()}
          >
            <Send size={11} className={ask.trim() && !busy ? t.accent : "text-[var(--text-subtle)]"} />
          </button>
        </div>
      </div>
    </div>
  );
}
