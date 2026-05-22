"""
GUI automation tools — control the desktop from JARVIS.
Uses ydotool (Wayland) and xdotool (X11/XWayland).
Screenshots via scrot (X11) or gnome-screenshot.
"""
import time
import subprocess
import asyncio
from pathlib import Path
from typing import Optional


async def _run(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return stdout.decode().strip() or "OK"
    return f"ERROR (exit {proc.returncode}): {stderr.decode().strip()}"


async def take_screenshot(path: Optional[str] = None) -> str:
    """Capture a screenshot. Returns the saved file path."""
    if path is None:
        ts = int(time.time())
        path = str(Path.home() / f"jarvis-screenshot-{ts}.png")

    # Try gnome-screenshot (works on Wayland + XWayland)
    for cmd in [
        f'gnome-screenshot -f "{path}"',
        f'scrot "{path}"',
        f'grim "{path}"',
    ]:
        tool = cmd.split()[0]
        if subprocess.run(["which", tool], capture_output=True).returncode == 0:
            result = await _run(cmd)
            if "ERROR" not in result:
                return f"Screenshot saved to: {path}"
    return "ERROR: No screenshot tool found (tried gnome-screenshot, scrot, grim)"


async def type_text(text: str, delay_ms: int = 12) -> str:
    """Type text using ydotool (Wayland-native)."""
    # Escape single quotes in text
    safe = text.replace("'", "'\\''")
    result = await _run(f"ydotool type --delay {delay_ms} -- '{safe}'")
    if "ERROR" in result:
        # Fallback to xdotool
        result = await _run(f"xdotool type --delay {delay_ms} -- '{safe}'")
    return result


async def key_press(keys: str) -> str:
    """
    Press a key or key combination.
    Examples: 'ctrl+c', 'super+l', 'Return', 'ctrl+shift+t'
    """
    result = await _run(f"ydotool key {keys}")
    if "ERROR" in result:
        result = await _run(f"xdotool key {keys}")
    return result


async def mouse_move(x: int, y: int) -> str:
    """Move mouse cursor to absolute screen coordinates."""
    result = await _run(f"ydotool mousemove --absolute -x {x} -y {y}")
    if "ERROR" in result:
        result = await _run(f"xdotool mousemove {x} {y}")
    return result


async def mouse_click(x: int, y: int, button: str = "left") -> str:
    """Move to (x,y) and click. Button: left, right, middle."""
    btn_map = {"left": "1", "right": "3", "middle": "2"}
    btn = btn_map.get(button.lower(), "1")

    move = await mouse_move(x, y)
    if "ERROR" in move:
        return move

    result = await _run(f"ydotool click {btn}")
    if "ERROR" in result:
        result = await _run(f"xdotool click {btn}")
    return result


# ── Tool schemas for LM Studio ────────────────────────────────────────────────

GUI_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Capture a screenshot of the entire screen. Returns the file path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional output path. Defaults to ~/jarvis-screenshot-<ts>.png",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text using the keyboard (simulated input to whatever window is focused).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type."},
                    "delay_ms": {
                        "type": "integer",
                        "description": "Delay between keystrokes in ms (default 12).",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "key_press",
            "description": "Press a key or key combo. Examples: 'ctrl+c', 'super+l', 'Return', 'ctrl+shift+t'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Key or combo to press."}
                },
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_move",
            "description": "Move the mouse cursor to absolute screen coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "Move to (x,y) and click. Use take_screenshot first to see the screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button (default: left).",
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
]
