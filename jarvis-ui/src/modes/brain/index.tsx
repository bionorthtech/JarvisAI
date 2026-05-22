/**
 * B2 — Brain (second-brain / vault) mode.
 *
 * Five tabs: overview (capture + today + ask RAG + inbox + ingest +
 * graph), vault (folder browser + note viewer/editor + backlinks +
 * similar + suggested wikilinks), search (full-text + semantic),
 * tasks (Tasks/ checklist toggling), learning (F4.2 track progress).
 *
 * Heavy file — owns the BrainGraph SVG force-directed viewer too,
 * which renders the knowledge graph entirely without external deps.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Brain, RefreshCw } from "lucide-react";

import { BACKEND } from "../../config";
import { useTheme } from "../../hooks/useTheme";
import { usePolling } from "../../hooks/usePolling";
import PaneHeader from "../../components/PaneHeader";
import { InfoRow, Card, Section } from "../../components/widgets";

// ─── Brain Pane types ─────────────────────────────────────────────────────────

type BrainStats = Record<string, number | string>;
type GraphStats = { notes: number; edges: number; avg_backlinks: number; isolated_count: number; isolated_sample: string[] };
type DailyNote  = { name: string; path: string };
type InboxItem  = { name: string; captured: string; source: string; status: string; preview: string };
type Source     = { index: number; name: string; score: number };
type AskResult  = { question: string; context: string; sources: Source[]; instruction: string };
type BrainNote  = { name: string; body: string; raw: string; wikilinks: string[]; frontmatter: Record<string, unknown> };
type SearchResult = { name: string; path: string; preview: string; score: number };
type BrainListItem = { name: string; path: string; size: number; modified: string };

export default function BrainPane() {
  const { t } = useTheme();
  const [brainTab, setBrainTab] = useState<"overview" | "vault" | "search" | "tasks" | "learning" | "skills">("overview");

  // C14.1 — skill library browser
  type SkillEntry = {
    slug: string; task_desc: string; agent_type: string;
    task_id: string | null; created_at: number; usage_count: number;
  };
  type SkillDetail = SkillEntry & { body: string; path: string };
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillSearch, setSkillSearch] = useState("");
  const [skillOpenSlug, setSkillOpenSlug] = useState<string | null>(null);
  const [skillOpen, setSkillOpen] = useState<SkillDetail | null>(null);
  const loadSkills = useCallback(async () => {
    setSkillsLoading(true);
    try {
      const r = await fetch(`${BACKEND}/skills?limit=100`);
      const d = await r.json();
      setSkills(d.skills ?? []);
    } catch { setSkills([]); }
    finally { setSkillsLoading(false); }
  }, []);
  const runSkillSearch = useCallback(async () => {
    const q = skillSearch.trim();
    if (!q) { loadSkills(); return; }
    setSkillsLoading(true);
    try {
      const r = await fetch(`${BACKEND}/skills/search?q=${encodeURIComponent(q)}&limit=20`);
      const d = await r.json();
      setSkills(d.hits ?? []);
    } catch { /* keep last */ }
    finally { setSkillsLoading(false); }
  }, [skillSearch, loadSkills]);
  const openSkill = useCallback(async (slug: string) => {
    if (skillOpenSlug === slug) {
      setSkillOpenSlug(null); setSkillOpen(null);
      return;
    }
    setSkillOpenSlug(slug);
    setSkillOpen(null);
    try {
      const r = await fetch(`${BACKEND}/skills/${encodeURIComponent(slug)}`);
      const d = await r.json();
      if (d.ok) setSkillOpen(d.skill);
    } catch { /* swallow */ }
  }, [skillOpenSlug]);
  useEffect(() => { if (brainTab === "skills") loadSkills(); }, [brainTab, loadSkills]);

  // ── Overview state ───────────────────────────────────────────────────
  const [vaultStats, setVaultStats] = useState<BrainStats | null>(null);
  const [graph, setGraph]           = useState<GraphStats | null>(null);
  const [today, setToday]           = useState<DailyNote | null>(null);
  const [todayBody, setTodayBody]   = useState<string>("");
  const [todayEditing, setTodayEditing] = useState(false);
  const [todayDraft, setTodayDraft] = useState("");
  const [todaySaving, setTodaySaving] = useState(false);
  const [inbox, setInbox]           = useState<InboxItem[]>([]);
  const [captureText, setCaptureText] = useState("");
  const [captureBusy, setCaptureBusy] = useState(false);
  const [ingestPath, setIngestPath] = useState("");
  const [ingestBusy, setIngestBusy] = useState(false);
  const [ingestMsg, setIngestMsg]   = useState<string>("");
  const [askQ, setAskQ]             = useState("");
  const [askBusy, setAskBusy]       = useState(false);
  const [askResult, setAskResult]   = useState<AskResult | null>(null);
  const [reindexBusy, setReindexBusy] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  // ── Vault state ──────────────────────────────────────────────────────
  const VAULT_FOLDERS = ["Daily", "Inbox", "Notes", "Sources", "Projects", "Tasks", "Operations", "Templates", "System"];
  const [vaultFolder, setVaultFolder] = useState("Notes");
  const [vaultList, setVaultList]     = useState<BrainListItem[]>([]);
  const [openNote, setOpenNote]       = useState<BrainNote | null>(null);
  const [openNoteName, setOpenNoteName] = useState("");
  const [noteEditing, setNoteEditing] = useState(false);
  const [noteDraft, setNoteDraft]     = useState("");
  const [noteSaving, setNoteSaving]   = useState(false);
  const [noteDeleting, setNoteDeleting] = useState(false);
  const [backlinks, setBacklinks]     = useState<string[]>([]);
  const [similar, setSimilar]         = useState<{ name: string; score: number }[]>([]);
  const [suggestLinks, setSuggestLinks] = useState<{ name: string; score: number }[]>([]);
  const [suggestBusy, setSuggestBusy] = useState(false);

  // ── Search state ─────────────────────────────────────────────────────
  const [searchQ, setSearchQ]       = useState("");
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);

  // ── Tasks state ──────────────────────────────────────────────────────
  const [taskNotes, setTaskNotes]   = useState<{ name: string; items: { text: string; done: boolean; line: number }[]; raw: string }[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);

  // ── Learning tracks state (F4.2) ─────────────────────────────────────
  type LearningTrack = {
    id: string; name: string; cadence_days: number; prereqs: string[];
    sources: string[]; topics_total: number; topics_done: number;
    current_topic: string | null; next_topic: string | null;
    status: "active" | "paused" | "dropped" | "done";
    last_advance_ts: number; progress_pct: number;
  };
  const [learningTracks, setLearningTracks] = useState<LearningTrack[]>([]);
  const [learningLoading, setLearningLoading] = useState(false);
  const [learningBusyId, setLearningBusyId] = useState<string | null>(null);

  // ── Loaders ─────────────────────────────────────────────────────────
  const loadAll = useCallback(() => {
    fetch(`${BACKEND}/brain/vault_stats`).then(r => r.json()).then(setVaultStats).catch(() => {});
    fetch(`${BACKEND}/brain/graph`).then(r => r.json()).then(setGraph).catch(() => {});
    fetch(`${BACKEND}/brain/today`).then(r => r.json()).then((d: DailyNote) => {
      setToday(d);
      fetch(`${BACKEND}/fs/cat?path=${encodeURIComponent(d.path)}`)
        .then(r => r.json()).then(b => { setTodayBody(b.content ?? ""); setTodayDraft(b.content ?? ""); }).catch(() => {});
    }).catch(() => {});
    fetch(`${BACKEND}/brain/inbox?limit=15`).then(r => r.json()).then(d => { setInbox(d.items ?? []); setLastRefreshed(new Date()); }).catch(() => {});
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);
  usePolling(loadAll, 120_000);

  const loadVaultFolder = useCallback((folder: string) => {
    setOpenNote(null); setOpenNoteName("");
    fetch(`${BACKEND}/brain/list?subdir=${encodeURIComponent(folder)}`)
      .then(r => r.json()).then(d => setVaultList(d.items ?? [])).catch(() => {});
  }, []);

  useEffect(() => { if (brainTab === "vault") loadVaultFolder(vaultFolder); }, [brainTab, vaultFolder, loadVaultFolder]);

  const openNoteByName = async (name: string) => {
    setOpenNoteName(name);
    setBacklinks([]); setSimilar([]); setSuggestLinks([]);
    try {
      const r = await fetch(`${BACKEND}/brain/note/${encodeURIComponent(name)}`);
      const d: BrainNote = await r.json();
      setOpenNote(d); setNoteDraft(d.raw); setNoteEditing(false);
      // Load backlinks + similar in parallel
      fetch(`${BACKEND}/brain/backlinks/${encodeURIComponent(name)}`).then(r => r.json()).then(d => setBacklinks(d.backlinks ?? [])).catch(() => {});
      fetch(`${BACKEND}/brain/similar/${encodeURIComponent(name)}?n=5`).then(r => r.json()).then(d => setSimilar(d.similar ?? [])).catch(() => {});
    } catch {}
  };

  const saveNote = async () => {
    if (!openNoteName) return;
    setNoteSaving(true);
    try {
      await fetch(`${BACKEND}/brain/update/${encodeURIComponent(openNoteName)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: noteDraft }),
      });
      setOpenNote(n => n ? { ...n, raw: noteDraft, body: noteDraft } : n);
      setNoteEditing(false);
    } finally { setNoteSaving(false); }
  };

  const deleteNote = async () => {
    if (!openNoteName || !confirm(`Delete ${openNoteName}?`)) return;
    setNoteDeleting(true);
    try {
      await fetch(`${BACKEND}/brain/note/${encodeURIComponent(openNoteName)}`, { method: "DELETE" });
      setOpenNote(null); setOpenNoteName("");
      loadVaultFolder(vaultFolder);
    } finally { setNoteDeleting(false); }
  };

  const suggestNoteLinks = async () => {
    if (!openNoteName || suggestBusy) return;
    setSuggestBusy(true);
    try {
      const r = await fetch(`${BACKEND}/brain/suggest_links/${encodeURIComponent(openNoteName)}?n=8`);
      const d = await r.json();
      setSuggestLinks(d.suggestions ?? []);
    } finally { setSuggestBusy(false); }
  };

  const applyLinks = async (links: string[]) => {
    if (!openNoteName || !links.length) return;
    await fetch(`${BACKEND}/brain/insert_links`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: openNoteName, links }),
    });
    openNoteByName(openNoteName);
    setSuggestLinks([]);
  };

  const saveTodayNote = async () => {
    if (!today) return;
    setTodaySaving(true);
    try {
      const noteName = `Daily/${today.name}`;
      await fetch(`${BACKEND}/brain/update/${encodeURIComponent(noteName)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: todayDraft }),
      });
      setTodayBody(todayDraft); setTodayEditing(false);
    } finally { setTodaySaving(false); }
  };

  // ── Search ───────────────────────────────────────────────────────────
  const doSearch = async () => {
    if (!searchQ.trim() || searchBusy) return;
    setSearchBusy(true); setSearchResults([]);
    try {
      const r = await fetch(`${BACKEND}/brain/search?q=${encodeURIComponent(searchQ)}`);
      const d = await r.json();
      setSearchResults(d.results ?? []);
    } finally { setSearchBusy(false); }
  };

  // ── Tasks ────────────────────────────────────────────────────────────
  const loadTasks = useCallback(async () => {
    setTasksLoading(true);
    try {
      const r = await fetch(`${BACKEND}/brain/list?subdir=Tasks`);
      const d = await r.json();
      const notes = await Promise.all((d.items ?? []).map(async (item: BrainListItem) => {
        const nr = await fetch(`${BACKEND}/brain/note/${encodeURIComponent(item.name)}`);
        const nd: BrainNote = await nr.json();
        const items = nd.body.split("\n").map((line, i) => {
          const m = line.match(/^(\s*-\s*\[)([ x])(\].*)/);
          if (!m) return null;
          return { text: m[3].replace(/^\]\s*/, ""), done: m[2] === "x", line: i };
        }).filter(Boolean) as { text: string; done: boolean; line: number }[];
        return { name: item.name, items, raw: nd.raw };
      }));
      setTaskNotes(notes);
    } finally { setTasksLoading(false); }
  }, []);

  useEffect(() => { if (brainTab === "tasks") loadTasks(); }, [brainTab, loadTasks]);

  // ── Learning tracks loaders (F4.2) ───────────────────────────────────
  const loadLearningTracks = useCallback(async () => {
    setLearningLoading(true);
    try {
      const r = await fetch(`${BACKEND}/learning/tracks`);
      const d = await r.json();
      setLearningTracks(d.tracks ?? []);
    } catch { /* ignore */ }
    finally { setLearningLoading(false); }
  }, []);

  useEffect(() => { if (brainTab === "learning") loadLearningTracks(); }, [brainTab, loadLearningTracks]);

  const completeLearningTopic = async (trackId: string) => {
    setLearningBusyId(trackId);
    try {
      await fetch(`${BACKEND}/learning/tracks/${encodeURIComponent(trackId)}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      await loadLearningTracks();
    } finally { setLearningBusyId(null); }
  };

  const setLearningStatus = async (trackId: string, status: "active" | "paused" | "dropped") => {
    setLearningBusyId(trackId);
    try {
      await fetch(`${BACKEND}/learning/tracks/${encodeURIComponent(trackId)}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await loadLearningTracks();
    } finally { setLearningBusyId(null); }
  };

  const toggleTask = async (noteIndex: number, lineIndex: number) => {
    const note = taskNotes[noteIndex];
    const lines = note.raw.split("\n");
    const li = note.items[lineIndex].line;
    lines[li] = lines[li].replace(/\[[ x]\]/, note.items[lineIndex].done ? "[ ]" : "[x]");
    const newRaw = lines.join("\n");
    await fetch(`${BACKEND}/brain/update/${encodeURIComponent(note.name)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: newRaw }),
    });
    setTaskNotes(notes => notes.map((n, i) => i !== noteIndex ? n : {
      ...n, raw: newRaw,
      items: n.items.map((it, j) => j !== lineIndex ? it : { ...it, done: !it.done }),
    }));
  };

  // ── Actions ─────────────────────────────────────────────────────────
  const doCapture = async () => {
    if (!captureText.trim() || captureBusy) return;
    setCaptureBusy(true);
    try {
      await fetch(`${BACKEND}/brain/capture`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: captureText, source: "ui" }),
      });
      setCaptureText(""); loadAll();
    } finally { setCaptureBusy(false); }
  };

  const doIngest = async () => {
    if (!ingestPath.trim() || ingestBusy) return;
    setIngestBusy(true); setIngestMsg("");
    try {
      const isDir = !ingestPath.startsWith("http") && !ingestPath.includes(".");
      const url = isDir ? "/brain/ingest/dir" : "/brain/ingest";
      const body = isDir ? { directory: ingestPath } : { path_or_url: ingestPath };
      const r = await fetch(`${BACKEND}${url}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      setIngestMsg(d.ok ? (d.ingested_count !== undefined ? `✓ ${d.ingested_count} files ingested, ${d.skipped_count} skipped` : `✓ ${d.note}`) : `✗ ${d.error ?? "failed"}`);
      setIngestPath(""); loadAll();
    } catch (e) { setIngestMsg(`✗ ${e}`); } finally { setIngestBusy(false); }
  };

  const doAsk = async () => {
    if (!askQ.trim() || askBusy) return;
    setAskBusy(true); setAskResult(null);
    try {
      const r = await fetch(`${BACKEND}/brain/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: askQ, n: 6 }) });
      setAskResult(await r.json());
    } finally { setAskBusy(false); }
  };

  const doReindex = async () => {
    if (reindexBusy) return;
    setReindexBusy(true);
    try { await fetch(`${BACKEND}/brain/reindex`, { method: "POST" }); loadAll(); } finally { setReindexBusy(false); }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header + tabs */}
      <div className="shrink-0 px-6 pt-5 pb-0">
        <PaneHeader icon={<Brain size={13} />} title="Brain" lastRefreshed={lastRefreshed} />
        <div className="flex gap-1 border-b border-white/[0.04] mt-3">
          {(["overview", "vault", "search", "tasks", "learning", "skills"] as const).map(tab => (
            <button key={tab} onClick={() => setBrainTab(tab)}
              className={`text-[10px] px-3 py-1 rounded-t-md uppercase tracking-widest transition-colors ${brainTab === tab ? `${t.accent} border-b-2 ${t.accentBorder.replace("border", "border-b")}` : "text-[var(--text-subtle)] hover:text-[var(--text-muted)]"}`}>
              {tab}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
       <div className={brainTab === "vault" ? "p-6 space-y-5" : "max-w-6xl mx-auto p-6 space-y-8"}>

      {/* ─── OVERVIEW TAB ──────────────────────────────────────────────── */}
      {brainTab === "overview" && (<>
        {/* Capture box */}
        <Card title="Capture">
          <textarea value={captureText} onChange={e => setCaptureText(e.target.value)}
            onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") doCapture(); }}
            placeholder="Drop anything here. Ideas, links, snippets. Lands in Inbox/ instantly. ⌘/Ctrl+Enter to send."
            className="w-full bg-[var(--surface-2)] border border-white/[0.04] rounded-lg p-3 text-[12px] text-[var(--text)] placeholder:text-[var(--text-subtle)] outline-none resize-none focus:border-white/10" rows={3} />
          <div className="flex items-center justify-end gap-2 mt-2">
            <span className="text-[10px] text-[var(--text-subtle)] mr-auto">{captureText.length} chars</span>
            <button onClick={doCapture} disabled={!captureText.trim() || captureBusy}
              className={`text-[11px] px-3 py-1.5 rounded-md ${t.accentBg} ${t.accentBgHover} ${t.accent} border ${t.accentBorder} disabled:opacity-40`}>
              {captureBusy ? "Capturing…" : "→ Inbox"}
            </button>
          </div>
        </Card>

        {/* Today + Ask */}
        <div className="grid grid-cols-2 gap-5">
          {/* Today — editable */}
          <Card
            title="Today"
            actions={
              <>
                <span className="text-[10px] text-[var(--text-subtle)] font-mono">{today?.name ?? ""}</span>
                <button onClick={() => { setTodayEditing(e => !e); setTodayDraft(todayBody); }}
                  className="text-[9px] text-[var(--text-subtle)] hover:text-[var(--text)] border border-white/[0.04] rounded px-1.5 py-0.5">
                  {todayEditing ? "cancel" : "edit"}
                </button>
              </>
            }
          >
            {todayEditing ? (
              <>
                <textarea value={todayDraft} onChange={e => setTodayDraft(e.target.value)}
                  className="w-full bg-[var(--surface-2)] border border-white/[0.04] rounded-lg p-3 text-[11px] text-[var(--text)] outline-none resize-none focus:border-white/10 font-mono" rows={10} />
                <button onClick={saveTodayNote} disabled={todaySaving}
                  className={`mt-2 text-[11px] px-3 py-1.5 rounded-md ${t.accentBg} ${t.accent} disabled:opacity-40`}>
                  {todaySaving ? "Saving…" : "Save"}
                </button>
              </>
            ) : (
              <pre className="text-[11px] text-[var(--text-muted)] whitespace-pre-wrap font-mono max-h-72 overflow-y-auto bg-[var(--surface-2)] rounded-lg p-3 border border-white/[0.03]">
                {todayBody || "(today's daily note is empty — capture some thoughts)"}
              </pre>
            )}
          </Card>

          {/* Ask the brain */}
          <Card title="Ask the Brain (RAG)">
            <div className="flex gap-2 mb-3">
              <input value={askQ} onChange={e => setAskQ(e.target.value)} onKeyDown={e => { if (e.key === "Enter") doAsk(); }}
                placeholder="What did I say about LUKS?" className="flex-1 bg-[var(--surface-2)] border border-white/[0.04] rounded-md px-3 py-1.5 text-[12px] outline-none focus:border-white/10" />
              <button onClick={doAsk} disabled={!askQ.trim() || askBusy}
                className={`text-[11px] px-3 py-1.5 rounded-md ${t.accentBg} ${t.accent} disabled:opacity-40`}>
                {askBusy ? "…" : "→"}
              </button>
            </div>
            {askResult && (
              <div className="space-y-2 max-h-60 overflow-y-auto">
                {askResult.sources.length > 0
                  ? askResult.sources.map(s => (
                    <div key={s.index} className="bg-[var(--surface-2)] border border-white/[0.03] rounded-md p-2 cursor-pointer hover:border-white/10"
                         onClick={() => { setBrainTab("vault"); setVaultFolder(s.name.split("/")[0]); openNoteByName(s.name); }}>
                      <div className="flex items-baseline gap-2 mb-1">
                        <span className={`text-[10px] font-mono ${t.accent}`}>#{s.index}</span>
                        <span className="text-[11px] text-[var(--text)] truncate flex-1">{s.name}</span>
                        <span className="text-[9px] text-[var(--text-subtle)] tabular-nums">{(s.score ?? 0).toFixed(2)}</span>
                      </div>
                    </div>))
                  : <p className="text-[11px] text-[var(--text-subtle)]">{askResult.context}</p>}
                <p className="text-[9px] text-[var(--text-subtle)] italic">Click a result to open in Vault. Send question to chat for synthesized answer.</p>
              </div>
            )}
          </Card>
        </div>

        {/* Inbox + Stats */}
        <div className="grid grid-cols-3 gap-5">
          <div className="col-span-2">
            <Card title={`Inbox (${inbox.length})`}>
              <div className="max-h-60 overflow-y-auto space-y-1">
                {inbox.length === 0 && <p className="text-[11px] text-[var(--text-subtle)]">(empty — capture something above)</p>}
                {inbox.map(item => (
                  <div key={item.name} className="bg-[var(--surface-2)] border border-white/[0.03] rounded-md p-2">
                    <div className="flex items-baseline gap-2 mb-0.5">
                      <span className="text-[9px] text-[var(--text-subtle)] font-mono shrink-0">{item.captured.replace("T"," ").replace(/^"|"$/g,"").slice(0,16)}</span>
                      <span className={`text-[8px] uppercase ${item.status === "processed" ? "text-emerald-500" : "text-amber-500"}`}>{item.status}</span>
                    </div>
                    <p className="text-[11px] text-[var(--text)] truncate">{item.preview}</p>
                  </div>
                ))}
              </div>
            </Card>
          </div>
          <Card title="Vault">
            {graph && <div className="space-y-1 mb-3">
              <InfoRow label="notes" val={String(graph.notes)} />
              <InfoRow label="edges" val={String(graph.edges)} />
              <InfoRow label="isolated" val={String(graph.isolated_count)} />
            </div>}
            {vaultStats && <div className="space-y-1">
              {["Daily","Inbox","Notes","Sources","Projects","Tasks"].map(k => <InfoRow key={k} label={k} val={String(vaultStats[k] ?? 0)} />)}
            </div>}
          </Card>
        </div>

        {/* Ingest */}
        <Card title="Ingest Source">
          <div className="flex gap-2">
            <input value={ingestPath} onChange={e => setIngestPath(e.target.value)} onKeyDown={e => { if (e.key === "Enter") doIngest(); }}
              placeholder="~/Documents  ·  /path/to/doc.pdf  ·  https://example.com/page"
              className="flex-1 bg-[var(--surface-2)] border border-white/[0.04] rounded-md px-3 py-1.5 text-[12px] outline-none focus:border-white/10 font-mono" />
            <button onClick={doIngest} disabled={!ingestPath.trim() || ingestBusy}
              className={`text-[11px] px-3 py-1.5 rounded-md ${t.accentBg} ${t.accent} disabled:opacity-40`}>
              {ingestBusy ? "Ingesting…" : "Ingest"}
            </button>
            <button onClick={doReindex} disabled={reindexBusy} title="Re-embed entire vault"
              className="text-[11px] px-3 py-1.5 rounded-md border border-white/[0.04] text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40">
              {reindexBusy ? "…" : <RefreshCw size={12} />}
            </button>
          </div>
          {ingestMsg && <p className={`text-[10px] mt-2 ${ingestMsg.startsWith("✓") ? "text-emerald-400" : "text-red-400"}`}>{ingestMsg}</p>}
        </Card>

        {/* Graph */}
        <BrainGraph />
      </>)}

      {/* ─── VAULT TAB ─────────────────────────────────────────────────── */}
      {brainTab === "vault" && (
        <div className="flex gap-4 min-h-[600px]">
          {/* Folder sidebar */}
          <div className="w-48 shrink-0 space-y-1">
            <h3 className="text-[12px] text-[var(--text-muted)] font-medium mb-3">Folders</h3>
            {VAULT_FOLDERS.map(f => (
              <button key={f} onClick={() => { setVaultFolder(f); loadVaultFolder(f); }}
                className={`w-full text-left px-2 py-1.5 rounded-md text-[11px] transition-colors ${vaultFolder === f ? `${t.accentBg} ${t.accent}` : "text-[var(--text-subtle)] hover:text-[var(--text)] hover:bg-white/[0.02]"}`}>
                {f}
              </button>
            ))}
          </div>

          {/* Note list */}
          <div className="w-52 shrink-0 border-x border-white/[0.04] px-3 space-y-0.5 overflow-y-auto">
            <h3 className="text-[12px] text-[var(--text-muted)] font-medium mb-3 sticky top-0 bg-[var(--surface-2)] py-1">{vaultFolder} ({vaultList.length})</h3>
            {vaultList.length === 0 && <p className="text-[11px] text-[var(--text-subtle)]">Empty</p>}
            {vaultList.map(item => (
              <button key={item.name} onClick={() => openNoteByName(item.name)}
                className={`w-full text-left px-2 py-1.5 rounded-md text-[11px] font-mono transition-colors truncate ${openNoteName === item.name ? `${t.accentBg} ${t.accent}` : "text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-white/[0.02]"}`}>
                {item.name.replace(vaultFolder + "/", "")}
              </button>
            ))}
          </div>

          {/* Note viewer/editor */}
          <div className="flex-1 min-w-0">
            {!openNote ? (
              <div className="text-[11px] text-[var(--text-subtle)] p-4">Select a note to read or edit.</div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <h3 className={`text-[12px] font-bold ${t.accent} font-mono flex-1 truncate`}>{openNote.name}</h3>
                  <button onClick={() => setNoteEditing(e => !e)}
                    className="text-[10px] px-2 py-0.5 border border-white/[0.06] rounded text-[var(--text-muted)] hover:text-[var(--text)]">
                    {noteEditing ? "cancel" : "edit"}
                  </button>
                  {noteEditing && (
                    <button onClick={saveNote} disabled={noteSaving}
                      className={`text-[10px] px-2 py-0.5 rounded ${t.accentBg} ${t.accent} disabled:opacity-40`}>
                      {noteSaving ? "…" : "save"}
                    </button>
                  )}
                  <button onClick={deleteNote} disabled={noteDeleting}
                    className="text-[10px] px-2 py-0.5 border border-red-500/20 rounded text-red-500/60 hover:text-red-400 disabled:opacity-40">
                    {noteDeleting ? "…" : "delete"}
                  </button>
                </div>
                {noteEditing ? (
                  <textarea value={noteDraft} onChange={e => setNoteDraft(e.target.value)}
                    className="w-full bg-[var(--surface-2)] border border-white/[0.04] rounded-lg p-3 text-[11px] text-[var(--text)] outline-none resize-none focus:border-white/10 font-mono"
                    style={{ minHeight: "300px" }} />
                ) : (
                  <pre className="text-[11px] text-[var(--text)] whitespace-pre-wrap font-mono max-h-[400px] overflow-y-auto bg-[var(--surface-2)] rounded-lg p-4 border border-white/[0.03] leading-relaxed">
                    {openNote.body || openNote.raw}
                  </pre>
                )}

                {/* Backlinks + Similar + Suggest Links */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-[var(--surface-1)] border border-white/[0.04] rounded-lg p-3">
                    <h4 className="text-[12px] text-[var(--text-muted)] font-medium mb-2">Backlinks ({backlinks.length})</h4>
                    {backlinks.length === 0 ? <p className="text-[10px] text-[var(--text-subtle)]">No links to this note</p>
                      : backlinks.map(b => (
                        <button key={b} onClick={() => openNoteByName(b)}
                          className="block text-[10px] text-[var(--text-muted)] hover:text-[var(--text)] truncate font-mono">{b}</button>
                      ))}
                  </div>
                  <div className="bg-[var(--surface-1)] border border-white/[0.04] rounded-lg p-3">
                    <h4 className="text-[12px] text-[var(--text-muted)] font-medium mb-2 flex items-center gap-2">
                      Similar
                      <button onClick={suggestNoteLinks} disabled={suggestBusy}
                        className={`text-[9px] px-1.5 py-0.5 rounded border border-white/[0.06] ${t.accent} disabled:opacity-40 ml-auto`}>
                        {suggestBusy ? "…" : "suggest links"}
                      </button>
                    </h4>
                    {similar.slice(0, 4).map(s => (
                      <button key={s.name} onClick={() => openNoteByName(s.name)}
                        className="block text-[10px] text-[var(--text-muted)] hover:text-[var(--text)] truncate font-mono w-full text-left">{s.name}</button>
                    ))}
                    {suggestLinks.length > 0 && (
                      <div className="mt-2 border-t border-white/[0.04] pt-2">
                        <p className="text-[9px] text-[var(--text-subtle)] mb-1">Suggested wikilinks:</p>
                        {suggestLinks.map(s => (
                          <div key={s.name} className="flex items-center gap-1">
                            <span className="text-[10px] text-[var(--text-muted)] font-mono flex-1 truncate">[[{s.name.split("/").pop()}]]</span>
                            <button onClick={() => applyLinks([s.name])}
                              className={`text-[9px] px-1 rounded ${t.accentBg} ${t.accent}`}>+</button>
                          </div>
                        ))}
                        <button onClick={() => applyLinks(suggestLinks.map(s => s.name))}
                          className={`mt-1 text-[9px] px-2 py-0.5 rounded ${t.accentBg} ${t.accent} w-full`}>apply all</button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ─── SEARCH TAB ────────────────────────────────────────────────── */}
      {brainTab === "search" && (
        <div className="space-y-4">
          <div className="flex gap-2">
            <input value={searchQ} onChange={e => setSearchQ(e.target.value)} onKeyDown={e => { if (e.key === "Enter") doSearch(); }}
              placeholder="Search vault notes by name or content…"
              className="flex-1 bg-[var(--surface-2)] border border-white/[0.04] rounded-md px-3 py-2 text-[12px] outline-none focus:border-white/10 font-mono" />
            <button onClick={doSearch} disabled={!searchQ.trim() || searchBusy}
              className={`text-[11px] px-4 py-2 rounded-md ${t.accentBg} ${t.accent} disabled:opacity-40`}>
              {searchBusy ? "Searching…" : "Search"}
            </button>
          </div>
          {searchResults.length === 0 && searchQ && !searchBusy && (
            <p className="text-[11px] text-[var(--text-subtle)]">No results for "{searchQ}"</p>
          )}
          <div className="space-y-2">
            {searchResults.map(r => (
              <div key={r.name} onClick={() => { setBrainTab("vault"); setVaultFolder(r.name.split("/")[0]); openNoteByName(r.name); }}
                className="bg-[var(--surface-1)] border border-white/[0.04] rounded-lg p-4 cursor-pointer hover:border-white/10 transition-colors">
                <div className="flex items-baseline gap-2 mb-1">
                  <span className={`text-[11px] font-mono ${t.accent}`}>{r.name}</span>
                  <span className="text-[9px] text-[var(--text-subtle)] ml-auto tabular-nums">score {r.score}</span>
                </div>
                {r.preview && <p className="text-[10px] text-[var(--text-muted)] leading-relaxed truncate">…{r.preview}…</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ─── TASKS TAB ─────────────────────────────────────────────────── */}
      {brainTab === "tasks" && (
        <Section
          title="Tasks/ Vault"
          actions={
            <button onClick={loadTasks} disabled={tasksLoading}
              className="text-[10px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40">
              {tasksLoading ? "Loading…" : <RefreshCw size={11} />}
            </button>
          }
        >
          {taskNotes.length === 0 && !tasksLoading && (
            <p className="text-[11px] text-[var(--text-subtle)]">No task notes in Tasks/ — create one in the Vault or via chat.</p>
          )}
          {taskNotes.map((note, ni) => (
            <Card key={note.name} title={note.name.replace("Tasks/", "")}
              actions={
                <span className="text-[9px] text-[var(--text-subtle)]">
                  {note.items.filter(i => i.done).length}/{note.items.length} done
                </span>
              }
            >
              {note.items.length === 0 ? (
                <p className="text-[10px] text-[var(--text-subtle)]">No checkboxes found.</p>
              ) : (
                <div className="space-y-1.5">
                  {note.items.map((item, li) => (
                    <label key={li} className="flex items-start gap-2.5 cursor-pointer group">
                      <input type="checkbox" checked={item.done} onChange={() => toggleTask(ni, li)}
                        className="mt-0.5 shrink-0 accent-amber-500" />
                      <span className={`text-[11px] transition-colors ${item.done ? "text-[var(--text-subtle)] line-through" : "text-[var(--text)] group-hover:text-[var(--text)]"}`}>
                        {item.text}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </Card>
          ))}
        </Section>
      )}

      {/* ─── LEARNING TAB (F4.2) ──────────────────────────────────────── */}
      {brainTab === "learning" && (
        <Section
          title="Self-directed Learning Tracks"
          actions={
            <button onClick={loadLearningTracks} disabled={learningLoading}
              className="text-[10px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40">
              {learningLoading ? "Loading…" : <RefreshCw size={11} />}
            </button>
          }
        >
          {learningTracks.length === 0 && !learningLoading && (
            <p className="text-[11px] text-[var(--text-subtle)]">
              No tracks configured. Edit <code className="text-[var(--text-muted)]">~/jarvis/config/learning_tracks.yaml</code> to add some.
            </p>
          )}
          {learningTracks.map(track => {
            const busy = learningBusyId === track.id;
            const pct = Math.round((track.progress_pct ?? 0) * 100);
            const isDone = track.status === "done";
            const isPaused = track.status === "paused";
            const isDropped = track.status === "dropped";
            return (
              <div key={track.id} className={`bg-[var(--surface-1)] border rounded-xl p-5 ${isDropped ? "border-white/[0.02] opacity-50" : "border-white/[0.04]"}`}>
                <div className="flex items-baseline gap-3 mb-2">
                  <h4 className={`text-[12px] font-mono ${t.accent}`}>{track.name}</h4>
                  <span className="text-[9px] uppercase tracking-widest text-[var(--text-subtle)]">
                    {track.cadence_days}d cadence
                  </span>
                  <span className={`ml-auto text-[9px] uppercase tracking-widest ${
                    isDone ? "text-green-500" : isPaused ? "text-amber-500" : isDropped ? "text-[var(--text-subtle)]" : "text-[var(--text-muted)]"
                  }`}>
                    {track.status}
                  </span>
                </div>
                <div className="flex items-center gap-3 mb-3">
                  <div className="flex-1 h-1.5 bg-white/[0.04] rounded-full overflow-hidden">
                    <div className={`h-full ${t.accentBg} transition-all`} style={{ width: `${pct}%` }} />
                  </div>
                  <span className="text-[10px] text-[var(--text-subtle)] font-mono shrink-0">
                    {track.topics_done}/{track.topics_total}
                  </span>
                </div>
                {track.current_topic && !isDone && (
                  <div className="text-[11px] text-[var(--text-muted)] mb-1">
                    <span className="text-[var(--text-subtle)]">current: </span>{track.current_topic}
                  </div>
                )}
                {track.next_topic && (
                  <div className="text-[10px] text-[var(--text-subtle)] mb-3">
                    next: {track.next_topic}
                  </div>
                )}
                {isDone && (
                  <div className="text-[11px] text-green-500/80 mb-3">
                    Track complete — all topics covered.
                  </div>
                )}
                <div className="flex gap-2 flex-wrap">
                  {!isDone && !isDropped && (
                    <button onClick={() => completeLearningTopic(track.id)} disabled={busy}
                      className={`text-[10px] px-2.5 py-1 border ${t.accentBorder} ${t.accent} rounded hover:bg-white/[0.02] disabled:opacity-40`}>
                      Mark current done
                    </button>
                  )}
                  {!isDropped && !isDone && (
                    isPaused ? (
                      <button onClick={() => setLearningStatus(track.id, "active")} disabled={busy}
                        className="text-[10px] px-2.5 py-1 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-white/[0.02] disabled:opacity-40">
                        Resume
                      </button>
                    ) : (
                      <button onClick={() => setLearningStatus(track.id, "paused")} disabled={busy}
                        className="text-[10px] px-2.5 py-1 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-white/[0.02] disabled:opacity-40">
                        Pause
                      </button>
                    )
                  )}
                  {!isDropped && (
                    <button onClick={() => setLearningStatus(track.id, "dropped")} disabled={busy}
                      className="text-[10px] px-2.5 py-1 border border-white/[0.04] rounded text-[var(--text-subtle)] hover:text-red-400 hover:border-red-400/30 disabled:opacity-40">
                      Drop
                    </button>
                  )}
                  {isDropped && (
                    <button onClick={() => setLearningStatus(track.id, "active")} disabled={busy}
                      className="text-[10px] px-2.5 py-1 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40">
                      Restore
                    </button>
                  )}
                  {track.sources.length > 0 && (
                    <span className="ml-auto text-[9px] text-[var(--text-subtle)] self-center">
                      {track.sources.length} source{track.sources.length === 1 ? "" : "s"}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </Section>
      )}

      {/* ─── SKILLS TAB (C14.1) ────────────────────────────────────── */}
      {brainTab === "skills" && (
        <Section
          title={`Distilled Skills (${skills.length})`}
          actions={
            <button onClick={loadSkills} disabled={skillsLoading}
              className="text-[10px] px-2 py-0.5 border border-white/[0.04] rounded text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40">
              {skillsLoading ? "Loading…" : <RefreshCw size={11} />}
            </button>
          }
        >
          <div className="flex gap-2">
            <input value={skillSearch} onChange={e => setSkillSearch(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") runSkillSearch(); }}
              placeholder="Search skills by task description or slug…"
              className="flex-1 bg-[var(--surface-2)] border border-white/[0.04] rounded-md px-3 py-1.5 text-[12px] outline-none focus:border-white/10 font-mono" />
            <button onClick={runSkillSearch} disabled={skillsLoading}
              className={`text-[11px] px-3 py-1.5 rounded-md ${t.accentBg} ${t.accent} disabled:opacity-40`}>
              Search
            </button>
          </div>
          {skills.length === 0 && !skillsLoading && (
            <p className="text-[11px] text-[var(--text-subtle)]">
              No skills distilled yet. Run an agent goal — successful completions over
              40 chars description / 60 chars result land here automatically.
            </p>
          )}
          <div className="space-y-1.5">
            {skills.map(s => {
              const isOpen = skillOpenSlug === s.slug;
              const age = (Date.now() / 1000 - s.created_at) / 86400;
              const ageLabel = age < 1 ? `${Math.round(age * 24)}h` : `${Math.round(age)}d`;
              return (
                <div key={s.slug} className="bg-[var(--surface-1)] border border-white/[0.04] rounded-lg">
                  <button onClick={() => openSkill(s.slug)}
                    className={`w-full text-left px-3 py-2 flex items-baseline gap-2 hover:bg-white/[0.02] transition-colors ${
                      isOpen ? "border-b border-white/[0.04]" : ""
                    }`}>
                    <span className={`text-[10px] font-mono ${t.accent} shrink-0`}>{s.slug}</span>
                    <span className="text-[10px] text-[var(--text-muted)] flex-1 truncate" title={s.task_desc}>
                      {s.task_desc}
                    </span>
                    <span className="text-[9px] text-[var(--text-subtle)] shrink-0">{s.agent_type}</span>
                    <span className="text-[9px] text-[var(--text-subtle)] shrink-0 w-12 text-right">{ageLabel} ago</span>
                    {s.usage_count > 0 && (
                      <span className="text-[9px] text-emerald-500 shrink-0">×{s.usage_count}</span>
                    )}
                  </button>
                  {isOpen && (
                    <div className="px-4 py-3">
                      {!skillOpen && <p className="text-[10px] text-[var(--text-subtle)]">Loading…</p>}
                      {skillOpen && (
                        <pre className="text-[11px] text-[var(--text)] whitespace-pre-wrap leading-relaxed font-mono max-h-72 overflow-y-auto">
                          {skillOpen.body}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      )}

      </div>
      </div>
    </div>
  );
}

// ─── Brain Graph viewer ────────────────────────────────────────────────────────
// Self-contained SVG force-directed graph. No external deps.

type GraphNode = { id: string; label: string; domain: string; val: number; size: number;
                   x: number; y: number; vx: number; vy: number; fixed?: boolean };
type GraphData = { nodes: { id: string; label: string; domain: string; val: number; size: number }[];
                   links: { source: string; target: string }[];
                   node_count: number; edge_count: number };

const DOMAIN_COLORS: Record<string, string> = {
  Daily:      "#fbbf24", // amber-400
  Inbox:      "#fb923c", // orange-400
  Notes:      "#34d399", // emerald-400
  Sources:    "#22d3ee", // cyan-400
  System:     "#a78bfa", // violet-400
  Projects:   "#60a5fa", // blue-400
  Operations: "#f472b6", // pink-400
  Tasks:      "#f87171", // red-400
  Templates:  "#94a3b8", // slate-400
  _:          "#64748b",
};

function BrainGraph() {
  const { t } = useTheme();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [includeIsolated, setIncludeIsolated] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [computedNodes, setComputedNodes] = useState<GraphNode[]>([]);

  const W = 800, H = 520;

  // Fetch data
  useEffect(() => {
    fetch(`${BACKEND}/brain/graph_data?include_isolated=${includeIsolated}`)
      .then(r => r.json()).then(setGraph).catch(() => {});
  }, [includeIsolated]);

  // Force-directed layout (Fruchterman–Reingold). Runs once on data load.
  useEffect(() => {
    if (!graph) return;
    const N = graph.nodes.length;
    if (N === 0) { setComputedNodes([]); return; }

    const k = Math.sqrt((W * H) / Math.max(N, 1)) * 0.85;
    const idIdx = new Map(graph.nodes.map((n, i) => [n.id, i]));

    // Initialize positions on a circle so the layout is reproducible
    const nodes: GraphNode[] = graph.nodes.map((n, i) => {
      const angle = (i / Math.max(N, 1)) * Math.PI * 2;
      return {
        ...n,
        x: W / 2 + Math.cos(angle) * Math.min(W, H) * 0.35,
        y: H / 2 + Math.sin(angle) * Math.min(W, H) * 0.35,
        vx: 0, vy: 0,
      };
    });

    const links = graph.links
      .map(l => ({ s: idIdx.get(l.source) ?? -1, t: idIdx.get(l.target) ?? -1 }))
      .filter(l => l.s >= 0 && l.t >= 0);

    const ITERS = 250;
    let temperature = W / 10;
    for (let iter = 0; iter < ITERS; iter++) {
      // Repulsion: every pair pushes apart with force k²/d
      for (let i = 0; i < N; i++) {
        nodes[i].vx = 0; nodes[i].vy = 0;
        for (let j = 0; j < N; j++) {
          if (i === j) continue;
          let dx = nodes[i].x - nodes[j].x;
          let dy = nodes[i].y - nodes[j].y;
          let d2 = dx*dx + dy*dy + 0.01;
          const force = (k * k) / d2;
          nodes[i].vx += dx * force;
          nodes[i].vy += dy * force;
        }
      }
      // Attraction along edges: force d²/k
      for (const e of links) {
        const a = nodes[e.s], b = nodes[e.t];
        const dx = a.x - b.x, dy = a.y - b.y;
        const d = Math.sqrt(dx*dx + dy*dy) + 0.01;
        const force = (d * d) / k;
        const fx = (dx / d) * force * 0.02;
        const fy = (dy / d) * force * 0.02;
        a.vx -= fx; a.vy -= fy;
        b.vx += fx; b.vy += fy;
      }
      // Apply with cooling
      for (const n of nodes) {
        const speed = Math.sqrt(n.vx * n.vx + n.vy * n.vy) + 0.01;
        const limit = Math.min(speed, temperature) / speed;
        n.x += n.vx * 0.01 * limit;
        n.y += n.vy * 0.01 * limit;
        // Clamp to canvas
        n.x = Math.max(20, Math.min(W - 20, n.x));
        n.y = Math.max(20, Math.min(H - 20, n.y));
      }
      temperature *= 0.97;
    }
    setComputedNodes(nodes);
  }, [graph]);

  // Pan/zoom handlers
  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 0.9 : 1.1;
    setZoom(z => Math.max(0.3, Math.min(5, z * factor)));
  };
  const onMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (e.button === 0) setDragging(true);
  };
  const onMouseUp = () => setDragging(false);
  const onMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (dragging) setPan(p => ({ x: p.x + e.movementX / zoom, y: p.y + e.movementY / zoom }));
  };

  if (!graph) {
    return (
      <Card title="Knowledge Graph">
        <p className="text-[11px] text-[var(--text-subtle)]">Loading graph data…</p>
      </Card>
    );
  }

  const idxById = new Map(computedNodes.map((n, i) => [n.id, i]));

  return (
    <Card
      title={`Knowledge Graph — ${graph.node_count} nodes · ${graph.edge_count} edges`}
      actions={
        <>
          <label className="text-[10px] text-[var(--text-subtle)] flex items-center gap-1">
            <input type="checkbox" checked={includeIsolated} onChange={e => setIncludeIsolated(e.target.checked)} />
            isolated
          </label>
          <button
            onClick={() => { setZoom(1); setPan({x:0,y:0}); }}
            className={`text-[10px] px-2 py-1 rounded-md ${t.accentBg} ${t.accent} border ${t.accentBorder}`}
          >reset</button>
        </>
      }
    >
      <p className="text-[10px] text-[var(--text-subtle)] mb-2">
        Drag to pan, scroll to zoom. Hover a node to see its name. Color = section.
      </p>

      <div className="bg-[var(--bg)] border border-white/[0.04] rounded-lg overflow-hidden">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          height={H}
          onWheel={onWheel}
          onMouseDown={onMouseDown}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}
          onMouseMove={onMouseMove}
          style={{ cursor: dragging ? "grabbing" : "grab", userSelect: "none" }}
        >
          <g transform={`translate(${W/2 + pan.x},${H/2 + pan.y}) scale(${zoom}) translate(${-W/2},${-H/2})`}>
            {/* Edges first so nodes draw on top */}
            {graph.links.map((l, i) => {
              const a = computedNodes[idxById.get(l.source) ?? -1];
              const b = computedNodes[idxById.get(l.target) ?? -1];
              if (!a || !b) return null;
              const isHover = hover === l.source || hover === l.target;
              return (
                <line
                  key={i}
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={isHover ? "#94a3b8" : "#334155"}
                  strokeWidth={isHover ? 1.2 : 0.5}
                  opacity={isHover ? 0.9 : 0.5}
                />
              );
            })}
            {/* Nodes */}
            {computedNodes.map(n => {
              const c = DOMAIN_COLORS[n.domain] ?? DOMAIN_COLORS._;
              const isHover = hover === n.id;
              return (
                <g key={n.id}>
                  <circle
                    cx={n.x} cy={n.y} r={n.size + (isHover ? 2 : 0)}
                    fill={c}
                    fillOpacity={isHover ? 1 : 0.85}
                    stroke={isHover ? "#fff" : "#000"}
                    strokeWidth={isHover ? 1.5 : 0.5}
                    onMouseEnter={() => setHover(n.id)}
                    onMouseLeave={() => setHover(null)}
                    style={{ cursor: "pointer" }}
                  />
                  {(isHover || n.val >= 4) && (
                    <text
                      x={n.x + n.size + 3}
                      y={n.y + 3}
                      fontSize={9}
                      fill={isHover ? "#fafafa" : "#94a3b8"}
                      pointerEvents="none"
                      style={{ fontFamily: "JetBrains Mono, monospace" }}
                    >
                      {n.label}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* Domain legend */}
      <div className="flex flex-wrap gap-3 mt-3">
        {Object.entries(DOMAIN_COLORS).filter(([k]) => k !== "_").map(([d, c]) => (
          <div key={d} className="flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
            <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ backgroundColor: c }} />
            {d}
          </div>
        ))}
      </div>

      {hover && (
        <div className={`mt-3 p-2 bg-[var(--surface-2)] border ${t.accentBorder} rounded-md`}>
          <span className={`text-[11px] font-mono ${t.accent}`}>{hover}</span>
        </div>
      )}
    </Card>
  );
}
