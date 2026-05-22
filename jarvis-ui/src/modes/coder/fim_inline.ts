/**
 * C15.1 follow-up — FIM inline ghost-text for Coder mode.
 *
 * Self-contained CodeMirror 6 extension. After the user stops typing
 * for `IDLE_MS`, fires POST /coder/complete with prefix/suffix split
 * at the cursor. The response renders as a muted widget after the
 * caret; Tab accepts (inserts + clears ghost), Esc dismisses, any
 * other edit clears it too.
 *
 * Race-safe: each fetch increments `seq`; out-of-order responses are
 * dropped if the cursor has moved or another fetch has fired.
 *
 * No new dependencies — uses the @codemirror/{view,state} peers that
 * `codemirror` already pulls in.
 */
import {
  Decoration, type DecorationSet, EditorView, ViewPlugin,
  type ViewUpdate, WidgetType, keymap,
} from "@codemirror/view";
import {
  StateEffect, StateField, type Extension, type EditorState,
  Prec,
} from "@codemirror/state";

const IDLE_MS = 600;            // debounce window
const MIN_PREFIX_CHARS = 12;    // skip cursor-at-start-of-file noise
const MAX_INSERT_CHARS = 400;   // safety: don't insert a whole novel

// ── State ────────────────────────────────────────────────────────────────────

const setGhost  = StateEffect.define<{ text: string; pos: number } | null>();

const ghostField = StateField.define<DecorationSet>({
  create() { return Decoration.none; },
  update(deco, tr) {
    deco = deco.map(tr.changes);
    let next: DecorationSet | null = null;
    for (const e of tr.effects) {
      if (e.is(setGhost)) {
        if (e.value === null) {
          next = Decoration.none;
        } else {
          const widget = Decoration.widget({
            widget: new GhostWidget(e.value.text),
            side: 1,
          });
          next = Decoration.set([widget.range(e.value.pos)]);
        }
      }
    }
    // Any doc change clears the ghost (user edited).
    if (tr.docChanged && next === null) return Decoration.none;
    return next ?? deco;
  },
  provide: f => EditorView.decorations.from(f),
});

class GhostWidget extends WidgetType {
  constructor(readonly text: string) { super(); }
  eq(other: GhostWidget) { return other.text === this.text; }
  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-fim-ghost";
    span.style.opacity = "0.45";
    span.style.color = "#94a3b8";          // slate-400
    span.style.fontStyle = "italic";
    span.style.pointerEvents = "none";
    span.textContent = this.text;
    return span;
  }
  ignoreEvent() { return true; }
}

// Read the current ghost text + its anchor from state, if any.
function currentGhost(state: EditorState): { text: string; pos: number } | null {
  const set = state.field(ghostField, false);
  if (!set || set.size === 0) return null;
  let out: { text: string; pos: number } | null = null;
  set.between(0, state.doc.length, (from, _to, value) => {
    const w = value.spec.widget as GhostWidget;
    out = { text: w.text, pos: from };
    return false;
  });
  return out;
}

// ── Debounced fetcher plugin ─────────────────────────────────────────────────

const fimPlugin = (backend: string) => ViewPlugin.fromClass(class {
  timer: ReturnType<typeof setTimeout> | null = null;
  seq = 0;

  constructor(readonly view: EditorView) {}

  update(u: ViewUpdate) {
    // Any user activity invalidates the in-flight fetch.
    if (u.docChanged || u.selectionSet) {
      this.seq += 1;
      if (this.timer) { clearTimeout(this.timer); this.timer = null; }
      // Schedule a new fetch after IDLE_MS.
      this.timer = setTimeout(() => this.fire(), IDLE_MS);
    }
  }

  async fire() {
    this.timer = null;
    const view = this.view;
    const pos = view.state.selection.main.head;
    const doc = view.state.doc.toString();
    const prefix = doc.slice(0, pos);
    const suffix = doc.slice(pos);
    if (prefix.length < MIN_PREFIX_CHARS) return;

    // If a ghost is already shown at this exact pos, don't re-fetch.
    const existing = currentGhost(view.state);
    if (existing && existing.pos === pos) return;

    const mySeq = ++this.seq;
    try {
      const r = await fetch(`${backend}/coder/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prefix, suffix, max_tokens: 80 }),
      });
      const d = await r.json();
      // Bail if user kept typing / moved cursor.
      if (mySeq !== this.seq) return;
      if (this.view.state.selection.main.head !== pos) return;
      if (!d.ok || !d.completion) return;
      let text = String(d.completion).slice(0, MAX_INSERT_CHARS);
      // Don't render a completely-empty or whitespace-only ghost.
      if (!text.trim()) return;
      this.view.dispatch({
        effects: setGhost.of({ text, pos }),
      });
    } catch { /* network glitch — swallow */ }
  }

  destroy() {
    if (this.timer) clearTimeout(this.timer);
  }
});

// ── Keymap ───────────────────────────────────────────────────────────────────

function acceptGhost(view: EditorView): boolean {
  const ghost = currentGhost(view.state);
  if (!ghost) return false;
  view.dispatch({
    changes: { from: ghost.pos, to: ghost.pos, insert: ghost.text },
    selection: { anchor: ghost.pos + ghost.text.length },
    effects: setGhost.of(null),
  });
  return true;
}

function dismissGhost(view: EditorView): boolean {
  if (!currentGhost(view.state)) return false;
  view.dispatch({ effects: setGhost.of(null) });
  return true;
}

// ── Public ───────────────────────────────────────────────────────────────────

/**
 * Wire FIM ghost-text into a CodeMirror EditorView.
 *
 * @param backend  Base URL of the JARVIS backend (e.g. http://127.0.0.1:8000).
 *                 The extension hits `${backend}/coder/complete`.
 */
export function fimInline(backend: string): Extension {
  return [
    ghostField,
    fimPlugin(backend),
    // Higher precedence so Tab/Esc beat the default keymap when a ghost
    // is showing.
    Prec.high(keymap.of([
      { key: "Tab",    run: acceptGhost },
      { key: "Escape", run: dismissGhost },
    ])),
  ];
}
