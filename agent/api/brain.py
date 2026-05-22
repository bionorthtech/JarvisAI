"""/brain, /memory, /vault, /obsidian, /verify, /snapshot router.

The "knowledge surface" — second-brain vault (capture, daily, ingest,
graph, ask, search, backlinks, update), ChromaDB long-term memory + RAG,
encrypted secrets vault, Obsidian launcher, code verifier, restic
snapshot status.

URLs unchanged from pre-split main.py.
"""
from __future__ import annotations
import asyncio
import importlib
import importlib.util
import json
import os
import re as _re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agent.core import bus, memory, verifier

router = APIRouter(tags=["brain"])


# ── Obsidian launcher ────────────────────────────────────────────────────────

@router.post("/obsidian/open")
async def obsidian_open(path: str = Query(default="")):
    """Launch the Obsidian Flatpak app, optionally opening a specific note."""
    cmd = ["flatpak", "run", "md.obsidian.Obsidian"]
    vault = Path.home() / ".jarvis" / "brain"
    if path:
        note = vault / path if not path.startswith("/") else Path(path)
        cmd += [str(note)]
    else:
        cmd += [str(vault)]
    try:
        subprocess.Popen(cmd, start_new_session=True)
        return {"launched": True, "vault": str(vault)}
    except Exception as e:
        return {"launched": False, "error": str(e)}


# ── ChromaDB memory ──────────────────────────────────────────────────────────

@router.get("/memory/stats")
async def memory_stats(project: str = Query(default="default")):
    return await asyncio.to_thread(memory.get_stats, project)


@router.get("/memory/search")
async def memory_search(
    q: str = Query(...),
    project: str = Query(default="default"),
    n: int = Query(default=5),
):
    file_hits, chat_hits = await asyncio.gather(
        asyncio.to_thread(memory.search_files, q, project, n),
        asyncio.to_thread(memory.search_chat, q, project, n),
    )
    return {"files": file_hits, "chat": chat_hits}


# ── Long-term memory (LTM) ───────────────────────────────────────────────────

class LTMRequest(BaseModel):
    content: str
    tags: list = []


@router.post("/memory/ltm")
async def add_ltm(req: LTMRequest):
    """Add a fact to Long-Term Memory."""
    await asyncio.to_thread(memory.add_to_ltm, req.content, req.tags)
    return {"ok": True}


@router.get("/memory/ltm/search")
async def search_ltm(query: str = Query(...), n: int = Query(default=5)):
    """Search Long-Term Memory."""
    results = await asyncio.to_thread(memory.search_ltm, query, n)
    return {"results": results}


@router.get("/memory/ltm/stats")
async def ltm_stats():
    return memory.ltm_stats()


# ── Code verifier ────────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    code: str
    language: str = "auto"


@router.post("/verify")
async def verify_code(req: VerifyRequest):
    """Verify code safety before execution."""
    result = verifier.check(req.code, req.language)
    return {"verdict": result.verdict, "reason": result.reason, "line": result.line}


# ── Second brain — discovery + scan ──────────────────────────────────────────

@router.post("/brain/scan")
async def brain_scan_endpoint(sections: list[str] | None = None):
    """Trigger a full or partial second-brain scan."""
    import plugins.second_brain.plugin as _sb
    importlib.reload(_sb)
    return await asyncio.to_thread(_sb.brain_scan, sections)


@router.get("/brain/status")
async def brain_status_endpoint():
    import plugins.second_brain.plugin as _sb
    importlib.reload(_sb)
    return _sb.brain_status()


@router.get("/brain/query")
async def brain_query_endpoint(q: str = Query(...), n: int = Query(default=5)):
    from plugins.second_brain.plugin import brain_query
    return await asyncio.to_thread(brain_query, q, n)


# ── Second brain v2 — capture / daily / ingest / link / ask ──────────────────

class BrainCaptureBody(BaseModel):
    text: str
    source: str = "manual"
    tags: list[str] = []
    title: str | None = None


class BrainAskBody(BaseModel):
    question: str
    n: int = 6


class BrainIngestBody(BaseModel):
    path_or_url: str
    tags: list[str] = []


