"""
Trust tier classification and confirmation registry.
Every tool call is classified before execution.
DANGER/CRITICAL tiers require explicit user approval via the frontend modal.
"""
import asyncio
import uuid
from typing import Dict, Optional
from dataclasses import dataclass, field

# ── Trust tier map ────────────────────────────────────────────────────────────

TOOL_TIERS: Dict[str, str] = {
    # Filesystem
    "read_file": "SAFE",
    "list_directory": "SAFE",
    "grep_files": "SAFE",
    "write_file": "CAUTION",
    "run_shell": "DANGER",
    "run_sandboxed": "CAUTION",       # sandboxed — lower risk than run_shell
    # GUI
    "take_screenshot": "SAFE",
    "mouse_move": "SAFE",
    "key_press": "CAUTION",
    "type_text": "DANGER",
    "mouse_click": "DANGER",
    # Knowledge (all local/offline)
    "kiwix_search": "SAFE",
    "kiwix_get_article": "SAFE",
    "kiwix_status": "SAFE",
    "obsidian_search": "SAFE",
    "obsidian_read_note": "SAFE",
    "obsidian_list_notes": "SAFE",
    "obsidian_index_vault": "SAFE",
    "obsidian_create_note": "CAUTION",
    # Git
    "git_status": "SAFE",
    "git_diff": "SAFE",
    "git_log": "SAFE",
    "git_branch": "SAFE",
    "git_commit": "CAUTION",
    "git_checkout": "CAUTION",
    # System
    "process_list": "SAFE",
    "process_info": "SAFE",
    "system_resources": "SAFE",
    "network_connections": "SAFE",
    "system_hardware": "SAFE",
    "system_storage": "SAFE",
    "sandbox_info": "SAFE",
    "process_stop": "DANGER",
    # App control
    "app_launch": "CAUTION",
    "app_open_file": "CAUTION",
    "app_list_windows": "SAFE",
    # Docs / memory
    "ingest_file": "SAFE",
    "ingest_directory": "SAFE",
    "memory_stats": "SAFE",
    "memory_recall": "SAFE",
    # Web (internet-gated)
    "web_search": "CAUTION",
    # Second Brain
    "brain_scan":        "SAFE",
    "brain_status":      "SAFE",
    "brain_query":       "SAFE",
    "brain_note":        "SAFE",
    "brain_update_note": "CAUTION",
    "brain_task_add":    "CAUTION",
    "brain_task_list":   "SAFE",
    "brain_task_run":    "DANGER",
}


def get_tier(tool_name: str) -> str:
    return TOOL_TIERS.get(tool_name, "CAUTION")


def needs_confirm(tier: str) -> bool:
    return tier in ("DANGER", "CRITICAL")


def describe_action(tool_name: str, args: dict) -> str:
    if tool_name == "run_shell":
        return f"Run shell command: `{args.get('command', '')}`"
    if tool_name == "write_file":
        return f"Write to file: `{args.get('path', '')}`"
    if tool_name == "type_text":
        return f"Type text: `{args.get('text', '')[:60]}`"
    if tool_name == "mouse_click":
        return f"Click at ({args.get('x', 0)}, {args.get('y', 0)})"
    return f"Execute: {tool_name}({args})"


# ── Dry-run / impact preview (Phase 5.1) ─────────────────────────────────────

def preview_impact(tool_name: str, args: dict) -> Dict[str, str]:
    """
    Build an impact-preview payload for the DANGER/CRITICAL confirmation modal.
    Returns:
        - summary: one-line plain-English description
        - simulated_command: the actual command/signature (for review)
        - dry_run: a non-mutating analog (where possible)
        - affected: rough scope (file path / process / network rule)
        - reversible: "yes" / "no" / "partial"
    """
    out: Dict[str, str] = {
        "summary": describe_action(tool_name, args),
        "simulated_command": "",
        "dry_run": "(no dry-run available — review carefully)",
        "affected": "(unknown)",
        "reversible": "unknown",
    }

    if tool_name == "run_shell":
        cmd = args.get("command", "")
        out["simulated_command"] = cmd
        out["dry_run"] = _shell_dry_run(cmd)
        out["affected"] = _shell_scope(cmd)
        out["reversible"] = _shell_reversibility(cmd)
    elif tool_name == "write_file":
        path = args.get("path", "")
        out["simulated_command"] = f"write {len(args.get('content',''))} bytes → {path}"
        out["dry_run"] = f"ls -la {path} 2>/dev/null || echo '(file does not exist yet)'"
        out["affected"] = path
        out["reversible"] = "yes (backup of prior content recommended)"
    elif tool_name == "process_stop":
        pid = args.get("pid", "?")
        out["simulated_command"] = f"kill {pid}"
        out["dry_run"] = f"ps -p {pid} -o pid,user,cmd"
        out["affected"] = f"PID {pid}"
        out["reversible"] = "no (process must be relaunched)"
    elif tool_name in ("type_text", "mouse_click"):
        out["dry_run"] = "(GUI action — visible on screen, no dry-run)"
        out["affected"] = "active window / focused field"
        out["reversible"] = "no (depends on receiving app)"
    elif tool_name == "brain_task_run":
        out["simulated_command"] = f"brain_task_run {args}"
        out["dry_run"] = "brain_task_list (review before running)"
        out["affected"] = "vault tasks"
        out["reversible"] = "partial"

    return out


