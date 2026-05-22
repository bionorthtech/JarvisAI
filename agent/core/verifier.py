"""
JARVIS Code Verifier — runs on every piece of code before execution.

Pipeline:
  1. Syntax check (Python: stdlib ast / Shell: basic parse)
  2. AST walk — detect dynamic eval/exec nodes
  3. Pattern scan — banned and warning patterns
  4. Returns VerifyResult with verdict SAFE / WARNING / REJECT
"""
import ast
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class VerifyResult:
    verdict: str             # "SAFE" | "WARNING" | "REJECT"
    reason: Optional[str] = None
    line: Optional[int] = None

    def __str__(self) -> str:
        if self.verdict == "SAFE":
            return "SAFE"
        loc = f" (line {self.line})" if self.line else ""
        return f"{self.verdict}{loc}: {self.reason}"


# ── Python — hard banned ──────────────────────────────────────────────────────
_PY_BANNED = [
    (r"\beval\s*\(", "eval() — code injection risk"),
    (r"\bexec\s*\(", "exec() — code injection risk"),
    (r"__import__\s*\(", "__import__() — dynamic module load"),
    (r"importlib\.import_module\s*\(", "importlib.import_module — dynamic import"),
    (r"os\.system\s*\(", "os.system() — untracked shell"),
    (r"subprocess\b.*\bshell\s*=\s*True", "subprocess(shell=True) — injection risk"),
    (r"\bpopen\s*\(", "popen() — shell execution"),
    (r"os\.setuid\s*\(", "os.setuid() — privilege escalation"),
    (r"os\.setgid\s*\(", "os.setgid() — privilege escalation"),
    (r"ctypes\.CDLL\b", "ctypes.CDLL — raw library load"),
    (r"\bsocket\.socket\s*\(", "socket.socket() — network (offline mode)"),
    (r"urllib\.request\.urlopen\s*\(", "urlopen() — network (offline mode)"),
    (r"httpx\.|requests\.(get|post|put|delete|patch)\s*\(", "HTTP client — network"),
]

# ── Python — warn (show to user, don't block) ─────────────────────────────────
_PY_WARN = [
    (r"shutil\.rmtree\s*\(", "shutil.rmtree() — recursive deletion"),
    (r"os\.remove\s*\(", "os.remove() — file deletion"),
    (r"\.unlink\s*\(", "Path.unlink() — file deletion"),
    (r'glob\s*\(\s*["\'].*\*', "glob with wildcard"),
    (r"open\s*\(.*['\"]w['\"]", "open for write — will overwrite"),
    (r"pickle\.loads?\s*\(", "pickle — deserialisation risk"),
]

# ── Shell — hard banned ───────────────────────────────────────────────────────
_SH_BANNED = [
    (r"\brm\b.*-[rRf]*f[rR]*\s*/", "rm -rf on / — wipes system"),
    (r"\bdd\b.*\bof=/dev/", "dd to block device"),
    (r"\bmkfs\b", "mkfs — formats filesystem"),
    (r"\bfdisk\b|\bparted\b|\bgdisk\b", "disk partitioning tool"),
    (r"\bcurl\b.*\|\s*(bash|sh|zsh|fish)\b", "curl | bash — remote code execution"),
    (r"\bwget\b.*-O\s*-.*\|\s*(bash|sh)\b", "wget | bash — remote code execution"),
    (r"chmod\s+[0-7]*7[0-7][0-7]\s+/", "chmod 7xx on system path"),
    (r">\s*/etc/", "redirect write to /etc"),
    (r">\s*/boot/", "redirect write to /boot"),
    (r"\bchattr\b.*\+i\b", "chattr +i — immutable flag (persistence)"),
    (r"\biptables\b.*-F\b", "iptables -F — flush all firewall rules"),
]

# ── Shell — warn ─────────────────────────────────────────────────────────────
_SH_WARN = [
    (r"\brm\b.*-[rRf]", "recursive/force delete"),
    (r"\bsudo\b", "sudo — privilege escalation"),
    (r"\bsystemctl\b", "systemctl — service management"),
    (r"\bapt\b|\byum\b|\bdnf\b|\bpacman\b|\bbrew\b", "package manager"),
    (r"\bcrontab\b", "crontab — scheduled task"),
    (r"\bchmod\b|\bchown\b", "permission change"),
    (r">\s*~", "redirect overwriting home file"),
    (r"\bkill\b.*-9\b", "SIGKILL"),
]


class CodeVerifier:
    # ── Python ────────────────────────────────────────────────────────────────
    def check_python(self, code: str) -> VerifyResult:
        # 1. Syntax
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return VerifyResult(verdict="REJECT", reason=f"Syntax error: {e.msg}", line=e.lineno)

        # 2. AST walk — dynamic eval/exec
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in ("eval", "exec") and node.args:
                    if not isinstance(node.args[0], ast.Constant):
                        return VerifyResult(
                            verdict="REJECT",
                            reason=f"{name}() with dynamic argument — code injection risk",
                            line=getattr(node, "lineno", None),
                        )

        # 3. Pattern scan
        for pattern, desc in _PY_BANNED:
            for i, line in enumerate(code.splitlines(), 1):
                if re.search(pattern, line):
                    return VerifyResult(verdict="REJECT", reason=desc, line=i)

        for pattern, desc in _PY_WARN:
            for i, line in enumerate(code.splitlines(), 1):
                if re.search(pattern, line):
                    return VerifyResult(verdict="WARNING", reason=desc, line=i)

        return VerifyResult(verdict="SAFE")

    # ── Shell ─────────────────────────────────────────────────────────────────
    def check_shell(self, command: str) -> VerifyResult:
        for pattern, desc in _SH_BANNED:
            if re.search(pattern, command, re.IGNORECASE):
                return VerifyResult(verdict="REJECT", reason=desc)

        for pattern, desc in _SH_WARN:
            if re.search(pattern, command, re.IGNORECASE):
                return VerifyResult(verdict="WARNING", reason=desc)

        return VerifyResult(verdict="SAFE")

    # ── Auto-detect ───────────────────────────────────────────────────────────
    def check(self, code: str, language: str = "auto") -> VerifyResult:
        if language == "python":
            return self.check_python(code)
        if language in ("shell", "bash", "sh"):
            return self.check_shell(code)
        # Auto-detect: try Python first, fall back to shell
        if code.strip().startswith(("#!", "import ", "from ", "def ", "class ")):
            return self.check_python(code)
        return self.check_shell(code)


verifier = CodeVerifier()
