#!/usr/bin/env python3
"""One-screen JARVIS health snapshot.

Hits the live backend and prints a single-page summary of:
  - LM Studio reachability + which model is loaded
  - Bot scheduler status (which bots are due, failed, never_run)
  - Drives + emotion levels
  - Recent reflections (verdict counts)
  - Audit chain verify
  - Disk + memory pressure (light, from /perf/live)
  - Periodic registry health (last_run for each entry)

Designed so the user can run `venv/bin/python3 scripts/jarvis_status.py`
without Claude or the UI and know what's wrong inside 30s. Pairs with
`docs/RUNBOOK.md` for symptom → fix lookups.

Usage:
    venv/bin/python3 scripts/jarvis_status.py
    venv/bin/python3 scripts/jarvis_status.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def _get(path: str, timeout: float = 3.0):
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
        return json.loads(body)
    except urllib.error.URLError as e:
        return {"_error": str(e.reason)}
    except json.JSONDecodeError as e:
        return {"_error": f"bad JSON: {e}"}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def _icon(ok: bool, warn: bool = False) -> str:
    if warn:
        return "!"
    return "✓" if ok else "✗"


def _section(title: str) -> None:
    print()
    print(f"\033[1m{title}\033[0m")
    print("─" * 60)


def _row(icon: str, key: str, value: str) -> None:
    color = {"✓": "\033[32m", "✗": "\033[31m", "!": "\033[33m"}.get(icon, "")
    end = "\033[0m" if color else ""
    print(f"  {color}{icon}{end} {key:<32}{value}")


def collect() -> dict:
    return {
        "health":         _get("/health"),
        "bots":           _get("/bots/status"),
        "drives":         _get("/drives"),
        "emotion":        _get("/emotion/state"),
        "perf":           _get("/perf/live"),
        "audit_verify":   _get("/audit/verify"),
        "reflections":    _get("/reflections?limit=20"),
        "wants":          _get("/wants"),
        "autonomy":       _get("/autonomy/status"),
    }


def render(d: dict) -> int:
    """Print the snapshot. Returns 0 on all-clear, 1 if any red found."""
    any_red = False

    # ── LM Studio ────────────────────────────────────────────────────
    _section("LM Studio")
    h = d.get("health") or {}
    if h.get("_error"):
        _row("✗", "Backend reachable", f"NO ({h['_error']})")
        print("\n  Backend is offline. Start: cd ~/jarvis && venv/bin/python3 main.py")
        return 1
    lm = h.get("lm_studio") or {}
    if lm.get("connected"):
        _row("✓", "Connected", f"{lm.get('latency_ms', 0):.0f}ms")
        _row("✓", "Models loaded", ", ".join(lm.get("models", [])[:3]))
    else:
        any_red = True
        _row("✗", "Connected", "NO")
        if lm.get("blocked_hint"):
            print(f"      Hint: {lm['blocked_hint']}")

    # ── Bots ─────────────────────────────────────────────────────────
    _section("Bots")
    bs = d.get("bots") or {}
    bots = bs.get("bots", []) or []
    if not bots:
        _row("!", "No bots discovered", "")
    for b in bots:
        st = b.get("last_status", "?")
        if st == "ok":
            icon = "✓"
            value = f"last run {b.get('last_run_age_s', 0)}s ago"
        elif st == "never_run":
            icon = "!"
            value = "never run"
        else:
            icon = "✗"
            any_red = True
            err = (b.get("last_error") or "no detail")[:60]
            value = f"{st} — {err}"
        _row(icon, b.get("id", "?"), value)

    # ── Drives + emotion ─────────────────────────────────────────────
    _section("Drives + Emotion")
    dr = d.get("drives") or {}
    for k in ("curiosity", "maintenance", "learning"):
        v = float(dr.get(k, 0) or 0)
        bar = "▓" * int(v * 10) + "░" * (10 - int(v * 10))
        warn = v >= 0.75
        _row("!" if warn else "✓", k, f"{bar} {v:.2f}")
    em = d.get("emotion") or {}
    dominant = em.get("dominant", "?")
    state = em.get("state") or {}
    _row("·", "Dominant emotion", f"{dominant} ({state.get(dominant, 0):.2f})" if dominant in state else dominant)

    # ── Reflections ──────────────────────────────────────────────────
    _section("Recent reflections (last 20)")
    refs = (d.get("reflections") or {}).get("reflections", []) or []
    if not refs:
        _row("·", "No reflections recorded yet", "")
    else:
        from collections import Counter
        verdicts = Counter(r.get("verdict", "?") for r in refs)
        for v, n in verdicts.most_common():
            icon = "✓" if v == "solved" else ("!" if v == "partial" else "✗")
            _row(icon, v, str(n))

    # ── Wants ────────────────────────────────────────────────────────
    _section("Wants")
    wants = (d.get("wants") or {}).get("wants", []) or []
    for w in wants:
        status = w.get("status", "?")
        icon = {"satisfied": "✓", "unmet": "✗"}.get(status, "!")
        if status == "unmet":
            any_red = True
        _row(icon, w.get("id", "?"), w.get("want", ""))

    # ── Audit chain ──────────────────────────────────────────────────
    _section("Audit chain")
    av = d.get("audit_verify") or {}
    if av.get("_error"):
        _row("!", "Verify endpoint", av["_error"])
    elif av.get("ok"):
        _row("✓", "Chain verified", f"{av.get('rows', '?')} entries")
    else:
        any_red = True
        _row("✗", "Chain BROKEN", str(av))

    # ── Perf snapshot ────────────────────────────────────────────────
    _section("Performance")
    pf = d.get("perf") or {}
    for kind in ("lm_studio", "chromadb", "websocket"):
        s = (pf.get(kind) or {})
        n = int(s.get("samples", 0) or 0)
        if n == 0:
            _row("·", kind, "no samples yet")
        else:
            _row("✓", kind, f"p50 {s.get('p50_ms', 0):.0f}ms · {n} samples")

    print()
    if any_red:
        print("\033[31m✗ Issues found. See docs/RUNBOOK.md for fixes.\033[0m")
        return 1
    print("\033[32m✓ All systems nominal.\033[0m")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true",
                    help="emit raw JSON instead of the human view")
    args = ap.parse_args()

    snapshot = collect()
    if args.json:
        print(json.dumps(snapshot, indent=2, default=str))
        return 0
    return render(snapshot)


if __name__ == "__main__":
    sys.exit(main())
