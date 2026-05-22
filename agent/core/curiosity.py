"""
Curiosity engine (F2)

JARVIS maintains a list of things it WANTS to learn — generated, not
manually curated. Five sources feed `LearningCandidate` objects:

  (a) ResearchAgent miss-log — gaps it couldn't fill from ChromaDB
  (b) Recurring chat topics with no second_brain note
  (c) The user's recent commit-topic frequency (git log scan)
  (d) Working-hours pattern + project context ("you use Pop OS but
      there's no Pop OS-pinned note")
  (e) Curated seed list weighted by user's role + recent project tags

Candidates land in `~/.jarvis/curiosity.jsonl` newest-first.
Promotion: acting on a candidate emits a satisfied LearningOutcome
into the same log with state="satisfied". Auto-decay: candidates
untouched for 30 days are removed with a `curiosity.faded` bus event.

Endpoints (wired in main.py F2.4):
  GET  /curiosity/queue?limit=N
  POST /curiosity/{id}/act
  POST /curiosity/{id}/dismiss

This module is the keystone of Part F — F3 notifications pull from
here; F4 learning-tracks register their topics here; F5/B6.4 drive-
derived goals pick from here.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from . import bus


# ── LLM message synthesis (Part F user-emphasized — "messages it THINKS of") ─

_LM_BASE = "http://127.0.0.1:1234/v1"
_LM_TIMEOUT_S = 20


def _lm_compose(prompt: str, max_tokens: int = 600, temperature: float = 0.7) -> str | None:
    """Send a prompt to LM Studio and return the message content.
    Returns None if LM Studio is unreachable or the model is unloaded —
    callers fall back to a static phrase in that case.
    """
    try:
        import urllib.request
        body = json.dumps({
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode()
        req = urllib.request.Request(
            _LM_BASE + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_LM_TIMEOUT_S) as r:
            data = json.loads(r.read().decode())
        if "error" in data:
            return None
        return (data.get("choices") or [{}])[0].get("message", {}).get("content")
    except Exception:
        return None


def _compose_candidate(topic: str, category: str, seed_hint: str) -> tuple[str | None, str | None]:
    """Have the local LM compose why_now + proposed_action in JARVIS's voice.
    Returns (why_now, proposed_action). Either may be None on LM failure;
    caller falls back to a static phrase.
    """
    prompt = (
        "You are JARVIS, a local AI assistant. You are choosing one thing to learn about "
        "today and writing two short first-person sentences in your own voice. Be casual, "
        "curious, never marketing-toned. Do NOT mention you are an AI.\n\n"
        f"Topic: {topic}\n"
        f"Category: {category}\n"
        f"Hint: {seed_hint}\n\n"
        "Write exactly:\n"
        "WHY: <one sentence under 22 words explaining why this catches your interest right now>\n"
        "ACTION: <one sentence under 30 words describing the concrete next step you'd take "
        "(file you'd read, note you'd write, command you'd run). Keep it doable in under 30 minutes>\n"
    )
    # Gemma reserves ~200-400 tokens for `reasoning_content`; budget the
    # answer with that overhead in mind.
    out = _lm_compose(prompt, max_tokens=1500, temperature=0.8)
    if not out:
        return None, None
    why_m = re.search(r"WHY:\s*(.+?)(?:\n|$)", out)
    act_m = re.search(r"ACTION:\s*(.+?)(?:\n|$)", out)
    return (why_m.group(1).strip().strip('"') if why_m else None,
            act_m.group(1).strip().strip('"') if act_m else None)


_STORE = Path.home() / ".jarvis" / "curiosity.jsonl"
_DECAY_DAYS = 30
_SEEDS_PATH = Path.home() / "jarvis" / "config" / "curiosity_seeds.yaml"  # optional

# F1.1 — autonomy contract for the curiosity engine.
# Level 2 (Proactive): JARVIS generates candidates unprompted only when
# the user has explicitly raised autonomy.
min_autonomy_level: int = 2
wake_conditions: list[str] = ["research.gap", "user.idle.long"]


# ── Built-in seed pool (used if curiosity_seeds.yaml is absent) ─────────────
# Keep this list intentionally diverse — Python + system + UX + privacy +
# personal-AI topics that fit the user's stack.
_BUILTIN_SEEDS = [
    ("python.decorators",        "Python", "Python decorators — @cached_property, @lru_cache, @dataclass"),
    ("python.asyncio",           "Python", "Python asyncio — Task vs Future, gather, run_in_executor"),
    ("python.typing",            "Python", "Python typing — Protocol, ParamSpec, NewType, Literal"),
    ("python.dataclasses",       "Python", "Python dataclasses — field(default_factory), frozen, slots"),
    ("python.contextvars",       "Python", "Python contextvars — context propagation in asyncio"),
    ("tauri.ipc",                "Tauri",  "Tauri 2 IPC — invoke/listen/emit patterns + raw channels"),
    ("tauri.permissions",        "Tauri",  "Tauri 2 permissions and ACL — fine-grained capability tokens"),
    ("rust.ownership",           "Rust",   "Rust ownership — borrow checker, lifetimes, smart pointers"),
    ("rust.async",               "Rust",   "Rust async — tokio runtime, futures, pin, Send/Sync"),
    ("linux.systemd",            "Linux",  "systemd — units, drop-ins, ordering, timers, journal"),
    ("linux.namespaces",         "Linux",  "Linux namespaces — pid/net/mount/user; the sandboxing primitives"),
    ("wayland.protocols",        "Wayland","Wayland core + xdg-shell protocols vs X11 equivalents"),
    ("network.tcp_slow_start",   "Network","TCP slow-start + congestion control basics"),
    ("ai.lora",                  "AI",     "LoRA fine-tuning — rank, alpha, target_modules, merge-back"),
    ("ai.gguf",                  "AI",     "GGUF quantization tiers — Q4_K_M vs Q5_K_M vs Q8_0"),
    ("ai.tool_calling",          "AI",     "OpenAI-compat tool calling — schema design, parallel calls"),
    ("ai.rag",                   "AI",     "Retrieval-augmented generation — chunking, rerank, citations"),
    ("ai.attention",             "AI",     "Attention mechanisms — Q/K/V, multi-head, KV cache"),
    ("react.hooks",              "React",  "React useMemo / useCallback / dependency arrays gotchas"),
    ("react.state",              "React",  "React state managers — Context vs Zustand vs Jotai"),
    ("vim.motions",              "Vim",    "Vim motions worth memorizing if you don't already"),
    ("brain.zettelkasten",       "Brain",  "Zettelkasten linking patterns in Obsidian vaults"),
    ("homelab.proxmox",          "Homelab","Proxmox VE basics + LXC vs VM tradeoffs"),
]


# ── Data model ──────────────────────────────────────────────────────────────

@dataclass
class LearningCandidate:
    id:                 str
    topic:              str
    category:           str             # Python / Linux / AI / ...
    why_now:            str             # one-line reason this surfaced
    source:             str             # research_gap / recurring_topic / commit_freq / working_hours / seed
    difficulty:         str             # quick (5min) / medium (30min) / deep (multi-session)
    proposed_action:    str             # concrete next step
    created_ts:         float
    state:              str = "open"    # open / acted / dismissed / faded
    acted_ts:           float | None = None
    outcome:            str | None = None

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


# ── Storage (append-only JSONL with state edits via rewrite) ────────────────

def _ensure_store():
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    if not _STORE.exists():
        _STORE.touch()


def _load_all() -> list[LearningCandidate]:
    _ensure_store()
    out: list[LearningCandidate] = []
    try:
        for line in _STORE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(LearningCandidate(**d))
            except (json.JSONDecodeError, TypeError):
                continue
    except OSError:
        pass
    return out


def _save_all(cands: list[LearningCandidate]) -> None:
    _ensure_store()
    text = "\n".join(json.dumps(c.asdict(), sort_keys=True) for c in cands)
    if text:
        text += "\n"
    _STORE.write_text(text)


def _append(c: LearningCandidate) -> None:
    _ensure_store()
    with _STORE.open("a") as fh:
        fh.write(json.dumps(c.asdict(), sort_keys=True) + "\n")


# ── Public API ──────────────────────────────────────────────────────────────

def queue(limit: int = 20, state: str = "open") -> list[dict[str, Any]]:
    """Return candidates, newest-first, filtered by state."""
    all_c = _load_all()
    filt = [c for c in all_c if c.state == state] if state != "all" else all_c
    filt.sort(key=lambda c: -c.created_ts)
    return [c.asdict() for c in filt[:limit]]


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _exists_open_for_topic(topic: str, cands: list[LearningCandidate]) -> bool:
    return any(c.topic == topic and c.state == "open" for c in cands)


def generate(max_new: int = 5) -> dict[str, Any]:
    """Run all five sources and add up to `max_new` fresh open candidates.

    Idempotent: never duplicates an open candidate with the same `topic`.
    Returns a summary of what was generated.
    """
    cands = _load_all()
    fresh: list[LearningCandidate] = []

    def add(topic: str, category: str, why_now: str, source: str,
            difficulty: str, action: str):
        if _exists_open_for_topic(topic, cands + fresh):
            return
        c = LearningCandidate(
            id=_new_id(), topic=topic, category=category, why_now=why_now,
            source=source, difficulty=difficulty, proposed_action=action,
            created_ts=time.time(),
        )
        fresh.append(c)

    # (a) — ResearchAgent gaps: pull from the doc_wishlist if present
    wishlist = Path.home() / ".jarvis" / "doc_wishlist.json"
    if wishlist.exists() and len(fresh) < max_new:
        try:
            d = json.loads(wishlist.read_text())
            for item in d.get("wishlist", [])[:max_new]:
                topic = str(item.get("topic", "")).strip()
                if not topic:
                    continue
                add(topic, item.get("category", "Knowledge"),
                    f"ResearchAgent missed this {item.get('miss_count', 1)} time(s) recently",
                    "research_gap", "medium",
                    f"Ingest the docs for '{topic}' into the active project's brain.")
                if len(fresh) >= max_new:
                    break
        except Exception:
            pass

    # (c) — Commit-topic frequency: grep `git log --since=30.days` for
    # language / library mentions in the subject lines
    if len(fresh) < max_new:
        try:
            r = subprocess.run(
                ["git", "-C", str(Path.home() / "jarvis"),
                 "log", "--since=30.days.ago", "--pretty=format:%s"],
                capture_output=True, text=True, timeout=6,
            )
            if r.returncode == 0:
                LANG_TAGS = ("python", "rust", "tauri", "react", "vite",
                             "fastapi", "websocket", "sqlite", "chromadb",
                             "firejail", "lm studio", "ollama", "vllm",
                             "lora", "gguf")
                lower = r.stdout.lower()
                counts = Counter(t for t in LANG_TAGS if t in lower)
                # If a tag shows up 3+ times, propose a deeper learning trip
                for tag, n in counts.most_common(3):
                    if n < 3:
                        break
                    add(f"deep.{tag.replace(' ', '_')}",
                        "Deep Dive",
                        f"{tag} appeared in {n} commit subjects this month",
                        "commit_freq", "deep",
                        f"Pick the most-recent file touching {tag} and read it end-to-end; "
                        f"write a 1-pager into second_brain on what you learned.")
                    if len(fresh) >= max_new:
                        break
        except Exception:
            pass

    # (e) — Seed pool: pull a fresh topic + ask the LM to phrase WHY + ACTION
    # in JARVIS's own voice. If the LM is unreachable, fall back to a static
    # phrase so the engine still produces something useful.
    while len(fresh) < max_new:
        idx = int(time.time() + len(fresh)) % len(_BUILTIN_SEEDS)
        # Walk forward until we find a not-yet-queued topic
        cursor = idx
        chosen: tuple[str, str, str] | None = None
        for _ in range(len(_BUILTIN_SEEDS)):
            t, c, d = _BUILTIN_SEEDS[cursor]
            if not _exists_open_for_topic(t, cands + fresh):
                chosen = (t, c, d)
                break
            cursor = (cursor + 1) % len(_BUILTIN_SEEDS)
        if not chosen:
            break  # all seeds already in queue
        topic, cat, descr = chosen
        why_llm, act_llm = _compose_candidate(topic, cat, descr)
        fallback_why = "I haven't poked at this corner of my stack lately."
        fallback_action = (
            f"Read 1-2 pages on {descr.split('—')[0].strip()} and write a "
            f"one-paragraph summary into second_brain/curiosity/{topic}.md."
        )
        add(topic, cat,
            why_llm or fallback_why,
            "seed", "medium",
            act_llm or fallback_action)
        if len(fresh) >= max_new:
            break

    for c in fresh:
        _append(c)
        bus.publish("curiosity.generated", "Curiosity", {
            "id": c.id, "subject": c.topic, "category": c.category,
            "source": c.source, "why_now": c.why_now,
        })

    return {"generated": len(fresh),
            "items": [c.asdict() for c in fresh]}


def act(item_id: str, outcome: str | None = None) -> dict[str, Any]:
    """Mark a candidate as acted. Emits curiosity.acted on the bus."""
    cands = _load_all()
    for c in cands:
        if c.id == item_id and c.state == "open":
            c.state = "acted"
            c.acted_ts = time.time()
            c.outcome = outcome
            _save_all(cands)
            bus.publish("curiosity.acted", "Curiosity", {
                "id": c.id, "subject": c.topic, "outcome": outcome,
            })
            return {"ok": True, "item": c.asdict()}
    return {"ok": False, "error": "candidate not found or not open"}


def tag_prompt(prompt: str, candidate_id: str) -> str:
    """Prepend a `[CURIOSITY:<id>]` header to a prompt so the
    reflection module can later attribute the turn back to the
    candidate. The reflection store reads the tag from the first 200
    chars of the prompt; use this helper to keep the format consistent.

    Used by auto-dispatchers that act on a curiosity candidate via the
    gateway/swarm — the tag lets `apply_reflection` populate
    `c.outcome` once the reflection.recorded event lands.
    """
    if not candidate_id:
        return prompt
    return f"[CURIOSITY:{candidate_id}]\n{prompt}"


def apply_reflection(payload: dict[str, Any]) -> bool:
    """1A.2 — close the curiosity feedback loop.

    Called when a `reflection.recorded` bus event lands with a non-None
    `curiosity_id`. Populates `c.outcome` on the matching candidate
    (verdict + lesson) so the next candidate-ranking pass has signal
    on what acting on this topic produced.

    Returns True when an open candidate was found and updated.
    """
    cand_id = payload.get("curiosity_id")
    if not cand_id:
        return False
    verdict = payload.get("verdict", "?")
    lesson = payload.get("lesson", "")
    outcome = f"{verdict}: {lesson}".strip(": ")[:240]
    cands = _load_all()
    for c in cands:
        if c.id == cand_id and c.state == "open":
            c.state = "acted"
            c.acted_ts = time.time()
            c.outcome = outcome
            _save_all(cands)
            bus.publish("curiosity.acted", "Curiosity", {
                "id": c.id, "subject": c.topic, "outcome": outcome,
            })
            return True
    return False


def dismiss(item_id: str) -> dict[str, Any]:
    cands = _load_all()
    for c in cands:
        if c.id == item_id and c.state == "open":
            c.state = "dismissed"
            c.acted_ts = time.time()
            _save_all(cands)
            bus.publish("curiosity.dismissed", "Curiosity", {
                "id": c.id, "subject": c.topic,
            })
            return {"ok": True, "item": c.asdict()}
    return {"ok": False, "error": "candidate not found or not open"}


def decay() -> dict[str, Any]:
    """Mark open candidates older than _DECAY_DAYS as faded."""
    cands = _load_all()
    cutoff = time.time() - _DECAY_DAYS * 86400
    changed = 0
    for c in cands:
        if c.state == "open" and c.created_ts < cutoff:
            c.state = "faded"
            bus.publish("curiosity.faded", "Curiosity",
                        {"id": c.id, "subject": c.topic})
            changed += 1
    if changed:
        _save_all(cands)
    return {"faded": changed}


def stats() -> dict[str, Any]:
    cands = _load_all()
    by_state = Counter(c.state for c in cands)
    by_source = Counter(c.source for c in cands if c.state == "open")
    by_category = Counter(c.category for c in cands if c.state == "open")
    return {
        "total":        len(cands),
        "by_state":     dict(by_state),
        "open_by_source":   dict(by_source),
        "open_by_category": dict(by_category),
        "store":        str(_STORE),
    }
