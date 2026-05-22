"""
Process monitor plugin — system process and resource monitoring.
Uses psutil (already in venv). All reads are SAFE tier.
process_stop is DANGER — always requires confirmation.
"""
import shutil
import subprocess

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False


async def process_list(filter_name: str = "", limit: int = 25) -> str:
    if not _PSUTIL:
        return "ERROR: psutil not installed. Run: pip install psutil"

    header = f"{'PID':>7}  {'CPU%':>6}  {'MEM%':>5}  {'STATUS':<9}  {'NAME':<22}  COMMAND"
    sep = "-" * 80
    rows = []

    for proc in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_percent", "status", "cmdline"]
    ):
        try:
            info = proc.info
            name = info.get("name") or ""
            if filter_name and filter_name.lower() not in name.lower():
                continue
            rows.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    rows.sort(key=lambda p: p.get("cpu_percent") or 0, reverse=True)

    lines = [header, sep]
    for p in rows[:limit]:
        cmd_parts = p.get("cmdline") or []
        cmd = " ".join(cmd_parts)[:35] if cmd_parts else p.get("name", "")
        lines.append(
            f"{p['pid']:>7}  "
            f"{p.get('cpu_percent', 0):>6.1f}  "
            f"{p.get('memory_percent', 0):>5.1f}  "
            f"{p.get('status', '?'):<9}  "
            f"{p.get('name', '')[:22]:<22}  "
            f"{cmd}"
        )

    if not rows:
        lines.append("(no processes match filter)")
    elif len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more processes")

    return "\n".join(lines)


async def process_info(pid: int) -> str:
    if not _PSUTIL:
        return "ERROR: psutil not installed"
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            mem = proc.memory_info()
            cpu = proc.cpu_percent(interval=0.1)
            info = {
                "pid": proc.pid,
                "name": proc.name(),
                "status": proc.status(),
                "cpu_percent": f"{cpu:.1f}%",
                "memory_rss_mb": f"{mem.rss / 1_048_576:.1f} MB",
                "memory_vms_mb": f"{mem.vms / 1_048_576:.1f} MB",
                "cmdline": " ".join(proc.cmdline()),
                "cwd": proc.cwd(),
                "open_files": len(proc.open_files()),
                "threads": proc.num_threads(),
                "create_time": proc.create_time(),
            }
            try:
                conns = proc.net_connections()
                info["network_connections"] = len(conns)
                for c in conns[:5]:
                    laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
                    raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
                    info[f"  conn_{c.status}"] = f"{laddr} → {raddr}"
            except psutil.AccessDenied:
                info["network_connections"] = "denied"

        return "\n".join(f"{k}: {v}" for k, v in info.items())
    except psutil.NoSuchProcess:
        return f"ERROR: No process with PID {pid}"
    except psutil.AccessDenied:
        return f"ERROR: Access denied for PID {pid}"


async def process_stop(pid: int, force: bool = False) -> str:
    if not _PSUTIL:
        return "ERROR: psutil not installed"
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        if force:
            proc.kill()
            return f"SIGKILL sent to {name} (PID {pid})"
        else:
            proc.terminate()
            return f"SIGTERM sent to {name} (PID {pid})"
    except psutil.NoSuchProcess:
        return f"ERROR: No process with PID {pid}"
    except psutil.AccessDenied:
        return f"ERROR: Access denied — cannot stop PID {pid}"


async def system_resources() -> str:
    if not _PSUTIL:
        return "ERROR: psutil not installed"

    cpu = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count(logical=True)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")

    lines = [
        "=== System Resources ===",
        f"CPU:   {cpu:.1f}%  ({cpu_count} logical cores)",
        f"RAM:   {mem.used/1e9:.1f} / {mem.total/1e9:.1f} GB  ({mem.percent:.1f}% used)",
        f"Swap:  {swap.used/1e9:.1f} / {swap.total/1e9:.1f} GB  ({swap.percent:.1f}%)",
        f"Disk:  {disk.used/1e9:.1f} / {disk.total/1e9:.1f} GB  ({disk.percent:.1f}% used)",
    ]

    # GPU via nvidia-smi if available
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for gpu_line in result.stdout.strip().splitlines():
                    parts = [p.strip() for p in gpu_line.split(",")]
                    if len(parts) >= 5:
                        lines.append(
                            f"GPU:   {parts[0]} | {parts[1]}% util | "
                            f"{parts[2]}/{parts[3]} MB | {parts[4]}°C"
                        )
        except Exception:
            pass

    return "\n".join(lines)


async def network_connections(filter_pid: int = 0) -> str:
    if not _PSUTIL:
        return "ERROR: psutil not installed"

    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return "ERROR: Access denied — try with elevated permissions"

    lines = [f"{'PID':>7}  {'STATUS':<12}  {'LOCAL':<25}  {'REMOTE':<25}  PROCESS"]
    lines.append("-" * 90)

    shown = 0
    for c in sorted(conns, key=lambda x: x.pid or 0):
        if filter_pid and c.pid != filter_pid:
            continue
        if not c.raddr:
            continue  # Skip listening sockets

        laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
        pid_str = str(c.pid) if c.pid else "-"

        try:
            proc_name = psutil.Process(c.pid).name() if c.pid else "-"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            proc_name = "?"

        lines.append(
            f"{pid_str:>7}  {c.status:<12}  {laddr:<25}  {raddr:<25}  {proc_name}"
        )
        shown += 1
        if shown >= 50:
            lines.append("... (limited to 50 connections)")
            break

    if shown == 0:
        lines.append("No active outbound connections")

    return "\n".join(lines)
