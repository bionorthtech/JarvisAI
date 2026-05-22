"""
C5.1 — Homelab warden.

Read-only sweep of:
  - `systemctl --user list-units --type=service --state=failed` (per-user failed units)
  - `docker ps -a --format json` (containers, including stopped)
  - `podman ps -a --format json` (rootless containers if podman is present)
  - `journalctl --since=10min --priority=err` (recent error spikes)

Reports findings. **Does not auto-restart.** The plan originally
proposed auto-restart-with-backoff but auto-restart can conflict with
the user's intent (a service might be intentionally stopped); restart
is exposed as an explicit `/bots/homelab-warden/restart/{unit}` action
that flows through the existing DANGER-tier confirmation modal.

Trigger: every 5 minutes via the autonomy bot scheduler. Findings
flow on the bus as `homelab.finding` and bubble up to the dashboard.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

from agent.core import bus


_JOURNAL_LOOKBACK = "10min ago"
_DOCKER_TIMEOUT = 4
_SYSTEMCTL_TIMEOUT = 4


def _which(binary: str) -> bool:
    return shutil.which(binary) is not None


def _failed_user_units() -> list[dict[str, Any]]:
    """List user-level systemd services in the `failed` state. Returns
    [] if systemctl isn't usable as the current user (no DBUS session)."""
    if not _which("systemctl"):
        return []
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-units",
             "--type=service", "--state=failed", "--no-legend",
             "--plain", "--no-pager"],
            capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT,
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []
    out: list[dict[str, Any]] = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        # Strip leading bullet glyph if present.
        unit = parts[0].lstrip("●○ ")
        out.append({
            "unit": unit,
            "load": parts[1] if len(parts) > 1 else "",
            "active": parts[2] if len(parts) > 2 else "",
            "sub": parts[3] if len(parts) > 3 else "",
            "description": parts[4] if len(parts) > 4 else "",
        })
    return out


def _container_list(binary: str) -> list[dict[str, Any]]:
    """Run `<binary> ps -a --format json` and return parsed rows.
    Empty on permission denial or daemon-down."""
    if not _which(binary):
        return []
    try:
        r = subprocess.run(
            [binary, "ps", "-a", "--format", "json"],
            capture_output=True, text=True, timeout=_DOCKER_TIMEOUT,
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []
    out: list[dict[str, Any]] = []
    txt = r.stdout.strip()
    if not txt:
        return out
    # docker emits one JSON object per line; podman emits a JSON array.
    if txt.startswith("["):
        try:
            arr = json.loads(txt)
            return [_normalize_container(binary, x) for x in arr]
        except json.JSONDecodeError:
            return []
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(_normalize_container(binary, json.loads(line)))
        except json.JSONDecodeError:
            continue
    return out


def _normalize_container(engine: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce docker's / podman's slightly different JSON shapes to a
    single schema. Both engines expose State/Status/Names/Image but
    capitalization and types differ."""
    names = raw.get("Names") or raw.get("Name") or ""
    if isinstance(names, list):
        names = ", ".join(names)
    state = (raw.get("State") or raw.get("Status") or "").lower()
    return {
        "engine": engine,
        "id": (raw.get("ID") or raw.get("Id") or "")[:12],
        "image": raw.get("Image") or "",
        "name": names,
        "state": state,
        "status": raw.get("Status") or "",
    }


def _journal_error_count() -> dict[str, Any]:
    """Quick journalctl spike check — count error-priority lines in the
    last 10 minutes. Useful as a coarse "is the box on fire" signal."""
    if not _which("journalctl"):
        return {"available": False, "count": 0}
    try:
        r = subprocess.run(
            ["journalctl", "--since", _JOURNAL_LOOKBACK,
             "--priority=err", "--no-pager", "--quiet"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return {"available": False, "count": 0}
    if r.returncode != 0:
        return {"available": True, "count": 0, "error_rc": r.returncode}
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    return {"available": True, "count": len(lines), "sample": lines[:5]}


class HomelabWarden:
    min_autonomy_level: int = 1
    wake_conditions: list[str] = ["service.failed", "docker.error"]

    def run(self) -> dict[str, Any]:
        t0 = time.time()
        failed_units = _failed_user_units()
        docker_containers = _container_list("docker")
        podman_containers = _container_list("podman")
        journal = _journal_error_count()

        # Containers we care about: anything not "running" / "up".
        def _down(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in rows
                    if r["state"] not in ("running", "up") and r["state"]]

        down_docker = _down(docker_containers)
        down_podman = _down(podman_containers)

        findings: list[dict[str, Any]] = []
        for u in failed_units:
            findings.append({
                "kind": "service_failed",
                "id": u["unit"],
                "detail": u.get("description") or u["unit"],
            })
        for c in down_docker + down_podman:
            findings.append({
                "kind": "container_down",
                "id": f"{c['engine']}:{c['name'] or c['id']}",
                "detail": f"{c['image']} · {c['status']}",
            })
        if journal.get("count", 0) > 25:
            findings.append({
                "kind": "journal_spike",
                "id": "journal-10min",
                "detail": f"{journal['count']} error-priority lines in last 10min",
            })

        report = {
            "ts": t0,
            "duration_s": round(time.time() - t0, 2),
            "failed_units": failed_units,
            "containers": {
                "docker": docker_containers,
                "podman": podman_containers,
            },
            "containers_down": {
                "docker": down_docker,
                "podman": down_podman,
            },
            "journal_errors_10min": journal,
            "findings": findings,
            "summary": (
                "all systems nominal"
                if not findings
                else f"{len(findings)} issue(s): "
                + ", ".join(f["kind"] for f in findings[:3])
            ),
        }

        for f in findings:
            bus.publish("homelab.finding", "homelab_warden", f)
        bus.publish("homelab.report", "homelab_warden", {
            "summary": report["summary"],
            "finding_count": len(findings),
        })
        return report


    # ── restart (explicit user-initiated; not autonomous) ──────────────────

    def restart(self, kind: str, identifier: str,
                engine: str | None = None) -> dict[str, Any]:
        """Restart one failed service or stopped container. Returns the
        subprocess result. Only called from the UI's two-click confirm
        flow — never invoked by the autonomous tick. Read-error paths
        return structured errors instead of raising.

        kind=service     → `systemctl --user restart <identifier>`
        kind=container   → `<engine> start <identifier>` (engine=docker|podman)
        """
        if kind == "service":
            if not _which("systemctl"):
                return {"ok": False, "error": "systemctl unavailable"}
            cmd = ["systemctl", "--user", "restart", identifier]
        elif kind == "container":
            if engine not in ("docker", "podman"):
                return {"ok": False, "error": f"unknown engine: {engine}"}
            if not _which(engine):
                return {"ok": False, "error": f"{engine} not installed"}
            cmd = [engine, "start", identifier]
        else:
            return {"ok": False, "error": f"unknown kind: {kind}"}

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            bus.publish("homelab.restart_failed", "homelab_warden",
                        {"kind": kind, "id": identifier, "error": "timeout"})
            return {"ok": False, "error": "restart timed out (15s)"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

        payload = {
            "kind": kind, "id": identifier, "engine": engine,
            "returncode": r.returncode,
            "stdout": r.stdout.strip()[:400],
            "stderr": r.stderr.strip()[:400],
        }
        if r.returncode == 0:
            bus.publish("homelab.restart_ok", "homelab_warden", payload)
        else:
            bus.publish("homelab.restart_failed", "homelab_warden", payload)
        payload["ok"] = r.returncode == 0
        return payload


warden = HomelabWarden()
