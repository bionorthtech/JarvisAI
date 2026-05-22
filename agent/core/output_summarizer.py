"""
Advanced Output Summarization

Takes long agent output (stack traces, build logs, command output) and
produces a structured summary with root_cause / location / suggested_fix
that the UI can render compactly with an expand-for-detail toggle.

Supports:
  - Python tracebacks
  - npm / vite / tsc build errors
  - shell command failures
  - generic logs
"""
from __future__ import annotations

import re
from typing import Any

PY_FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)')
PY_ERROR_LINE_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*Error[^:\n]*): (.+)$",
    re.MULTILINE,
)
TS_ERROR_RE = re.compile(
    r"([^\s:]+\.tsx?):(\d+):(\d+)\s*-\s*error\s+(TS\d+):\s*(.+)",
)
NPM_VITE_RE = re.compile(
    r"\[vite\]\s+(\w+):\s+(.+?)(?:\n|$)|^Error:\s+(.+?)(?:\n|$)",
    re.MULTILINE,
)
SHELL_NONZERO_RE = re.compile(
    r"(?:Command failed|exited with code|exit status)\s+(\d+)",
    re.IGNORECASE,
)


def summarize(raw: str, source: str = "agent") -> dict[str, Any]:
    """
    Returns:
        {
          root_cause: str,
          location:   str,        # "file:line" if known
          suggested_fix: str,
          severity:   "info" | "warning" | "error",
          source:     "python" | "typescript" | "vite" | "shell" | "generic",
          raw_excerpt: str,       # last ~600 chars for the expand panel
        }
    """
    text = (raw or "").strip()
    if not text:
        return _empty(source, raw)

    # ── Python traceback (use innermost frame as location) ──
    err_match = PY_ERROR_LINE_RE.search(text)
    file_matches = PY_FILE_LINE_RE.findall(text)
    if err_match and file_matches:
        err_type, err_msg = err_match.group(1), err_match.group(2).strip()
        err = f"{err_type}: {err_msg}"
        file_path, line_no = file_matches[-1]  # innermost frame
        # Try to find the code line just after the last "File ..." marker
        code = ""
        idx = text.rfind(f'File "{file_path}", line {line_no}')
        if idx >= 0:
            tail = text[idx:].split("\n", 2)
            if len(tail) >= 2:
                code = tail[1].strip()
        return {
            "root_cause": err,
            "location": f"{file_path}:{line_no}",
            "suggested_fix": _suggest_python_fix(err, code),
            "severity": "error",
            "source": "python",
            "raw_excerpt": _tail(text),
        }

    # ── TypeScript compiler error ──
    m = TS_ERROR_RE.search(text)
    if m:
        file, ln, col, code, msg = m.groups()
        return {
            "root_cause": f"{code}: {msg.strip()}",
            "location": f"{file}:{ln}:{col}",
            "suggested_fix": _suggest_ts_fix(code, msg),
            "severity": "error",
            "source": "typescript",
            "raw_excerpt": _tail(text),
        }

    # ── Vite / npm build ──
    m = NPM_VITE_RE.search(text)
    if m:
        kind = m.group(1) or ""
        message = (m.group(2) or m.group(3) or "").strip()
        return {
            "root_cause": message[:200] or "vite build issue",
            "location": "(vite output — see excerpt)",
            "suggested_fix": "Re-run `npm run dev` after addressing the error message.",
            "severity": "warning" if kind in ("warn", "warning") else "error",
            "source": "vite",
            "raw_excerpt": _tail(text),
        }

    # ── Shell exit code ──
    m = SHELL_NONZERO_RE.search(text)
    if m:
        return {
            "root_cause": f"shell command failed (exit {m.group(1)})",
            "location": "(shell)",
            "suggested_fix": "Inspect the last command output and re-run with -x or --verbose to debug.",
            "severity": "error",
            "source": "shell",
            "raw_excerpt": _tail(text),
        }

    # ── Generic fallback — first non-empty line as headline ──
    headline = next((ln for ln in text.splitlines() if ln.strip()), "(no output)")
    return {
        "root_cause": headline[:200],
        "location": "(unknown)",
        "suggested_fix": "(no automatic suggestion — review excerpt)",
        "severity": "info",
        "source": source if source in ("python", "typescript", "vite", "shell") else "generic",
        "raw_excerpt": _tail(text),
    }


def _tail(text: str, n: int = 600) -> str:
    if len(text) <= n:
        return text
    return "…\n" + text[-n:]


def _empty(source: str, raw: str) -> dict[str, Any]:
    return {
        "root_cause": "(empty output)",
        "location": "",
        "suggested_fix": "",
        "severity": "info",
        "source": source,
        "raw_excerpt": raw or "",
    }


def _suggest_python_fix(err: str, code: str) -> str:
    err_l = err.lower()
    if "modulenotfounderror" in err_l or "no module named" in err_l:
        match = re.search(r"['\"]([^'\"]+)['\"]", err)
        pkg = match.group(1) if match else "<package>"
        return f"pip install {pkg.split('.')[0]}"
    if "attributeerror" in err_l and "has no attribute" in err_l:
        return "Verify the object's type and that the attribute name is spelled correctly."
    if "typeerror" in err_l and "argument" in err_l:
        return "Check the function signature — argument count or keyword name mismatch."
    if "filenotfounderror" in err_l:
        match = re.search(r"['\"]([^'\"]+)['\"]", err)
        path = match.group(1) if match else "<path>"
        return f"Verify the path exists: ls -la {path}"
    if "permissionerror" in err_l:
        return "Check file permissions / sudo requirements."
    if "syntaxerror" in err_l:
        return f"Fix the syntax issue near: {code[:80]}"
    if "indexerror" in err_l:
        return "Empty/short collection — add a length check before the index access."
    if "keyerror" in err_l:
        return "Use dict.get(key) or `if key in dict:` to avoid the missing-key crash."
    return "Read the full traceback in the expand panel."


def _suggest_ts_fix(code: str, msg: str) -> str:
    if code in ("TS2304", "TS2552"):
        return "Symbol not found — add the import or fix the spelling."
    if code in ("TS2322", "TS2345"):
        return "Type mismatch — adjust the type or cast appropriately."
    if code == "TS2554":
        return "Wrong number of arguments to a function — check the signature."
    if code == "TS7006":
        return "Implicit `any` — add an explicit type annotation."
    return "Read the full TS error in the expand panel."