def _shell_dry_run(cmd: str) -> str:
    """Best-effort non-destructive analog of a shell command."""
    c = cmd.strip()
    if c.startswith(("rm ", "rm -")):
        targets = [p for p in c.split()[1:] if not p.startswith("-")]
        return f"ls -la {' '.join(targets)}" if targets else "ls -la"
    if c.startswith("mv "):
        parts = c[3:].split()
        target = parts[0] if parts else ""
        return f"ls -la {target} 2>/dev/null"
    if c.startswith("cp "):
        parts = c.split()
        return f"ls -la {parts[1]} 2>/dev/null && ls -la {parts[2]} 2>/dev/null || echo '(target does not exist)'"
    if c.startswith(("apt ", "apt-get ", "sudo apt", "sudo apt-get")):
        return f"{c} --dry-run --simulate" if "--dry-run" not in c else c
    if c.startswith(("dpkg ", "sudo dpkg")):
        return c.replace("dpkg", "dpkg --dry-run --simulate", 1) if "--dry-run" not in c else c
    if c.startswith(("kill ", "pkill ")):
        target = c.split(" ", 1)[1] if " " in c else ""
        return f"ps {target}" if target.startswith("-") else f"ps -p {target}"
    if c.startswith("systemctl"):
        if any(verb in c for verb in (" stop ", " disable ", " restart ", " reload ", " mask ")):
            return c.replace("systemctl", "systemctl status", 1)
    if c.startswith(("git push", "git reset", "git rebase")):
        return "git status && git log --oneline -5"
    return f"(no dry-run available for: {c[:80]})"


def _shell_scope(cmd: str) -> str:
    c = cmd.strip()
    for prefix in ("rm", "mv", "cp", "chmod", "chown", "ln"):
        if c.startswith(prefix + " ") or c.startswith(prefix + " -"):
            parts = [p for p in c.split() if not p.startswith("-")]
            if len(parts) > 1:
                return parts[1]
    if "sudo" in c:
        return "system (requires sudo)"
    return "user-level"


def _shell_reversibility(cmd: str) -> str:
    c = cmd.strip()
    if c.startswith(("rm -rf", "rm -r", "rm ")):
        return "no (deletion)"
    if any(verb in c for verb in ("git push --force", "git reset --hard", "DROP TABLE", "TRUNCATE")):
        return "no (destructive)"
    if c.startswith(("apt purge", "apt remove", "sudo apt purge", "sudo apt remove")):
        return "yes (re-install)"
    if c.startswith(("systemctl stop", "systemctl restart")):
        return "yes (restart service)"
    return "depends on command"


# ── Confirmation registry ─────────────────────────────────────────────────────

@dataclass
class ConfirmRequest:
    id: str
    tool_name: str
    args: dict
    tier: str
    description: str
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    approved: Optional[bool] = None


class ConfirmationRegistry:
    def __init__(self):
        self._pending: Dict[str, ConfirmRequest] = {}

    def create(self, tool_name: str, args: dict, tier: str) -> ConfirmRequest:
        req = ConfirmRequest(
            id=str(uuid.uuid4())[:8],
            tool_name=tool_name,
            args=args,
            tier=tier,
            description=describe_action(tool_name, args),
        )
        self._pending[req.id] = req
        return req

    async def wait(self, req: ConfirmRequest, timeout: float = 120.0) -> bool:
        try:
            await asyncio.wait_for(req._event.wait(), timeout=timeout)
            return bool(req.approved)
        except asyncio.TimeoutError:
            self._pending.pop(req.id, None)
            return False

    def respond(self, confirm_id: str, approved: bool) -> bool:
        req = self._pending.get(confirm_id)
        if not req:
            return False
        req.approved = approved
        req._event.set()
        self._pending.pop(confirm_id, None)
        return True


confirm_registry = ConfirmationRegistry()