class BrainIngestDirBody(BaseModel):
    directory: str
    glob: str = "**/*"
    tags: list[str] = []


class BrainAppendBody(BaseModel):
    text: str
    section: str = "Notes"


class BrainInsertLinksBody(BaseModel):
    name: str
    links: list[str]


class BrainApplyTagsBody(BaseModel):
    name: str
    tags: list[str]


@router.post("/brain/capture")
async def brain_capture_endpoint(body: BrainCaptureBody):
    from plugins.second_brain.plugin import brain_capture
    return await asyncio.to_thread(
        brain_capture, body.text, body.source, body.tags, body.title
    )


@router.get("/brain/inbox")
async def brain_inbox_endpoint(limit: int = Query(default=20)):
    from plugins.second_brain.plugin import brain_inbox_list
    return await asyncio.to_thread(brain_inbox_list, limit)


@router.post("/brain/inbox/{name:path}/archive")
async def brain_inbox_archive_endpoint(name: str):
    from plugins.second_brain.plugin import brain_inbox_archive
    return await asyncio.to_thread(brain_inbox_archive, name)


@router.get("/brain/today")
async def brain_today_endpoint():
    from plugins.second_brain.plugin import brain_today
    return await asyncio.to_thread(brain_today)


@router.post("/brain/today/append")
async def brain_today_append_endpoint(body: BrainAppendBody):
    from plugins.second_brain.plugin import brain_today_append
    return await asyncio.to_thread(brain_today_append, body.text, body.section)


@router.get("/brain/daily")
async def brain_daily_endpoint(days: int = Query(default=7)):
    from plugins.second_brain.plugin import brain_daily_list
    return await asyncio.to_thread(brain_daily_list, days)


@router.post("/brain/ingest")
async def brain_ingest_endpoint(body: BrainIngestBody):
    from plugins.second_brain.plugin import brain_ingest
    return await asyncio.to_thread(brain_ingest, body.path_or_url, body.tags)


@router.post("/brain/ingest/dir")
async def brain_ingest_dir_endpoint(body: BrainIngestDirBody):
    from plugins.second_brain.plugin import brain_ingest_dir
    return await asyncio.to_thread(
        brain_ingest_dir, body.directory, body.glob, body.tags
    )


@router.get("/brain/similar/{name:path}")
async def brain_similar_endpoint(name: str, n: int = Query(default=5)):
    from plugins.second_brain.plugin import brain_similar
    return await asyncio.to_thread(brain_similar, name, n)


@router.get("/brain/suggest_links/{name:path}")
async def brain_suggest_links_endpoint(name: str, n: int = Query(default=5)):
    from plugins.second_brain.plugin import brain_suggest_links
    return await asyncio.to_thread(brain_suggest_links, name, n)


@router.get("/brain/suggest_tags/{name:path}")
async def brain_suggest_tags_endpoint(name: str, n: int = Query(default=5)):
    """B6.5 — LM-suggest tags for a note (frontmatter `tags:` list)."""
    from plugins.second_brain.plugin import brain_suggest_tags
    return await asyncio.to_thread(brain_suggest_tags, name, n)


@router.post("/brain/apply_tags")
async def brain_apply_tags_endpoint(body: BrainApplyTagsBody):
    """B6.5 — merge `tags` into the note's frontmatter tags list (idempotent)."""
    from plugins.second_brain.plugin import brain_apply_tags
    return await asyncio.to_thread(brain_apply_tags, body.name, body.tags)


@router.post("/brain/insert_links")
async def brain_insert_links_endpoint(body: BrainInsertLinksBody):
    from plugins.second_brain.plugin import brain_insert_links
    return await asyncio.to_thread(brain_insert_links, body.name, body.links)


@router.get("/brain/graph")
async def brain_graph_endpoint():
    from plugins.second_brain.plugin import brain_graph_stats
    return await asyncio.to_thread(brain_graph_stats)


@router.get("/brain/graph_data")
async def brain_graph_data_endpoint(include_isolated: bool = Query(default=True)):
    """Nodes + edges for the force-directed graph view in the Brain tab."""
    from plugins.second_brain.plugin import brain_graph_data
    return await asyncio.to_thread(brain_graph_data, include_isolated)


