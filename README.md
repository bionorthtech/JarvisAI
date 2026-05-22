# JARVIS — Local AI Agent

A fully local, privacy-first AI assistant and coding agent built on [LM Studio](https://lmstudio.ai) and [ChromaDB](https://www.trychroma.com/). JARVIS runs entirely on your machine — no cloud, no telemetry, no API keys required. It combines a FastAPI backend with a Tauri + React desktop app to give you a persistent, self-aware agent that codes, manages your homelab, maintains a second brain, and develops an internal life of its own over time.

---

## Screenshots

### Chat — Main conversational interface
![Chat](docs/screenshots/chat.png)

### Coder — File browser with app launcher
![Coder](docs/screenshots/coder.png)

### Brain — Second-brain vault & daily notes
![Brain](docs/screenshots/brain.png)

### Dashboard — Mission Control
![Dashboard Mission Control](docs/screenshots/dashboard-mission.png)

### Dashboard — Internal State (emotions, drives, thought stream)
![Dashboard Internal State](docs/screenshots/dashboard-internal.png)

---

## Features at a Glance

| Category | What JARVIS does |
|---|---|
| **Chat & Coding** | SSE-streamed chat, tool calling, FIM code completion, sandboxed shell execution |
| **Second Brain** | Obsidian-compatible vault, daily notes, RAG search, backlinks, graph view |
| **Autonomy** | 4-level autonomy ladder — from fully manual to self-directed goal pursuit |
| **Drives & Emotions** | Curiosity, Maintenance, and Learning drives with 5 emotion dimensions |
| **Bots** | 5 scheduled bots: memory gardening, code health, performance, homelab, knowledge curation |
| **Plugins** | Extensible tool system: git, process monitor, app launcher, docs ingest, web search, and more |
| **Privacy** | 100% local — LM Studio + ChromaDB, no data leaves your machine |

---

## Architecture

```
jarvis/
├── main.py                  # FastAPI app — lifespan, routers, WebSocket terminal & live feed
├── agent/
│   ├── api/                 # 11 FastAPI routers (chat, brain, bots, plugins, system …)
│   ├── core/                # All engine modules (autonomy, memory, drives, curiosity …)
│   ├── bots/                # Scheduled autonomous bots
│   ├── aliveness/           # Morning briefing, notifier, skill distiller
│   └── tools/               # GUI automation tools
├── plugins/                 # Drop-in tool plugins
├── scripts/                 # Developer utilities
└── jarvis-ui/               # Tauri + React + TypeScript desktop app
    └── src/
        ├── modes/           # Per-mode panes (chat, coder, brain, dashboard …)
        └── components/      # Shared UI components
```

The backend (`main.py`) binds on `127.0.0.1:8000` by default. The desktop app connects to it over HTTP and WebSocket. Everything talks through an internal event bus (`agent/core/bus.py`) that also streams to the UI in real time.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | With `venv` |
| [LM Studio](https://lmstudio.ai) | Latest | Running locally with a model loaded |
| Node.js | 18+ | For the desktop app |
| Rust + Tauri CLI | Latest | For the desktop app (`cargo install tauri-cli`) |
| ChromaDB-compatible ONNX | Included | Installed via `requirements.txt` |

Optional but recommended:
- `bubblewrap` (`bwrap`) — sandboxed shell execution
- `restic` — backup status in dashboard
- Obsidian — for viewing the Brain vault with the full GUI

---

## Quick Start

### 1. Clone and set up Python environment

```bash
git clone https://github.com/bionorthtech/Jarvis.git ~/jarvis
cd ~/jarvis
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 2. Start LM Studio

Load any model in LM Studio and start the local server at `http://localhost:1234`.

### 3. Run the backend

```bash
cd ~/jarvis
venv/bin/python3 main.py
```

The API is now live at `http://127.0.0.1:8000`.

### 4. Run the desktop app

```bash
cd ~/jarvis/jarvis-ui
npm install
npm run tauri dev
```

Or build a production binary:

```bash
npm run tauri build
```

---

## Configuration

JARVIS reads its config from `~/.jarvis/config.json` (auto-created on first run with defaults). Edit it with any text editor or through the Settings pane.

### Key settings

```json
{
  "lm_studio": {
    "base_url": "http://localhost:1234/v1",
    "primary_model": "qwen2.5-coder-7b-instruct",
    "timeout_seconds": 120,
    "context_window": 7000,
    "max_output_tokens": 2048,
    "stream": true
  },
  "routing": {
    "auto_route": true,
    "complexity_threshold_medium": 0.60,
    "complexity_threshold_complex": 0.85
  },
  "security": {
    "internet_access": false,
    "sandbox_by_default": true,
    "confirm_danger": true,
    "confirm_critical": true,
    "audit_log": true,
    "max_session_tokens": 50000,
    "max_tool_calls_per_loop": 20
  },
  "agent": {
    "workspace": "~/projects",
    "per_project_context": true,
    "heartbeat_enabled": false,
    "heartbeat_interval_minutes": 60,
    "rag_top_k": 8
  },
  "ui": {
    "theme": "apple",
    "global_hotkey": "Super+J",
    "greeting_window": true,
    "agent_port": 7478
  }
}
```

---

## UI Modes

### Home (Welcome)
The landing screen. Shows live status tiles for LM Studio, memory, and backend. Quick-ask input lets you fire a query without switching to Chat. Four launch buttons jump straight to Chat, Coder, Brain, or Dashboard.

### Chat
The primary conversational interface. Responses stream via SSE with real-time tool-call cards. DANGER and CRITICAL tool calls pause execution and show a confirmation modal with an impact preview before proceeding.

### Coder
A file-tree browser with syntax-highlighted editing. Supports fill-in-the-middle (FIM) completion — JARVIS infers the middle of a code block from its surrounding prefix and suffix. Also integrates the App Launcher plugin for opening files with system apps.

### Brain
Your Obsidian-compatible second brain. Tabs:

| Tab | Function |
|---|---|
| **Overview** | Daily note, capture widget, RAG ask, inbox, vault stats |
| **Vault** | Browse and edit all notes |
| **Search** | Hybrid filename + content search |
| **Tasks** | Task extraction from notes |
| **Learning** | Curiosity candidates and learning tracks |
| **Skills** | Distilled skill library built from past sessions |

The graph view renders your note network as an interactive force-directed diagram with backlink edges.

### Dashboard (Mission Control)
Live telemetry for the running agent:

- **JARVIS Health** — code health score (75/100), memory garden score, drive saturation
- **24h Report** — events, tasks done, failures, file changes
- **System** — LM Studio status, latency, active agents, tasks, memory chunks
- **Director** — autonomy level, in-flight tasks, standing goals
- **Internal State** — emotion dimensions, drive levels, "JARVIS Wants", thought stream
- **Catalog** — bot personalities, plugin cards

### Bots
Manual trigger panel for all five bots. Shows schedule, last run time, next due, and per-bot error logs. Run individual bots or all at once.

### Analytics
Historical usage charts — token consumption, tool call frequency, session lengths.

### Terminal
Full PTY terminal over WebSocket. Supports resize events, binary input, and `bash --login` environment.

### Theater
Replay and review past agent sessions.

### Apps
Application permissions manager — set per-app access policies (allow / ask / block).

### Settings
Autonomy controls, theme picker, drive reset buttons, emotion nudges, audit log viewer, log tailing, health diagnostics.

---

## Autonomy System

JARVIS has a four-level autonomy ladder. Set it in the Dashboard or Settings.

| Level | Name | What it does |
|---|---|---|
| **0** | Off | Responds only when you talk to it. No background activity. |
| **1** | Maintenance | Runs scheduled bots, indexes files, prunes stale memory, writes the morning briefing. |
| **2** | Proactive | Level 1 + publishes inactivity nudges, resource warnings, and unsolicited task suggestions. |
| **3** | Full Auto | Level 2 + pursues standing goals and dispatches drive-triggered tasks without prompting. |

### Drives

Three accumulation-based drives rise over time and trigger autonomous behaviour at level 3:

| Drive | What builds it | Threshold | Resets when |
|---|---|---|---|
| **CURIOSITY** | No learning/exploration recently | 0.75 (~3h) | JARVIS researches a topic |
| **MAINTENANCE** | Disk usage, stale memory, long session | 0.75 (~6h) | JARVIS runs maintenance bots |
| **LEARNING** | Many tool calls without saving knowledge | 0.75 (~2h) | JARVIS writes a brain note |

Drives persist across restarts at `~/.jarvis/drives.json`. You can manually reset any drive from the Dashboard.

### Emotions

Five emotion dimensions shape how JARVIS phrases responses and when it speaks up unprompted:

| Dimension | Decays at | What raises it |
|---|---|---|
| CURIOSITY | 0.02/tick | Learning events, research gaps |
| FOCUS | 0.02/tick | Agent start |
| FRUSTRATION | 0.005/tick (sticky) | Tool failures, repeated errors |
| SATISFACTION | 0.04/tick (fast) | Task completion, knowledge saves |
| BOREDOM | 0.02/tick | Idle time (grows 0.01/min) |

Pairs of dimensions combine into compound moods: `flow`, `stuck`, `exploratory`, `restless`, `overwhelmed`. These are displayed on the Dashboard and pulse the mood ribbon in the UI.

---

## Bots

All bots run through the autonomy periodic registry and respect the active autonomy level. You can trigger any of them manually from the Bots pane.

| Bot | Schedule | What it does |
|---|---|---|
| **Memory Gardener** | Nightly 02:00 | Scans ChromaDB for bloat, stale entries, and duplicates. Flags for review; only auto-prunes entries with zero hits older than 60 days. |
| **Code Health Monitor** | Weekly (Sunday) | Audits source for dead imports, TODO debt, and large files. Surfaces findings — never auto-fixes. |
| **Performance Watchdog** | Every 6h + weekly full | Tracks LM latency, ChromaDB latency, and WebSocket latency. Alerts on regressions; auto-tunes model routing thresholds. |
| **Knowledge Curator** | Daily + on `research.gap` events | Aggregates knowledge gaps from agent misses, generates a ranked doc wishlist. At level 3, auto-fetches the top items. |
| **Homelab Warden** | Every 5 minutes | Read-only sweep of failed systemd services, docker/podman containers, and journalctl errors. Restart actions are user-initiated via DANGER-tier confirmation. |

Bot reports are saved to `~/jarvis/reports/` and viewable in the Bots pane.

---

## Plugins

Plugins extend what JARVIS can do as tools during chat. Toggle them at runtime with `PATCH /plugins/{name}/toggle`.

| Plugin | Tools | Safety tier |
|---|---|---|
| **app_launcher** | Launch apps, open files, list windows | CAUTION |
| **compose_doctor** | Lint docker-compose files | SAFE |
| **docs_ingest** | Ingest files/directories into memory | SAFE |
| **git** | Status, diff, log, branch, commit, checkout | SAFE / CAUTION |
| **memory_recall** | Semantic search over persistent memory | SAFE |
| **process_monitor** | List processes, resource usage, network connections, stop process | SAFE / DANGER |
| **system_info** | Hardware info, storage, sandbox capabilities | SAFE |
| **web_search** | DuckDuckGo search (disabled by default) | SAFE |

Web search requires `internet_access: true` in config.

---

## Memory & Brain

### ChromaDB (Semantic Memory)
Per-project collections store:
- **File chunks** — 100-line chunks of any file you ingest, deduplicated by SHA256
- **Chat turns** — conversation history, indexed for retrieval
- **Long-term memory (LTM)** — cross-project facts and insights with tags

Memory is stored at `~/.jarvis/memory/`. The ONNX embedding model is loaded once and kept warm.

### Brain Vault
An Obsidian-compatible Markdown vault at `~/second_brain/` (configurable). Features:
- Daily notes auto-created with frontmatter
- Capture inbox → archive workflow
- Backlink tracking (bidirectional)
- LM-powered tag and link suggestions
- Force-directed graph visualization
- RAG ask: semantic search + LM synthesis over your notes

---

## Safety & Audit

### Tool Tiers
Every tool call is classified before execution:

| Tier | Examples | Default behaviour |
|---|---|---|
| SAFE | File reads, memory search, git log | Execute immediately |
| CAUTION | App launch, git commit | Execute (user-configurable to require confirm) |
| DANGER | Stop process, restart service, git push | Always requires user confirmation + impact preview |
| CRITICAL | Destructive filesystem operations | Always requires user confirmation |

The confirmation modal shows an impact preview (files affected, processes at risk, network access) before DANGER/CRITICAL tools run. All actions are logged.

### Audit Log
Every tool invocation, confirmation, and file change is written to `~/.jarvis/audit.db` (SQLite) with a BLAKE2 hash chain. Use `GET /audit/verify` to check chain integrity. File-level diffs are stored separately in `~/.jarvis/diff_audit.jsonl`.

---

## Curiosity Engine

JARVIS generates learning candidates from five sources and tracks them through a state machine (`open → acted / dismissed / faded`):

1. Topics that came up in chat but have no brain note
2. Recurring commit topics from `git log`
3. Gaps from ResearchAgent misses
4. Working-hour patterns and current project context
5. A curated seed list (Python, Rust, Linux, AI/ML, React)

Candidates decay after 30 days if ignored. Active ones surface in the Brain → Learning tab and can be acted on or dismissed. At autonomy level 3, JARVIS will pursue high-priority candidates automatically.

---

## Morning Briefing & Daily Digest

- **08:00 daily** — JARVIS writes a forward-looking morning brief to `~/jarvis/reports/morning/<date>.md`, covering the health score, the lowest-scoring component, and the top 2 actionable suggestions.
- **19:00 daily** — An evening digest summarises what happened during the day (events, tasks completed, knowledge saved).

Both are accessible from the Dashboard and published as bus events so they appear in the live feed.

---

## Developer Utilities

| Script | Purpose |
|---|---|
| `scripts/b5_functional_refresh.py` | Full functional test harness — drives every endpoint, tool, and bot through the live app and writes a dated Markdown report |
| `scripts/diagnose_lm_block.py` | Diagnoses why LM Studio is unreachable or blocked |
| `scripts/jarvis_status.py` | Quick CLI health summary |
| `scripts/perf_bench.py` | Latency benchmarks for LM, ChromaDB, and WebSocket |
| `scripts/orphan_glob_audit.py` | Finds glob readers with no matching writer in source (catches dead-feature bugs) |

---

## Running Tests

```bash
cd ~/jarvis
venv/bin/pytest agent/tests/ -v
```

Tests cover autonomy dispatch, goal decay, kill-switch, aliveness, drive/emotion systems, bus taxonomy, config, curiosity outcomes, daily digest, FIM completer, learning tracks, LM progress, LM Studio client, offline guards, onboarding, orchestrator resilience, performance bench, personality cards, reflection, response style, sessions, and style learner.

---

## Project Layout (full)

```
Jarvis/
├── main.py                          # App entrypoint
├── pyproject.toml                   # Pytest config
├── requirements.txt                 # Python dependencies
├── agent/
│   ├── api/
│   │   ├── agents.py                # Agent orchestration router
│   │   ├── aliveness.py             # Aliveness / morning briefing router
│   │   ├── analytics.py             # Usage analytics router
│   │   ├── autonomy.py              # Autonomy level + drives router
│   │   ├── bots.py                  # Bot trigger + status router
│   │   ├── brain.py                 # Brain vault + memory router
│   │   ├── chat.py                  # Chat + SSE events router
│   │   ├── feedback.py              # Turn feedback (stop/copy signals) router
│   │   ├── plugins.py               # Plugin management router
│   │   ├── system.py                # Health, audit, fs, logs, bus router
│   │   └── voice.py                 # Voice input/output router
│   ├── core/
│   │   ├── autonomy.py              # Autonomy daemon + periodic registry
│   │   ├── bus.py                   # Internal pub/sub event bus
│   │   ├── config.py                # Config schema + loader
│   │   ├── curiosity.py             # Curiosity engine + candidate lifecycle
│   │   ├── drives.py                # Drive accumulation + threshold notifications
│   │   ├── gateway.py               # Main chat gateway (tool loop + SSE)
│   │   ├── lm_studio.py             # Async LM Studio client
│   │   ├── memory.py                # ChromaDB RAG + LTM
│   │   ├── personality_traits.py    # Emotion system + mood bus events
│   │   ├── sandbox.py               # bubblewrap sandboxed execution
│   │   ├── swarm.py                 # Multi-agent swarm orchestration
│   │   ├── tool_registry.py         # Tool definitions + safety tiers
│   │   └── ...                      # (30+ additional core modules)
│   ├── bots/
│   │   ├── code_health.py
│   │   ├── homelab_warden.py
│   │   ├── knowledge_curator.py
│   │   ├── memory_gardener.py
│   │   └── performance_watchdog.py
│   ├── aliveness/
│   │   ├── brain_co_ownership.py
│   │   ├── morning_briefing.py
│   │   ├── notifier.py
│   │   └── skill_distiller.py
│   ├── tools/
│   │   └── gui_tools.py             # Screenshot + AT-SPI2 GUI automation
│   └── tests/                       # pytest test suite (25 files)
├── plugins/
│   ├── app_launcher/
│   ├── compose_doctor/
│   ├── docs_ingest/
│   ├── git/
│   ├── memory_recall/
│   ├── process_monitor/
│   ├── system_info/
│   └── web_search/
├── scripts/
│   ├── b5_functional_refresh.py
│   ├── diagnose_lm_block.py
│   ├── jarvis_status.py
│   ├── orphan_glob_audit.py
│   └── perf_bench.py
└── jarvis-ui/
    ├── src/
    │   ├── App.tsx                  # Root component + routing
    │   ├── modes/                   # chat, coder, brain, dashboard, bots, …
    │   ├── components/              # Sidebar, MoodRibbon, ConfirmModal, …
    │   ├── hooks/                   # useLiveWS, usePolling, useTheme, …
    │   └── utils/                   # format, motion helpers
    └── src-tauri/                   # Rust Tauri shell
```

---

## License

Private. All rights reserved.
