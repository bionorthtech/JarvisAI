# Project Description

**Short (GitHub repo description — 1 sentence):**
> A fully local, privacy-first AI assistant and coding agent with a second brain, autonomous bots, emotional drives, and a Tauri desktop app — powered by LM Studio and ChromaDB, zero cloud required.

---

**Medium (README header / About section — 3 sentences):**
> JARVIS is a self-hosted AI agent that runs entirely on your machine. It combines a FastAPI backend with a Tauri + React desktop app to give you a persistent coding assistant, an Obsidian-compatible second brain, a homelab warden, and a four-level autonomy system — from "respond when asked" to "pursue its own goals while you sleep." Everything runs over LM Studio and ChromaDB — no API keys, no telemetry, no data leaving your hardware.

---

**Long (blog post / product page):**
> JARVIS is a fully local AI assistant built for developers who want their agent to actually know them. It runs a FastAPI backend connected to LM Studio and ChromaDB, giving you streaming chat with tool-calling, sandboxed shell execution, fill-in-the-middle code completion, and a RAG-powered second brain that grows with every conversation.
>
> The **Coder pane** gives you a full file-tree browser, syntax-highlighted editing, and an App Launcher that can open any system application directly from chat. The **Brain pane** is an Obsidian-compatible vault with a capture inbox, daily auto-generated notes, semantic RAG search across 1,000+ notes, and a live graph of backlink connections. The **Dashboard** streams live telemetry — a health ring scoring code quality, memory freshness, and drive saturation; a 24-hour event report; and an Internal State panel showing JARVIS's emotion dimensions and real-time thought stream.
>
> The autonomy system has four levels: at level 0 it does nothing unless you ask; at level 3 it runs scheduled bots, pursues standing goals, and dispatches tasks driven by its own curiosity, maintenance, and learning drives. Five emotion dimensions — curiosity, focus, frustration, satisfaction, and boredom — compound into moods that shape how JARVIS talks and when it speaks up. Five autonomous **Bots** (Memory Gardener, Code Health Monitor, Performance Watchdog, Knowledge Curator, Homelab Warden) keep your system healthy in the background, each with a manual "Run now" trigger and a full report log. An extensible plugin system adds git, process monitoring, app launching, Docker Compose linting, docs ingestion, and optional web search. Every action is logged to an auditable SQLite chain — SAFE tools run instantly, DANGER tools pause for a confirmation modal with an impact preview before executing.

---

## Screenshot captions

| File | Caption |
|---|---|
| `Screenshot_2026-05-21_20-42-18.png` | **Chat** — Main conversational interface showing JARVIS online, the full sidebar, streaming input bar, and the Notifier surfacing JARVIS's internal monologue. |
| `Screenshot_2026-05-21_20-43-01.png` | **Brain** — Overview tab showing the capture widget, today's daily note, Ask the Brain RAG search, and vault stats (1,397 notes, 265 edges). |
| `Screenshot_2026-05-21_20-43-43.png` | **Dashboard / Mission Control** — Live health ring (81 — Watchful), JARVIS Health bars, 24h report, and system status tiles streaming from the event bus. |
| `Screenshot_2026-05-21_20-43-57.png` | **Dashboard / Internal State** — Emotion dimensions, drive levels (Learning at 48%), JARVIS Wants panel, and the live Thought Stream. |
| `Screenshot_2026-05-21_20-44-19.png` | **Bots** — All 5 scheduled bots with their cadence, last-run status, autonomy gate, and manual Run now buttons. |
