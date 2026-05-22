"""
JARVIS Memory — ChromaDB-backed RAG store.
Persists file contents and conversation turns, searchable per project.
"""
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

# chromadb 1.0.x ships a posthog telemetry call that's incompatible with
# its bundled posthog version — every collection op spams a warning.
# Telemetry is already disabled via Settings below, but the failed-send
# warning still fires before that gate. Silence the logger upfront.
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)
logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

logger = logging.getLogger("jarvis.memory")

_DATA_DIR = Path.home() / ".jarvis" / "memory"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_ef = embedding_functions.DefaultEmbeddingFunction()
_client: Optional[chromadb.PersistentClient] = None


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=str(_DATA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _sanitize(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    safe = safe.strip("_-") or "default"
    return safe[:60]


def _files_col(project: str):
    return _get_client().get_or_create_collection(
        f"f_{_sanitize(project)}", embedding_function=_ef
    )


def _chat_col(project: str):
    return _get_client().get_or_create_collection(
        f"c_{_sanitize(project)}", embedding_function=_ef
    )


# ── Write ──────────────────────────────────────────────────────────────────────

def add_file(path: str, content: str, project: str = "default") -> None:
    col = _files_col(project)
    doc_id = hashlib.sha256(path.encode()).hexdigest()[:16]
    lines = content.splitlines()
    chunk_size = 100

    # Remove existing chunks for this file
    try:
        existing = col.get(where={"path": path})
        if existing["ids"]:
            col.delete(ids=existing["ids"])
    except Exception:
        pass

    chunks, ids, metas = [], [], []
    for i in range(0, max(1, len(lines)), chunk_size):
        chunk = "\n".join(lines[i : i + chunk_size])
        if not chunk.strip():
            continue
        chunks.append(chunk)
        ids.append(f"{doc_id}_{i}")
        metas.append({
            "path": path,
            "start_line": i + 1,
            "end_line": min(i + chunk_size, len(lines)),
            "ts": int(time.time()),
        })

    if chunks:
        col.add(documents=chunks, ids=ids, metadatas=metas)
        logger.debug("memory: stored %d chunks for %s in project %s", len(chunks), path, project)


def add_interaction(user_msg: str, assistant_msg: str, project: str = "default") -> None:
    col = _chat_col(project)
    doc_id = hashlib.sha256(f"{time.time()}{user_msg[:50]}".encode()).hexdigest()[:16]
    col.add(
        documents=[f"USER: {user_msg[:500]}\nJARVIS: {assistant_msg[:500]}"],
        ids=[doc_id],
        metadatas=[{"ts": int(time.time()), "user": user_msg[:200]}],
    )


# ── Read ───────────────────────────────────────────────────────────────────────

def _record_chroma(latency_ms: float) -> None:
    """G3.3 — feed perf watchdog. Best-effort, never raises."""
    try:
        from agent.bots.performance_watchdog import watchdog
        watchdog.record_chroma_latency(latency_ms)
    except Exception:
        pass


def prewarm(project: str = "default") -> dict:
    """2A — Warm the ChromaDB client + embedding function before the
    first real query.

    Cold-cache: the embedding model loads on first call (~200ms+ on
    qwen-class hardware), the HNSW index loads from disk lazily,
    persistent client opens its SQLite/duckdb. Without prewarm, the
    first user query pays that cost — perf_bench saw cold p95 of 854ms
    drop to warm 390ms.

    Cheap: one count() on the default project's collections + one
    1-result query against ltm. Total under 500ms on a typical box.
    Safe to call multiple times (no-ops after the client is built).
    """
    import time as _t
    t0 = _t.monotonic()
    try:
        # Build the client + ensure both per-project collections exist.
        files = _files_col(project)
        chat = _chat_col(project)
        ltm = _ltm_col()
        # Issue a tiny n=1 query against each non-empty collection so
        # the ONNX embedding model is hot regardless of which search
        # path the user hits first (was only warming LTM, which is
        # often empty for new users — first /brain/ask paid the
        # cold-cache cost). count() alone doesn't run inference.
        for col in (files, chat, ltm):
            try:
                if col.count() > 0:
                    col.query(query_texts=["ok"], n_results=1)
            except Exception:
                # Per-collection failure is non-fatal — keep warming the rest.
                continue
        elapsed_ms = (_t.monotonic() - t0) * 1000
        logger.info("chromadb prewarm complete in %.0fms", elapsed_ms)
        return {"ok": True, "ms": round(elapsed_ms, 1)}
    except Exception as e:
        logger.warning("chromadb prewarm failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}


def search_files(query: str, project: str = "default", n: int = 5) -> list[dict]:
    import time as _t
    t0 = _t.monotonic()
    try:
        col = _files_col(project)
        count = col.count()
        if count == 0:
            return []
        r = col.query(query_texts=[query], n_results=min(n, count))
        return [
            {"content": doc, "path": meta.get("path", ""), "distance": dist, "meta": meta}
            for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0])
        ]
    except Exception as e:
        logger.debug("memory search_files error: %s", e)
        return []
    finally:
        _record_chroma((_t.monotonic() - t0) * 1000)


def search_chat(query: str, project: str = "default", n: int = 3) -> list[dict]:
    try:
        col = _chat_col(project)
        count = col.count()
        if count == 0:
            return []
        r = col.query(query_texts=[query], n_results=min(n, count))
        return [
            {"content": doc, "distance": dist, "ts": meta.get("ts", 0)}
            for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0])
        ]
    except Exception as e:
        logger.debug("memory search_chat error: %s", e)
        return []


def get_stats(project: str = "default") -> dict:
    try:
        return {
            "project": project,
            "file_chunks": _files_col(project).count(),
            "chat_turns": _chat_col(project).count(),
        }
    except Exception:
        return {"project": project, "file_chunks": 0, "chat_turns": 0}


# ── Long-Term Memory (cross-project, permanent) ────────────────────────────────

def _ltm_col():
    return _get_client().get_or_create_collection("ltm_global", embedding_function=_ef)


def add_to_ltm(content: str, tags: list = None) -> None:
    """Store a fact/insight in Long-Term Memory. Persists across all projects."""
    col = _ltm_col()
    doc_id = hashlib.sha256(f"{time.time()}{content[:50]}".encode()).hexdigest()[:16]
    col.add(
        documents=[content],
        ids=[doc_id],
        metadatas=[{"ts": int(time.time()), "tags": ",".join(tags or [])}],
    )
    logger.debug("ltm: stored fact (tags=%s)", tags)


def search_ltm(query: str, n: int = 3) -> list[dict]:
    """Search Long-Term Memory for relevant facts/insights."""
    try:
        col = _ltm_col()
        count = col.count()
        if count == 0:
            return []
        r = col.query(query_texts=[query], n_results=min(n, count))
        return [
            {"content": doc, "distance": dist, "ts": meta.get("ts", 0)}
            for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0])
        ]
    except Exception as e:
        logger.debug("ltm search error: %s", e)
        return []


def ltm_stats() -> dict:
    try:
        return {"ltm_entries": _ltm_col().count()}
    except Exception:
        return {"ltm_entries": 0}
