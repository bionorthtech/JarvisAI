"""
JARVIS Sandbox — bubblewrap (bwrap) execution layer.
Runs shell commands in an isolated namespace: no network, restricted filesystem,
unprivileged user. Falls back to restricted subprocess if bwrap unavailable.

socat integration: if a sandboxed process needs to reach a specific local
Unix socket service (e.g. a language server), socat can forward
/tmp/sandbox-<name>.sock → the real socket before the process starts.
"""
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.sandbox")

_BWRAP = shutil.which("bwrap") or "/usr/bin/bwrap"
_SOCAT = shutil.which("socat")

# Workspace mounted read-write inside the sandbox
_WORKSPACE = Path.home() / "jarvis" / "workspace"
_WORKSPACE.mkdir(parents=True, exist_ok=True)


def _bwrap_available() -> bool:
    return Path(_BWRAP).exists()


def _socat_available() -> bool:
    return _SOCAT is not None


def run_sandboxed(
    command: str,
    workspace: Optional[str] = None,
    timeout: int = 60,
    allow_socket: Optional[str] = None,
) -> str:
    """
    Run a shell command inside a bubblewrap sandbox.

    workspace:    directory to bind as /workspace (read-write). Defaults to ~/jarvis/workspace.
    timeout:      seconds before the process is killed.
    allow_socket: path to a Unix domain socket to expose inside the sandbox via socat.
                  The socket will appear as /tmp/service.sock inside the sandbox.
    """
    ws = Path(workspace or str(_WORKSPACE)).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    if not _bwrap_available():
        logger.warning("bwrap not found — running with restricted env (no namespace isolation)")
        return _run_restricted(command, str(ws), timeout)

    socat_proc = None
    sandbox_socket = None

    try:
        # ── Optional: bridge a Unix socket into the sandbox via socat ─────────
        if allow_socket and _socat_available():
            sock_path = Path(allow_socket)
            if sock_path.exists():
                # Create a temp socket inside the workspace that socat will proxy
                sandbox_socket = str(ws / "service.sock")
                # socat: listen on workspace/service.sock, forward to real socket
                socat_cmd = [
                    _SOCAT,
                    f"UNIX-LISTEN:{sandbox_socket},fork,reuseaddr",
                    f"UNIX-CONNECT:{allow_socket}",
                ]
                socat_proc = subprocess.Popen(
                    socat_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.debug("socat bridge: %s → %s", sandbox_socket, allow_socket)

        # ── Build bwrap command ───────────────────────────────────────────────
        bwrap_cmd = [
            _BWRAP,
            # Read-only system dirs
            "--ro-bind", "/usr", "/usr",
            "--ro-bind-try", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind-try", "/sbin", "/sbin",
            "--ro-bind-try", "/etc/ssl", "/etc/ssl",     # TLS certs (for pip etc.)
            "--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf",
            # Isolation
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--tmpfs", "/home",
            # Workspace is the only writable area
            "--bind", str(ws), "/workspace",
            "--chdir", "/workspace",
            # Namespace isolation
            "--unshare-net",       # no network
            "--unshare-pid",       # isolated PID tree
            "--unshare-uts",       # isolated hostname
            "--new-session",
            "--die-with-parent",
        ]

        # Expose socat socket inside sandbox if set up
        if sandbox_socket and Path(sandbox_socket).exists():
            bwrap_cmd += ["--bind", sandbox_socket, "/tmp/service.sock"]

        bwrap_cmd += ["--", "bash", "-c", command]

        result = subprocess.run(
            bwrap_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                "HOME": "/workspace",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "TERM": "dumb",
                "LANG": "en_US.UTF-8",
            },
        )

        out = result.stdout.strip()
        err = result.stderr.strip()

        if result.returncode == 0:
            return out or "(no output)"
        return f"EXIT {result.returncode}\n{(out + os.linesep + err).strip()}"

    except subprocess.TimeoutExpired:
        return f"SANDBOX TIMEOUT: {timeout}s exceeded"
    except FileNotFoundError:
        return f"SANDBOX ERROR: bwrap not found at {_BWRAP}"
    except Exception as e:
        return f"SANDBOX ERROR: {e}"
    finally:
        if socat_proc:
            socat_proc.terminate()
            try:
                socat_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                socat_proc.kill()
        if sandbox_socket and Path(sandbox_socket).exists():
            Path(sandbox_socket).unlink(missing_ok=True)


def _run_restricted(command: str, workspace: str, timeout: int) -> str:
    """Fallback: no bwrap. Run with minimal env, no root."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace,
            env={
                "HOME": workspace,
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "TERM": "dumb",
                "LANG": "en_US.UTF-8",
            },
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode == 0:
            return out or "(no output)"
        return f"EXIT {result.returncode}\n{(out + os.linesep + err).strip()}"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT: {timeout}s exceeded"
    except Exception as e:
        return f"ERROR: {e}"


def sandbox_info() -> dict:
    """Report what sandbox capabilities are available."""
    return {
        "bwrap": _bwrap_available(),
        "bwrap_path": _BWRAP if _bwrap_available() else None,
        "socat": _socat_available(),
        "socat_path": _SOCAT,
        "workspace": str(_WORKSPACE),
    }
