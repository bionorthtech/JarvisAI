"""
compose_doctor — lint Docker Compose files for production-hygiene issues.

Heuristics (all read-only):
  HIGH:
    - image without an explicit tag, or with `:latest` (irreproducible)
    - host network mode (`network_mode: host`) — escapes container isolation
    - privileged: true
    - bind mount of `/` or `/var/run/docker.sock` (container escape vector)
  MEDIUM:
    - no `healthcheck` declared
    - no `restart` policy (defaults to "no" — process won't survive crashes)
    - read_only filesystem not enabled
    - `cap_add` includes broad caps (SYS_ADMIN, NET_ADMIN, ALL)
  LOW:
    - no `mem_limit` / `deploy.resources.limits.memory` set
    - no `cpus` / `deploy.resources.limits.cpus` set
    - no `user:` override (container runs as root inside)

Reports findings ordered worst-first. No fix is applied; the agent
just surfaces what to tighten. Compose Spec v3+ shape supported;
falls back gracefully on older docker-compose v2 syntax.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_COMPOSE_GLOBS = ("docker-compose.yml", "docker-compose.yaml",
                  "compose.yml", "compose.yaml",
                  "docker-compose.*.yml", "docker-compose.*.yaml")

_BROAD_CAPS = {"SYS_ADMIN", "NET_ADMIN", "ALL", "SYS_PTRACE", "SYS_MODULE"}


def _try_yaml() -> Any:
    """Import PyYAML lazily so the plugin loads even if yaml is missing.
    Returns the module or None."""
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError:
        return None


def _discover(path: Path) -> list[Path]:
    """Return one or more compose files. If `path` is a YAML file, just
    that. If it's a directory, glob the standard compose filenames."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    found: list[Path] = []
    for pattern in _COMPOSE_GLOBS:
        found.extend(path.glob(pattern))
    return sorted(set(found))


def _lint_service(svc: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply every check to one service block. Returns list of findings."""
    out: list[dict[str, Any]] = []

    def add(sev: str, code: str, detail: str) -> None:
        out.append({"service": svc, "severity": sev, "code": code, "detail": detail})

    image = body.get("image")
    build = body.get("build")
    if image:
        if ":" not in image or image.endswith(":latest"):
            add("HIGH", "image_unpinned",
                f"image `{image}` has no explicit version tag (pin to a sha256@ or :X.Y.Z)")
    elif not build:
        add("MEDIUM", "no_image_no_build",
            "neither `image` nor `build` declared — service won't start")

    # Network / privilege escapes
    net = body.get("network_mode")
    if net == "host":
        add("HIGH", "host_network",
            "`network_mode: host` shares the host network namespace — escapes container isolation")
    if body.get("privileged"):
        add("HIGH", "privileged",
            "`privileged: true` grants the container near-root host access")

    # Bind-mount escape vectors
    volumes = body.get("volumes") or []
    for v in volumes:
        if isinstance(v, str):
            src = v.split(":", 1)[0]
        elif isinstance(v, dict):
            src = v.get("source", "")
        else:
            continue
        src = (src or "").rstrip("/")
        if src == "" or src == "/":
            add("HIGH", "bind_root",
                f"bind-mount of host `/` ({v}) — container escape vector")
        if "docker.sock" in src or src.endswith("/var/run/docker.sock"):
            add("HIGH", "docker_sock",
                f"bind-mount of docker.sock ({v}) — equivalent to root on the host")

    # Cap escapes
    cap_add = body.get("cap_add") or []
    if any(c.upper() in _BROAD_CAPS for c in cap_add):
        add("MEDIUM", "broad_caps",
            f"`cap_add` includes broad capabilities ({cap_add}) — consider narrower caps")

    # Resilience
    if "healthcheck" not in body:
        add("MEDIUM", "no_healthcheck",
            "no `healthcheck` declared — orchestrators won't know when the container is ready")
    if "restart" not in body and "restart_policy" not in (body.get("deploy") or {}):
        add("MEDIUM", "no_restart",
            "no `restart` policy — process won't recover from crash (default is `no`)")
    if not body.get("read_only"):
        add("MEDIUM", "no_read_only",
            "`read_only: true` not set — root FS is writable, larger attack surface")

    # Resource caps (compose v2 inline OR v3 deploy.resources.limits)
    deploy = body.get("deploy") or {}
    limits = (deploy.get("resources") or {}).get("limits") or {}
    has_mem = "mem_limit" in body or "memory" in limits
    has_cpu = "cpus" in body or "cpus" in limits
    if not has_mem:
        add("LOW", "no_mem_limit",
            "no `mem_limit` / `deploy.resources.limits.memory` — runaway containers can OOM the host")
    if not has_cpu:
        add("LOW", "no_cpu_limit",
            "no `cpus` / `deploy.resources.limits.cpus` — no CPU cap")

    if not body.get("user"):
        add("LOW", "no_user_override",
            "no `user:` set — container runs as the image's default user (often root)")

    return out


def _severity_rank(sev: str) -> int:
    return {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(sev, 9)


async def compose_lint(path: str = ".") -> str:
    """Public tool entry. JSON-formatted result string (so the LM can
    quote it back to the user without extra formatting work)."""
    yaml = _try_yaml()
    if yaml is None:
        return json.dumps({
            "ok": False,
            "error": "PyYAML not installed — `pip install pyyaml` to enable compose_doctor.",
        })
    resolved = Path(path or ".").expanduser().resolve()
    files = _discover(resolved)
    if not files:
        return json.dumps({
            "ok": False,
            "error": f"no docker-compose file found at {resolved} "
                     "(searched docker-compose*.yml, compose*.yml)",
        })

    all_findings: list[dict[str, Any]] = []
    file_summaries: list[dict[str, Any]] = []

    for f in files:
        try:
            text = f.read_text()
            doc = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            all_findings.append({
                "file": str(f), "service": "(parse error)",
                "severity": "HIGH", "code": "yaml_parse",
                "detail": f"YAML parse failed: {e}",
            })
            continue
        except OSError as e:
            all_findings.append({
                "file": str(f), "service": "(read error)",
                "severity": "HIGH", "code": "io_error",
                "detail": str(e),
            })
            continue

        services = doc.get("services") or {}
        file_findings: list[dict[str, Any]] = []
        for name, body in services.items():
            if not isinstance(body, dict):
                continue
            for finding in _lint_service(name, body):
                finding["file"] = str(f.relative_to(resolved)
                                       if resolved in f.parents or f == resolved
                                       else f)
                file_findings.append(finding)
        all_findings.extend(file_findings)
        file_summaries.append({
            "file": str(f),
            "services": len(services),
            "findings": len(file_findings),
        })

    all_findings.sort(key=lambda x: (_severity_rank(x["severity"]),
                                     x.get("service", ""), x.get("code", "")))

    by_sev = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in all_findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    return json.dumps({
        "ok": True,
        "scanned_files": len(files),
        "total_findings": len(all_findings),
        "by_severity": by_sev,
        "files": file_summaries,
        "findings": all_findings,
    }, indent=2)