@router.post("/brain/ask")
async def brain_ask_endpoint(body: BrainAskBody):
    from plugins.second_brain.plugin import brain_ask
    return await asyncio.to_thread(brain_ask, body.question, body.n)


@router.post("/brain/reindex")
async def brain_reindex_endpoint():
    from plugins.second_brain.plugin import brain_reindex
    return await asyncio.to_thread(brain_reindex)


@router.get("/brain/vault_stats")
async def brain_vault_stats_endpoint():
    from plugins.second_brain.plugin import brain_vault_stats
    return await asyncio.to_thread(brain_vault_stats)


# ── Vault file operations (note CRUD) ────────────────────────────────────────

def _load_vault_module(spec_id: str = "_brain_vault"):
    spec = importlib.util.spec_from_file_location(
        spec_id, Path.home() / "jarvis/plugins/second_brain/vault.py"
    )
    vmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vmod)
    return vmod


@router.get("/brain/list")
async def brain_list_endpoint(subdir: str = Query(default="")):
    """List notes in a vault subdir. Returns relative names + size + mtime."""
    vmod = _load_vault_module()
    notes = vmod.list_notes(subdir)
    items = [{
        "name": str(p.relative_to(vmod.VAULT))[:-3],
        "size": p.stat().st_size,
        "mtime": p.stat().st_mtime,
    } for p in notes]
    return {"subdir": subdir, "items": sorted(items, key=lambda i: -i["mtime"])}


@router.get("/brain/note/{name:path}")
async def brain_note_endpoint(name: str):
    """Read one note by relative name (no .md). Returns frontmatter + body."""
    vmod = _load_vault_module()
    try:
        text = vmod.read_note(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"note not found: {name}")
    fm, body = vmod.split_frontmatter(text)
    return {"name": name, "frontmatter": fm, "body": body, "raw": text,
            "wikilinks": vmod.find_wikilinks(text)}


class BrainUpdateBody(BaseModel):
    content: str


@router.get("/brain/search")
async def brain_search_endpoint(q: str = Query(...), subdir: str = Query(default="")):
    """Hybrid brain search: filename match + grep content. Returns ranked list."""
    vmod = _load_vault_module()
    vault_root = vmod.VAULT
    search_root = vault_root / subdir if subdir else vault_root
    q_lower = q.lower()
    results = []
    try:
        for f in sorted(search_root.rglob("*.md")):
            rel = str(f.relative_to(vault_root))
            score = 0
            preview = ""
            if q_lower in f.stem.lower():
                score += 10
            try:
                text = f.read_text(errors="replace")
                if q_lower in text.lower():
                    score += 5
                    idx = text.lower().find(q_lower)
                    preview = text[max(0, idx - 40):idx + 120].replace("\n", " ").strip()
            except Exception:
                pass
            if score > 0:
                results.append({"name": rel.replace(".md", ""), "path": str(f),
                                 "preview": preview, "score": score})
        results.sort(key=lambda x: -x["score"])
    except Exception as e:
        return {"results": [], "error": str(e)}
    return {"results": results[:30], "count": len(results)}


@router.get("/brain/backlinks/{name:path}")
async def brain_backlinks_endpoint(name: str):
    """Find all notes that link to this note via [[name]] wikilink syntax."""
    vmod = _load_vault_module()
    vault_root = vmod.VAULT
    stem = Path(name).stem
    pattern = _re.compile(r"\[\[" + _re.escape(stem) + r"(?:\|[^\]]+)?\]\]", _re.IGNORECASE)
    backlinks = []
    for f in sorted(vault_root.rglob("*.md")):
        try:
            text = f.read_text(errors="replace")
            if pattern.search(text):
                rel = str(f.relative_to(vault_root)).replace(".md", "")
                backlinks.append(rel)
        except Exception:
            pass
    return {"name": name, "backlinks": backlinks, "count": len(backlinks)}


