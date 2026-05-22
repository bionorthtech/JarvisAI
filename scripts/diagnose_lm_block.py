#!/usr/bin/env python3
"""Diagnose why JARVIS can't reach LM Studio.

Three failure modes get conflated as "LM Studio offline":

  1. LM Studio isn't running        → no listener on 127.0.0.1:1234
  2. OpenSnitch blocked the egress  → listener exists, connect refused
  3. LM Studio binding to ::1 only  → IPv4 fails, IPv6 succeeds

This script classifies which one is happening and prints the exact
OpenSnitch allow rule (or LM Studio fix) you need. It does NOT modify
any firewall or system state — that's all on you (per the user's
no-network-changes rule).

Usage:
    venv/bin/python3 scripts/diagnose_lm_block.py
    venv/bin/python3 scripts/diagnose_lm_block.py --json
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from pathlib import Path

HOST_V4 = "127.0.0.1"
HOST_V6 = "::1"
PORT = 1234


def _listeners_on(port: int) -> tuple[bool, bool, str]:
    """Probe `ss -tln` and return (has_v4_listener, has_v6_listener, raw_line).

    `ss` formats listeners by binding address:
      - `127.0.0.1:1234`        → v4 only
      - `0.0.0.0:1234` or `*:1234` → both families (dualstack)
      - `[::]:1234`             → both families (most kernels)
      - `[::1]:1234`            → v6 loopback only

    Classifying v4 vs v6 explicitly matters because a dualstack
    listener that's firewall-blocked on v4 would otherwise look like
    "ipv6-only" and we'd tell the user to reconfigure LM Studio when
    the real fix is to add an OpenSnitch v4 allow rule.
    """
    try:
        out = subprocess.run(
            ["ss", "-tln"],
            capture_output=True, text=True, timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, False, "ss not available"

    v4 = False
    v6 = False
    raw_line = ""
    for line in out.stdout.splitlines():
        if f":{port} " not in line and not line.rstrip().endswith(f":{port}"):
            continue
        raw_line = line.strip()
        # The local-address column shows where the socket is bound.
        # Heuristic: presence of "[" or "::" marks an IPv6 bind;
        # "0.0.0.0", "127.0.0.1", "*:" mark IPv4 (and "*:" / "[::]:"
        # mean dualstack, which counts for both).
        if "0.0.0.0:" in line or "127." in line.split()[3:5][0:1][0]:
            v4 = True
        if "*:" in line:
            v4 = True
            v6 = True
        if "[::]:" in line:
            v4 = True   # [::] dualstack binds v4 too on most Linux kernels
            v6 = True
        if "[::1]:" in line:
            v6 = True
    return v4, v6, raw_line


def _tcp_probe(host: str, port: int, timeout: float = 1.0) -> tuple[bool, str]:
    """Plain TCP connect probe. Returns (ok, error_string)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return True, ""
    except ConnectionRefusedError as e:
        return False, f"connection refused ({e.errno})"
    except socket.timeout:
        return False, "timeout"
    except OSError as e:
        return False, f"OSError({e.errno}): {e.strerror}"
    finally:
        try:
            s.close()
        except OSError:
            pass


def _http_probe(timeout: float = 2.0) -> tuple[bool, str]:
    """Hit /v1/models — confirms LM Studio actually answers HTTP."""
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(
            f"http://{HOST_V4}:{PORT}/v1/models", timeout=timeout
        ) as resp:
            body = resp.read(2048)
            return True, f"HTTP {resp.status}, {len(body)}B"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def diagnose() -> dict:
    """Run all probes, classify, return a structured result."""
    has_v4, has_v6, ss_line = _listeners_on(PORT)
    tcp_v4, tcp_v4_err = _tcp_probe(HOST_V4, PORT)
    tcp_v6, tcp_v6_err = _tcp_probe(HOST_V6, PORT)
    http_ok, http_detail = _http_probe() if tcp_v4 else (False, "skipped — TCP failed")

    # Classify in priority order: firewall blocks first (commonest
    # surprise), then ipv6-only, then not-running, then http_error.
    if http_ok:
        verdict = "ok"
        explanation = "LM Studio is reachable. No block."
        fix = None
    elif has_v4 and not tcp_v4:
        # A v4 listener exists but connect refused — that's a firewall
        # block, regardless of what the v6 socket is doing.
        verdict = "firewall_blocked"
        explanation = (
            f"A v4 listener exists on 127.0.0.1:{PORT} (per `ss -tln`) "
            f"but TCP connect was refused: {tcp_v4_err}. That's a "
            "firewall block (OpenSnitch is the usual cause on this box)."
        )
        venv_python = str((Path(__file__).resolve().parent.parent / "venv" / "bin" / "python3").resolve())
        fix = (
            "Add an OpenSnitch allow rule via the UI (right-click tray "
            "icon → Rules → New). Set:\n"
            f"  • Process path:  {venv_python}\n"
            "  • Action:        Allow\n"
            "  • Duration:      Forever\n"
            "  • Destination:   127.0.0.1\n"
            f"  • Dest port:     {PORT}\n"
            "  • Protocol:      TCP\n"
            "After saving, re-run this script — it should report `ok`."
        )
    elif not has_v4 and not has_v6 and not tcp_v4:
        verdict = "lm_studio_not_running"
        explanation = (
            f"Nothing is listening on port {PORT}. LM Studio is not "
            "running."
        )
        fix = "Open LM Studio and click Start Server."
    elif not has_v4 and has_v6 and tcp_v6:
        verdict = "ipv6_only"
        explanation = (
            "LM Studio is bound to ::1 (IPv6) but not 127.0.0.1 (IPv4). "
            "JARVIS connects to 127.0.0.1:1234."
        )
        fix = (
            "In LM Studio Server settings, set host to 0.0.0.0 or 127.0.0.1 "
            "(not ::1)."
        )
    elif tcp_v4 and not http_ok:
        verdict = "http_error"
        explanation = (
            "TCP connects but HTTP /v1/models failed. LM Studio is up "
            "but probably hasn't loaded a model yet, or returned non-2xx."
        )
        fix = "Open LM Studio, load qwen2.5-coder-7b-instruct, click Start Server."
    else:
        verdict = "unknown"
        explanation = "Couldn't classify. Raw probe output below."
        fix = "Send the output of this script to the maintainer."

    return {
        "verdict": verdict,
        "explanation": explanation,
        "fix": fix,
        "probes": {
            "listener_v4": {"present": has_v4, "ss_line": ss_line},
            "listener_v6": {"present": has_v6},
            "tcp_127.0.0.1": {"ok": tcp_v4, "error": tcp_v4_err},
            "tcp_::1": {"ok": tcp_v6, "error": tcp_v6_err},
            "http_/v1/models": {"ok": http_ok, "detail": http_detail},
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()

    result = diagnose()

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["verdict"] == "ok" else 1

    verdict = result["verdict"]
    print(f"verdict: {verdict}")
    print(f"\n{result['explanation']}\n")
    if result["fix"]:
        print("Fix:")
        for line in result["fix"].splitlines():
            print(f"  {line}")
    print("\nProbe details:")
    for name, info in result["probes"].items():
        marker = "✓" if info.get("ok") or info.get("present") else "✗"
        detail = info.get("error") or info.get("detail") or info.get("ss_line") or ""
        print(f"  {marker} {name:18} {detail}")

    return 0 if verdict == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
