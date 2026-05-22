# JARVIS — Project Descriptions

---

## GitHub repo description (160 chars max)

> Fully local AI assistant & coding agent. LM Studio + ChromaDB + Tauri desktop app. Second brain, autonomous bots, emotional drives. Zero cloud, zero telemetry.

---

## Short bio / tagline (1 sentence)

> JARVIS is a fully local AI assistant and coding agent with a second brain, autonomous bots, and emotional drives — powered by LM Studio and ChromaDB, no cloud required.

---

## Medium — About section (3–4 sentences)

> JARVIS is a self-hosted AI agent that runs entirely on your machine. It pairs a FastAPI backend with a Tauri + React desktop app to give you a persistent coding assistant, an Obsidian-compatible second brain, a homelab monitor, and a four-level autonomy system — from "respond when asked" to "pursue goals while you sleep." Five emotion dimensions and three accumulation-based drives shape how JARVIS behaves and when it acts on its own. Everything runs over LM Studio and ChromaDB — no API keys, no telemetry, no data ever leaving your hardware.

---

## Long — Blog / product page

JARVIS is a fully local AI assistant built for developers who want their agent to actually know them over time.

**The core stack** is a FastAPI backend connected to LM Studio and ChromaDB, wrapped in a Tauri + React desktop app. You get streaming chat with live tool-call cards, sandboxed shell execution, fill-in-the-middle code completion, and a RAG-powered second brain that indexes everything you've ever worked on.

**The Brain pane** is an Obsidian-compatible Markdown vault with a capture inbox, auto-generated daily notes, semantic search across thousands of notes, backlink tracking, and a force-directed graph of your knowledge network. Ask the Brain a question and JARVIS searches semantically then synthesises an answer from your own notes.

**The Dashboard** streams live telemetry — a health ring scoring code quality, memory freshness, and drive saturation; a 24-hour event report; system status tiles; and an Internal State panel showing JARVIS's emotion dimensions (Curiosity, Focus, Frustration, Satisfaction, Boredom), its drive levels, and a real-time thought stream.

**The autonomy system** has four levels. At level 0 it does nothing unless you ask. At level 3 it runs scheduled bots, pursues standing goals, and dispatches tasks driven by its own curiosity, maintenance, and learning drives — automatically, between your sessions.

**Five bots** (Memory Gardener, Code Health Monitor, Performance Watchdog, Knowledge Curator, Homelab Warden) keep your system healthy in the background. Each has a manual trigger and writes a dated report. **Eight plugins** extend what JARVIS can do as tools: git, process monitoring, app launching, Docker Compose linting, docs ingestion, and optional web search.

Every action is classified before it runs — SAFE tools execute instantly, DANGER tools pause for a confirmation modal with an impact preview, and everything is logged to an auditable SQLite chain you can verify at any time.

---

## Screenshot captions

| File | Caption |
|---|---|
| `Screenshot_2026-05-21_20-42-18.png` | **Chat** — Main interface showing JARVIS online, the full sidebar navigation, streaming input bar, and the Notifier surfacing JARVIS's internal monologue. |
| `Screenshot_2026-05-21_20-43-01.png` | **Brain** — Overview showing the capture widget, today's auto-generated daily note, Ask the Brain RAG search, and vault stats (1,397 notes, 265 edges). |
| `Screenshot_2026-05-21_20-43-43.png` | **Dashboard / Mission Control** — Live health ring (81 — Watchful), Code Health / Memory Garden / Drives bars, 24h report, and system status tiles streaming from the event bus. |
| `Screenshot_2026-05-21_20-43-57.png` | **Dashboard / Internal State** — Emotion dimensions, drive levels (Learning at 48%), JARVIS Wants panel, and the live Thought Stream showing JARVIS planning a brain note. |
| `Screenshot_2026-05-21_20-44-19.png` | **Bots** — All 5 scheduled bots with cadence, last-run status, autonomy gate (NEEDS L1), and manual Run now buttons. |
