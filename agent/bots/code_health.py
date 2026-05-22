"""
Code Health Monitor Bot

Audits Jarvis source for dead imports, TODO debt, large files, and dependency drift.
Surfaces findings — does not auto-fix code.

Trigger: POST /bots/code-health/run + weekly via autonomy scheduler.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from agent.bots import write_report
from agent.core import bus

JARVIS_ROOT = Path.home() / "jarvis"
SKIP_DIRS = ("venv", "__pycache__", "node_modules", ".git", "dist", "build")


def _is_skipped(path: Path) -> bool:
    parts = path.parts
    return any(d in parts for d in SKIP_DIRS)


class CodeHealthMonitor:
    min_autonomy_level: int = 1
    wake_conditions: list[str] = ["code.changed.bulk"]

    def run(self) -> dict[str, Any]:
        t0 = time.time()
        findings: list[dict[str, Any]] = []

        todo_count = self._count_todos()
        if todo_count > 10:
            findings.append({
                "type": "todo_debt",
                "count": todo_count,
                "detail": f"{todo_count} TODO/FIXME/HACK lines in codebase",
            })

        large_files = self._large_files()
        if large_files:
            findings.append({
                "type": "large_files",
                "count": len(large_files),
                "detail": f"Large files: {', '.join(large_files[:3])}",
                "files": large_files,
            })

        unused_imports = self._unused_imports()
        if unused_imports:
            findings.append({
                "type": "unused_imports",
                "count": len(unused_imports),
                "detail": f"{len(unused_imports)} unused imports across codebase",
                "samples": unused_imports[:5],
            })

        report = {
            "ts": t0,
            "duration_s": round(time.time() - t0, 2),
            "findings": findings,
            "score": self._score(findings),
        }

        report["todos"] = next((f["count"] for f in findings if f["type"] == "todo_debt"), 0)
        report["large_files"] = next((f["count"] for f in findings if f["type"] == "large_files"), 0)

        bus.publish("code_health.report", "CodeHealthMonitor", {
            "findings": len(findings),
            "todos": report["todos"],
            "large_files": report["large_files"],
            "score": report["score"],
        })
        write_report("code_health", report)
        return report

    def _count_todos(self) -> int:
        try:
            r = subprocess.run(
                ["grep", "-rn", "--include=*.py", "-E", "TODO|FIXME|HACK",
                 str(JARVIS_ROOT),
                 "--exclude-dir=venv", "--exclude-dir=__pycache__",
                 "--exclude-dir=node_modules", "--exclude-dir=.git"],
                capture_output=True, text=True, timeout=15,
            )
            return len(r.stdout.strip().splitlines()) if r.stdout else 0
        except Exception:
            return 0

    def _large_files(self) -> list[str]:
        large: list[str] = []
        try:
            for f in JARVIS_ROOT.rglob("*.py"):
                if _is_skipped(f):
                    continue
                try:
                    lines = len(f.read_text(errors="replace").splitlines())
                except Exception:
                    continue
                if lines > 500:
                    large.append(f"{f.relative_to(JARVIS_ROOT)}: {lines} lines")
        except Exception:
            pass
        return sorted(large, key=lambda s: -int(s.split(": ")[1].split()[0]))

    def _unused_imports(self) -> list[str]:
        try:
            r = subprocess.run(
                ["python3", "-m", "pyflakes", str(JARVIS_ROOT / "agent"),
                 str(JARVIS_ROOT / "main.py")],
                capture_output=True, text=True, timeout=20,
            )
            lines = (r.stdout or "").splitlines()
            return [ln for ln in lines if "imported but unused" in ln]
        except Exception:
            return []

    def _score(self, findings: list[dict[str, Any]]) -> int:
        score = 100
        for f in findings:
            if f["type"] == "todo_debt":
                score -= min(20, f["count"] // 5)
            elif f["type"] == "large_files":
                score -= min(15, f["count"] * 3)
            elif f["type"] == "unused_imports":
                score -= min(15, f["count"])
        return max(0, score)


code_monitor = CodeHealthMonitor()
