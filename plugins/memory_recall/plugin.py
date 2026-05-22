"""Explicit memory recall tool — lets JARVIS search its own long-term memory."""
from agent.core.memory import search_files, search_chat


async def memory_recall(query: str, project: str = "default") -> str:
    file_hits = search_files(query, project=project, n=5)
    chat_hits = search_chat(query, project=project, n=3)

    if not file_hits and not chat_hits:
        return f"No memory found for: '{query}' in project '{project}'"

    lines = [f"Memory results for '{query}' (project: {project}):"]

    if file_hits:
        lines.append("\n=== Files ===")
        for h in file_hits:
            dist = round(h["distance"], 3)
            meta = h.get("meta", {})
            lines.append(f"\n[{h['path']} lines {meta.get('start_line')}-{meta.get('end_line')} | dist={dist}]")
            lines.append(h["content"][:600])

    if chat_hits:
        lines.append("\n=== Past Conversations ===")
        for h in chat_hits:
            dist = round(h["distance"], 3)
            lines.append(f"\n[dist={dist}]")
            lines.append(h["content"][:400])

    return "\n".join(lines)
