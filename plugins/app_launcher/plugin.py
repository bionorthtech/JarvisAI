"""
App launcher plugin.
Launches desktop applications, opens files, and lists windows via AT-SPI2.
Uses xdg-open for file opening (respects user's default apps).
Uses ydotool/xdotool for window management.
"""
import shlex
import subprocess
import shutil
from pathlib import Path


def _run(cmd: list, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode == 0:
            return out or "OK"
        return f"error (exit {result.returncode}): {err or out}"
    except subprocess.TimeoutExpired:
        return "timed out"
    except FileNotFoundError:
        return f"command not found: {cmd[0]}"
    except Exception as e:
        return f"error: {e}"


async def app_launch(app: str, args: str = "") -> str:
    # Sanitize: no shell metacharacters in app name
    safe_app = app.strip()
    if any(c in safe_app for c in (";", "&", "|", "`", "$", ">")):
        return f"ERROR: app name contains invalid characters: {safe_app}"

    # Find the executable
    if not shutil.which(safe_app):
        return (
            f"'{safe_app}' not found in PATH.\n"
            f"Is it installed? Try: which {safe_app}"
        )

    cmd = [safe_app]
    if args:
        cmd += shlex.split(args)

    # Launch detached (don't wait for app to close)
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Launched: {' '.join(cmd)}"
    except Exception as e:
        return f"Failed to launch {safe_app}: {e}"


async def app_open_file(path: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"ERROR: File not found: {path}"

    opener = shutil.which("xdg-open")
    if not opener:
        return "ERROR: xdg-open not found — is xdg-utils installed?"

    try:
        subprocess.Popen(
            [opener, str(p)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Opened with default app: {p.name}"
    except Exception as e:
        return f"Failed to open {p.name}: {e}"


async def app_list_windows() -> str:
    """List open windows using wmctrl (X11) or qdbus (KDE) or AT-SPI2."""
    # Try wmctrl first (most common on Ubuntu/PopOS)
    if shutil.which("wmctrl"):
        result = _run(["wmctrl", "-l"])
        if "error" not in result.lower() and result:
            lines = ["Open windows (wmctrl):"]
            for line in result.splitlines()[:30]:
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    lines.append(f"  [{parts[0]}] {parts[3]}")
            return "\n".join(lines)

    # Fallback: xdotool
    if shutil.which("xdotool"):
        result = _run(["xdotool", "search", "--onlyvisible", "--name", ""])
        if result and "error" not in result.lower():
            return f"Window IDs (xdotool):\n{result}"

    # Fallback: AT-SPI2
    try:
        import pyatspi
        desktop = pyatspi.Registry.getDesktop(0)
        windows = []
        for app in desktop:
            for window in app:
                if window.getRole() == pyatspi.ROLE_FRAME:
                    windows.append(f"  [{app.name}] {window.name}")
        if windows:
            return "Open windows (AT-SPI2):\n" + "\n".join(windows[:30])
    except ImportError:
        pass
    except Exception:
        pass

    return (
        "Could not list windows. "
        "Install wmctrl: sudo apt install wmctrl"
    )
