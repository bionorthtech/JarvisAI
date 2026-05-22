"""System information plugin — hardware, storage, security."""
import platform
import shutil
import subprocess

from agent.core.sandbox import sandbox_info as _sandbox_info


def _run(cmd: list) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else r.stderr.strip()
    except Exception:
        return ""


async def system_hardware() -> str:
    lines = ["=== Hardware ==="]

    # CPU
    cpu_info = _run(["lscpu"])
    for field in ("Model name", "CPU(s)", "Thread(s) per core", "Core(s) per socket"):
        for line in cpu_info.splitlines():
            if line.strip().startswith(field):
                lines.append(f"  {line.strip()}")
                break

    # RAM
    mem_info = _run(["free", "-h"])
    for line in mem_info.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            lines.append(f"  RAM: {parts[1]} total, {parts[2]} used, {parts[3]} free")

    # GPU
    if shutil.which("nvidia-smi"):
        gpu = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader"])
        if gpu:
            lines.append(f"  GPU: {gpu}")
    else:
        lspci = _run(["lspci"])
        for line in lspci.splitlines():
            if any(k in line for k in ("VGA", "3D", "Display", "NVIDIA", "AMD", "Intel")):
                lines.append(f"  GPU: {line.split(':', 2)[-1].strip()}")
                break

    # OS
    lines.append(f"  OS: {platform.platform()}")
    lines.append(f"  Kernel: {platform.release()}")
    lines.append(f"  Arch: {platform.machine()}")
    lines.append(f"  Python: {platform.python_version()}")

    return "\n".join(lines)


async def system_storage() -> str:
    df = _run(["df", "-h", "--output=source,fstype,size,used,avail,pcent,target"])
    if not df:
        return "ERROR: df command failed"

    lines = ["=== Storage ==="]
    for line in df.splitlines():
        # Skip tmpfs, devtmpfs, squashfs except root
        if any(t in line for t in ("tmpfs", "devtmpfs", "squashfs", "udev")):
            continue
        lines.append(f"  {line}")
    return "\n".join(lines)


async def sandbox_info() -> str:
    info = _sandbox_info()
    lines = [
        "=== Sandbox Capabilities ===",
        f"  bwrap:     {'✓ available at ' + info['bwrap_path'] if info['bwrap'] else '✗ not found — install: sudo apt install bubblewrap'}",
        f"  socat:     {'✓ available at ' + (info['socat_path'] or '') if info['socat'] else '✗ not found — install: sudo apt install socat'}",
        f"  workspace: {info['workspace']}",
    ]
    if not info["bwrap"]:
        lines.append("  NOTE: Without bwrap, shell commands run with restricted env only (no namespace isolation)")
    return "\n".join(lines)
