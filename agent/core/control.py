"""
JARVIS Control Layer — Layer 1 policy engine.
Pure Python, zero LLM calls. Every tool call passes through this before execution.

Responsibilities:
  1. Tool allowlist enforcement
  2. Trust tier determination (with path-based escalation)
  3. Token budget guard (prevent context overflow)
  4. Prompt injection defence (wrap untrusted content in safe tags)
  5. Path safety (block writes to system / credential paths)
"""
import re
from pathlib import Path
from typing import Optional

from agent.core.confirmations import TOOL_TIERS

# ── System paths — writes always blocked ─────────────────────────────────────
_BLOCKED_WRITE_PREFIXES = (
    "/etc/", "/boot/", "/sys/", "/proc/", "/dev/",
    "/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/",
    "/lib/", "/lib64/",
)

# Sensitive filename patterns — block reads AND writes
_SENSITIVE_RE = re.compile(
    r"(\.ssh/|\.gnupg/|\.aws/credentials|\.config/gcloud/"
    r"|shadow$|passwd$|sudoers|authorized_keys"
    r"|id_rsa|id_ed25519|id_ecdsa|\.pem$|\.key$|\.p12$"
    r"|\.env$|secrets\.(json|toml|yaml|yml)$)",
    re.IGNORECASE,
)

# ── Token budget ──────────────────────────────────────────────────────────────
_CHARS_PER_TOKEN = 4
_MAX_CONTENT_TOKENS = 8000  # max chars / 4 = tokens for a single content arg

# ── Prompt injection patterns ─────────────────────────────────────────────────
_INJECTION_RE = re.compile(
    r"(ignore (all |previous )?instructions"
    r"|you are now"
    r"|new (system )?prompt"
    r"|disregard your"
    r"|act as (?!a|an|the)"   # 'act as' without article = suspicious
    r"|<\|.*?\|>"              # special token markers
    r"|###\s*SYSTEM)"
    r"|jailbreak",
    re.IGNORECASE,
)


class PolicyViolation(Exception):
    """Raised when a tool call violates policy. Message shown to user."""
    pass


class ControlLayer:
    def __init__(self, allowed_tools: Optional[set] = None):
        # None = all registered tools are allowed
        self._allowed = allowed_tools

    # ── 1. Tool allowlist ─────────────────────────────────────────────────────
    def check_tool_allowed(self, tool_name: str) -> None:
        if self._allowed is not None and tool_name not in self._allowed:
            raise PolicyViolation(
                f"Tool '{tool_name}' is not in the allowlist.\n"
                f"Allowed: {', '.join(sorted(self._allowed))}"
            )

    # ── 2. Trust tier with path escalation ───────────────────────────────────
    def effective_tier(self, tool_name: str, args: dict) -> str:
        base_tier = TOOL_TIERS.get(tool_name, "CAUTION")

        path = args.get("path", "")
        if not path or tool_name not in ("write_file", "read_file", "list_directory"):
            return base_tier

        resolved = str(Path(path).expanduser().resolve())

        # Block writes to system paths entirely
        if tool_name == "write_file":
            for prefix in _BLOCKED_WRITE_PREFIXES:
                if resolved.startswith(prefix):
                    raise PolicyViolation(
                        f"Write to system path blocked: {resolved}"
                    )

        # Escalate to CRITICAL for sensitive patterns on any operation
        if _SENSITIVE_RE.search(resolved):
            if tool_name == "write_file":
                raise PolicyViolation(
                    f"Write to sensitive path blocked: {resolved}\n"
                    "This path may contain credentials or private keys."
                )
            return "CRITICAL"  # reads of sensitive files need explicit confirm

        # Write outside home dir = DANGER
        if tool_name == "write_file":
            home = str(Path.home())
            if not resolved.startswith(home):
                return "DANGER"
            return "CAUTION"

        return base_tier

    # ── 3. Token budget ───────────────────────────────────────────────────────
    def check_content_size(self, content: str, label: str = "content") -> None:
        tokens = len(content) // _CHARS_PER_TOKEN
        if tokens > _MAX_CONTENT_TOKENS:
            raise PolicyViolation(
                f"{label} too large (~{tokens:,} tokens, limit {_MAX_CONTENT_TOKENS:,}). "
                "Split the file or request a specific range."
            )

    # ── 4. Prompt injection wrapper ───────────────────────────────────────────
    def wrap_untrusted(self, content: str, source: str = "file") -> str:
        """
        Wrap external content in <untrusted_content> so the model treats it
        as data, not instructions. Scan for injection attempts first.
        """
        if _INJECTION_RE.search(content):
            # Don't block — just flag clearly
            content = "[INJECTION_PATTERN_DETECTED]\n" + content

        return (
            f"<untrusted_content source=\"{source}\">\n"
            f"{content}\n"
            f"</untrusted_content>"
        )

    def should_wrap(self, tool_name: str) -> bool:
        """True for tools that return external/user data into the context."""
        return tool_name in (
            "read_file", "kiwix_search", "kiwix_get_article",
            "obsidian_read_note", "obsidian_search", "web_search",
            "memory_recall",
        )

    # ── 5. Main validation ────────────────────────────────────────────────────
    def validate(self, tool_name: str, args: dict) -> str:
        """
        Run all policy checks. Returns the effective trust tier.
        Raises PolicyViolation on failure.
        """
        self.check_tool_allowed(tool_name)
        tier = self.effective_tier(tool_name, args)
        content = args.get("content", "")
        if content:
            self.check_content_size(content, label="write_file content")
        return tier


control = ControlLayer()
