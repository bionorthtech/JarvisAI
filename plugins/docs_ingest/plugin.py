"""
Docs ingest plugin — ingest documents into JARVIS ChromaDB memory.
Supports: PDF, markdown, plain text, source code files.
All offline. No internet required.
"""
from pathlib import Path
import agent.core.memory as memory

_TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".org",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".rb", ".php", ".swift", ".kt",
    ".sh", ".bash", ".zsh", ".fish",
    ".html", ".css", ".scss",
    ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".sql", ".graphql",
    ".dockerfile", ".makefile",
}


def _read_text(path: Path) -> tuple:
    """Returns (content, error). error is None on success."""
    suffix = path.suffix.lower()

    if suffix in _TEXT_EXTENSIONS or suffix == "":
        try:
            return path.read_text(encoding="utf-8", errors="replace"), None
        except OSError as e:
            return "", str(e)

    if suffix == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
            return text, None
        except ImportError:
            return "", "pypdf not installed — run: pip install pypdf"
        except Exception as e:
            return "", f"PDF read error: {e}"

    if suffix == ".docx":
        try:
            import docx
            doc = docx.Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs if p.text)
            return text, None
        except ImportError:
            return "", "python-docx not installed — run: pip install python-docx"
        except Exception as e:
            return "", f"DOCX read error: {e}"

    return "", f"Unsupported type: {suffix}"


async def ingest_file(path: str, project: str = "default") -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"ERROR: File not found: {path}"
    if not p.is_file():
        return f"ERROR: Not a file: {path}"

    content, err = _read_text(p)
    if err:
        return f"ERROR: {err}"
    if not content.strip():
        return f"WARNING: File appears empty: {p.name}"

    memory.add_file(str(p), content, project)
    words = len(content.split())
    return f"Ingested '{p.name}' — {words:,} words → project '{project}'"


async def ingest_directory(
    path: str,
    project: str = "default",
    extensions: str = ".md,.txt,.py,.js,.ts,.rs,.go",
) -> str:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        return f"ERROR: Not a directory: {path}"

    exts = {e.strip().lower() for e in extensions.split(",")}
    ok, skipped, errors = 0, 0, []

    for f in p.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in exts:
            skipped += 1
            continue
        content, err = _read_text(f)
        if err:
            errors.append(f"{f.name}: {err}")
            continue
        if not content.strip():
            skipped += 1
            continue
        memory.add_file(str(f), content, project)
        ok += 1

    lines = [f"Ingested {ok} file(s) from {p.name} → project '{project}'"]
    if skipped:
        lines.append(f"Skipped: {skipped} (wrong type or empty)")
    if errors:
        lines.append(f"Errors ({len(errors)}):")
        lines.extend(f"  {e}" for e in errors[:10])
    return "\n".join(lines)


async def memory_stats(project: str = "default") -> str:
    stats = memory.get_stats(project)
    return (
        f"Memory stats for project '{project}':\n"
        f"  File chunks:  {stats['file_chunks']:,}\n"
        f"  Chat turns:   {stats['chat_turns']:,}"
    )