@router.post("/brain/update/{name:path}")
async def brain_update_endpoint(name: str, body: BrainUpdateBody):
    """Write (create or overwrite) a brain note by relative path."""
    vmod = _load_vault_module()
    vault_root = vmod.VAULT
    target = vault_root / (name if name.endswith(".md") else name + ".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content)
    return {"ok": True, "path": str(target), "name": name, "bytes": len(body.content)}


@router.delete("/brain/note/{name:path}")
async def brain_delete_endpoint(name: str):
    """Delete a brain note by relative path."""
    vmod = _load_vault_module()
    vault_root = vmod.VAULT
    target = vault_root / (name if name.endswith(".md") else name + ".md")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"note not found: {name}")
    target.unlink()
    return {"ok": True, "deleted": name}


# ── Encrypted secrets vault (5.5) ────────────────────────────────────────────

from cryptography.fernet import Fernet, InvalidToken

_VAULT_FILE = Path.home() / ".jarvis" / "vault.enc"
_VAULT_KEY_FILE = Path.home() / ".jarvis" / ".vault_key"


def _get_vault_key() -> bytes:
    """Get or create the vault encryption key (mode 600)."""
    _VAULT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _VAULT_KEY_FILE.exists():
        return _VAULT_KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    _VAULT_KEY_FILE.write_bytes(key)
    _VAULT_KEY_FILE.chmod(0o600)
    return key


def _load_vault() -> dict:
    try:
        if _VAULT_FILE.exists():
            f = Fernet(_get_vault_key())
            return json.loads(f.decrypt(_VAULT_FILE.read_bytes()).decode())
    except (InvalidToken, Exception):
        pass
    return {}


def _save_vault(data: dict) -> None:
    f = Fernet(_get_vault_key())
    _VAULT_FILE.write_bytes(f.encrypt(json.dumps(data).encode()))
    _VAULT_FILE.chmod(0o600)


class VaultSecretBody(BaseModel):
    key: str
    value: str


@router.get("/vault/keys")
async def vault_list_keys():
    """List secret names in the vault (values never returned)."""
    vault = await asyncio.to_thread(_load_vault)
    return {"keys": list(vault.keys())}


@router.post("/vault/secret")
async def vault_set_secret(body: VaultSecretBody):
    """Store an encrypted secret in the vault."""
    vault = await asyncio.to_thread(_load_vault)
    vault[body.key] = body.value
    await asyncio.to_thread(_save_vault, vault)
    bus.publish("vault.secret_stored", "vault", {"key": body.key})
    return {"ok": True, "key": body.key}


@router.delete("/vault/secret/{key}")
async def vault_delete_secret(key: str):
    """Remove a secret from the vault."""
    vault = await asyncio.to_thread(_load_vault)
    removed = key in vault
    vault.pop(key, None)
    await asyncio.to_thread(_save_vault, vault)
    return {"ok": removed, "key": key}


@router.get("/vault/audit")
async def vault_audit():
    """Audit trail: is the vault file present, key accessible, chain intact?"""
    from agent.core import audit
    chain_ok, chain_msg = audit.verify_chain()
    return {
        "vault_exists": _VAULT_FILE.exists(),
        "key_exists": _VAULT_KEY_FILE.exists(),
        "audit_chain_ok": chain_ok,
        "audit_chain_msg": chain_msg,
    }


# ── Restic snapshot (5.8) ────────────────────────────────────────────────────

@router.get("/snapshot/status")
async def snapshot_status():
    """Check restic backup status (5.8 Encrypted Memory Snapshots)."""
    restic_bin = shutil.which("restic")
    password_file = Path.home() / ".restic.pass"
    repo_env = os.environ.get("RESTIC_REPOSITORY", "")
    return {
        "restic_installed": bool(restic_bin),
        "password_configured": password_file.exists(),
        "repository_env_set": bool(repo_env),
        "backup_script": str(Path.home() / "jarvis" / "security" / "restic-backup.sh"),
        "jarvis_data_dirs": [
            str(Path.home() / ".jarvis"),
            str(Path.home() / "jarvis"),
        ],
        "note": "Run security/restic-backup.sh to create encrypted snapshot. "
                "Set RESTIC_REPOSITORY env var first.",
    }
