"""
Git plugin — safe git operations for JARVIS.
Read operations are SAFE. Write operations (commit, checkout) are CAUTION.
Push is intentionally omitted — use run_shell with explicit confirmation.
"""
import subprocess
from pathlib import Path


def _git(args: list, cwd: str = ".") -> str:
    resolved = str(Path(cwd).expanduser().resolve())
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=resolved,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode == 0:
            return out or "(no output)"
        return f"git error (exit {result.returncode}):\n{err or out}"
    except FileNotFoundError:
        return "ERROR: git not found. Install with: sudo apt install git"
    except subprocess.TimeoutExpired:
        return "ERROR: git command timed out (30s)"
    except Exception as e:
        return f"ERROR: {e}"


async def git_status(path: str = ".") -> str:
    return _git(["status", "--short", "--branch"], cwd=path)


async def git_diff(path: str = ".", staged: bool = False, file: str = "") -> str:
    args = ["diff"]
    if staged:
        args.append("--staged")
    if file:
        args += ["--", file]
    result = _git(args, cwd=path)
    if len(result) > 6000:
        result = result[:6000] + "\n...[diff truncated at 6000 chars]"
    return result


async def git_log(path: str = ".", count: int = 15) -> str:
    return _git(
        ["log", f"--max-count={count}", "--oneline", "--graph", "--decorate"],
        cwd=path,
    )


async def git_branch(path: str = ".") -> str:
    return _git(["branch", "-vv"], cwd=path)


async def git_commit(path: str = ".", message: str = "") -> str:
    if not message.strip():
        return "ERROR: commit message is required"

    # Show what will be staged first
    status = _git(["status", "--short"], cwd=path)
    if not status.strip():
        return "Nothing to commit — working tree clean."

    preview = f"About to commit:\n{status}\n\nMessage: {message}\n\n"

    stage = _git(["add", "-A"], cwd=path)
    commit = _git(["commit", "-m", message], cwd=path)

    result = preview + (stage + "\n" if stage and stage != "(no output)" else "") + commit
    return result.strip()


async def git_checkout(path: str = ".", branch: str = "", create: bool = False) -> str:
    if not branch:
        return "ERROR: branch name required"

    args = ["checkout"]
    if create:
        args.append("-b")
    args.append(branch)

    return _git(args, cwd=path)
