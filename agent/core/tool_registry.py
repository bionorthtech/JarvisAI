import difflib
import json
import subprocess
import logging
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Callable, Optional, List
from dataclasses import dataclass

_DIFF_LOG = Path.home() / ".jarvis" / "diff_audit.jsonl"


def _record_diff(path: Path, new_content: str) -> None:
    """Write a unified diff of old→new content to the audit log (1.9)."""
    try:
        old_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if path.exists() else []
        new_lines = new_content.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path.name}", tofile=f"b/{path.name}", lineterm="",
        ))
        if not diff:
            return  # no change — nothing to record
        record = {"id": str(uuid.uuid4()), "ts": time.time(),
                  "path": str(path), "diff": diff}
        _DIFF_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DIFF_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # diff logging is best-effort
from agent.tools.gui_tools import (
    take_screenshot, type_text, key_press, mouse_move, mouse_click,
    GUI_TOOL_SCHEMAS,
)
from agent.core.plugin_loader import discover_plugins
from agent.core.confirmations import TOOL_TIERS
from agent.core.verifier import verifier
from agent.core.sandbox import run_sandboxed

logger = logging.getLogger("jarvis.registry")


@dataclass
class ToolResponse:
    success: bool
    output: str
    error: Optional[str] = None


class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self._schemas: List[Dict[str, Any]] = []
        self._register_builtin_tools()
        self._load_plugins()

    def _register_builtin_tools(self):
        # Filesystem
        self.tools["read_file"] = self.read_file
        self.tools["write_file"] = self.write_file
        self.tools["run_shell"] = self.run_shell
        self.tools["list_directory"] = self.list_directory
        self.tools["grep_files"] = self.grep_files
        self.tools["run_sandboxed"] = self.run_sandboxed_tool
        # GUI automation
        self.tools["take_screenshot"] = take_screenshot
        self.tools["type_text"] = type_text
        self.tools["key_press"] = key_press
        self.tools["mouse_move"] = mouse_move
        self.tools["mouse_click"] = mouse_click

        self._schemas = self._builtin_schemas() + GUI_TOOL_SCHEMAS

    def _load_plugins(self):
        try:
            plugins = discover_plugins()
            for schema, fn, tier in plugins:
                name = schema["function"]["name"]
                self.tools[name] = fn
                self._schemas.append(schema)
                TOOL_TIERS.setdefault(name, tier)
                logger.info("registered plugin tool: %s (tier=%s)", name, tier)
        except Exception as e:
            logger.warning("plugin loading failed: %s", e)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return self._schemas

    def _builtin_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file from the filesystem.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute or home-relative (~) path."}
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file. Creates parent directories as needed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_shell",
                    "description": "Execute a shell command (git, npm, python3, make, etc.) and return output.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"}
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List the contents of a directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep_files",
                    "description": "Search files for a text pattern (like grep -rn). Returns matching lines with file paths and line numbers.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Search pattern (regex supported)"},
                            "path": {"type": "string", "description": "Directory to search", "default": "."},
                            "recursive": {"type": "boolean", "default": True},
                            "file_pattern": {"type": "string", "description": "Glob filter e.g. '*.py'", "default": ""},
                            "ignore_case": {"type": "boolean", "default": False},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_sandboxed",
                    "description": "Run a shell command inside a bubblewrap sandbox — isolated filesystem, no network access. Use for running untrusted or generated code safely.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
                        },
                        "required": ["command"],
                    },
                },
            },
        ]

    async def read_file(self, path: str) -> str:
        p = Path(path).expanduser().resolve()
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return f"ERROR: File not found: {path}"
        except Exception as e:
            return f"ERROR reading file: {e}"

    async def write_file(self, path: str, content: str) -> str:
        p = Path(path).expanduser().resolve()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            # Diff-based change tracking (1.9): capture before/after diff
            _record_diff(p, content)
            p.write_text(content, encoding="utf-8")
            return f"OK: wrote {len(content)} chars to {p}"
        except Exception as e:
            return f"ERROR writing file: {e}"

    async def run_shell(self, command: str) -> str:
        # Verify before execution — REJECT blocks, WARNING proceeds with flag
        check = verifier.check_shell(command)
        if check.verdict == "REJECT":
            return f"⛔ BLOCKED by verifier: {check.reason}\nCommand not executed."
        prefix = f"⚠ WARNING: {check.reason}\n" if check.verdict == "WARNING" else ""
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=60,
            )
            out = proc.stdout.strip()
            err = proc.stderr.strip()
            if proc.returncode == 0:
                return prefix + (out or "(no output)")
            return prefix + f"EXIT {proc.returncode}\n{out}\n{err}".strip()
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out (60s)"
        except Exception as e:
            return f"ERROR: {e}"

    async def grep_files(
        self, pattern: str, path: str = ".", recursive: bool = True,
        file_pattern: str = "", ignore_case: bool = False,
    ) -> str:
        """Grep files in a directory for a pattern."""
        p = Path(path).expanduser().resolve()
        args = ["grep", "-n"]
        if recursive:
            args.append("-r")
        if ignore_case:
            args.append("-i")
        if file_pattern:
            args += ["--include", file_pattern]
        args += [pattern, str(p)]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
            out = proc.stdout.strip()
            if not out:
                return f"No matches for '{pattern}' in {path}"
            lines = out.splitlines()
            if len(lines) > 100:
                out = "\n".join(lines[:100]) + f"\n... ({len(lines) - 100} more matches)"
            return out
        except subprocess.TimeoutExpired:
            return "ERROR: grep timed out"
        except Exception as e:
            return f"ERROR: {e}"

    async def run_sandboxed_tool(self, command: str, timeout: int = 30) -> str:
        """Run a shell command inside a bubblewrap sandbox (no network, isolated filesystem)."""
        check = verifier.check_shell(command)
        if check.verdict == "REJECT":
            return f"⛔ BLOCKED by verifier: {check.reason}"
        return run_sandboxed(command, timeout=timeout)

    async def list_directory(self, path: str) -> str:
        p = Path(path).expanduser().resolve()
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            lines = []
            for e in entries:
                if e.is_dir():
                    lines.append(f"[DIR]  {e.name}/")
                else:
                    try:
                        size = e.lstat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"[FILE] {e.name}  ({size} B)")
            return "\n".join(lines) if lines else "(empty)"
        except FileNotFoundError:
            return f"ERROR: not found: {path}"
        except Exception as e:
            return f"ERROR: {e}"

    async def call(self, name: str, args: Dict[str, Any]) -> str:
        """Invoke a tool by name. Auto-detects sync vs async — plugin tools
        defined with plain `def` are dispatched via asyncio.to_thread so they
        don't block the event loop, while async tools are awaited directly.

        Previously this method unconditionally awaited the result, which
        crashed with 'a coroutine was expected' the moment the LM tried to
        call any sync plugin tool.
        """
        import asyncio
        import inspect

        if name not in self.tools:
            return f"ERROR: unknown tool '{name}'. Available: {', '.join(self.tools)}"
        fn = self.tools[name]
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(**args)
            else:
                result = await asyncio.to_thread(fn, **args)
            return result
        except TypeError as e:
            return f"ERROR: bad args for '{name}': {e}"


registry = ToolRegistry()
