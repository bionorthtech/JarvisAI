"""
External Connection Adapter Framework

Plugin-style interface for connecting JARVIS to external tools, APIs, and
hardware. Each adapter declares: id, kind, config schema, status check,
and (optionally) action handlers. Adapters are registered at runtime.

Builtin adapter kinds:
  - mqtt:     pub/sub messaging (paho-mqtt) — for Home Assistant, sensors
  - webhook:  outbound HTTP callbacks
  - external_api: opt-in HTTPS endpoints
  - hardware: serial / Arduino / GPIO — local-only

State persisted to ~/.jarvis/adapters.json.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import bus

ADAPTERS_FILE = Path.home() / ".jarvis" / "adapters.json"
ADAPTER_KINDS = ("mqtt", "webhook", "external_api", "hardware", "custom")


class Adapter:
    """Base class for external adapters. Subclasses override `status` and
    `dispatch` for their specific protocol.
    """

    def __init__(self, adapter_id: str, kind: str, config: dict[str, Any]):
        self.id = adapter_id
        self.kind = kind
        self.config = config
        self.enabled: bool = bool(config.get("enabled", True))
        self.created_at = time.time()
        self.last_status: dict[str, Any] = {}

    def status(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "enabled": self.enabled,
            "config_keys": list(self.config.keys()),
            "created_at": self.created_at,
            "last_status": self.last_status,
        }

    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"error": "adapter does not implement dispatch", "action": action}


def _is_loopback_url(url: str) -> bool:
    """True if a URL targets 127.0.0.1 / localhost / ::1. Used by
    WebhookAdapter to block external dispatch when SecurityConfig.
    internet_access is False (the default)."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")


class WebhookAdapter(Adapter):
    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"error": "adapter disabled"}
        if action != "post":
            return {"error": f"unsupported action {action}"}

        import urllib.request
        import urllib.error
        url = self.config.get("url")
        if not url:
            return {"error": "webhook url not configured"}

        # Offline guard — block non-loopback URLs unless the user has
        # explicitly enabled internet access. Fails closed if config
        # can't be read.
        try:
            from agent.core.config import config
            allow_external = bool(config.security.internet_access)
        except Exception:
            allow_external = False
        if not allow_external and not _is_loopback_url(url):
            return {
                "error": (
                    f"webhook url {url!r} is not loopback and "
                    "security.internet_access is False — refusing to dispatch."
                )
            }

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "jarvis"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.last_status = {"ok": True, "code": resp.status, "ts": time.time()}
                bus.publish("adapter.dispatched", "adapters", {
                    "id": self.id, "kind": self.kind, "ok": True,
                })
                return {"ok": True, "code": resp.status}
        except urllib.error.URLError as e:
            self.last_status = {"ok": False, "error": str(e)[:200], "ts": time.time()}
            bus.publish("adapter.error", "adapters", {
                "id": self.id, "error": str(e)[:200],
            })
            return {"error": str(e)[:200]}


class MQTTAdapter(Adapter):
    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"error": "adapter disabled"}
        try:
            import paho.mqtt.publish as publish
        except ImportError:
            return {"error": "paho-mqtt not installed — pip install paho-mqtt"}

        host = self.config.get("host", "127.0.0.1")
        port = int(self.config.get("port", 1883))
        topic = payload.get("topic") or self.config.get("default_topic")
        message = payload.get("message", "")
        if not topic:
            return {"error": "topic required"}
        try:
            publish.single(topic, payload=message, hostname=host, port=port)
            self.last_status = {"ok": True, "ts": time.time()}
            return {"ok": True}
        except Exception as e:
            self.last_status = {"ok": False, "error": str(e)[:200], "ts": time.time()}
            return {"error": str(e)[:200]}


_ADAPTER_FACTORIES: dict[str, Callable[[str, str, dict], Adapter]] = {
    "webhook": lambda i, k, c: WebhookAdapter(i, k, c),
    "mqtt":    lambda i, k, c: MQTTAdapter(i, k, c),
    "external_api": lambda i, k, c: WebhookAdapter(i, k, c),  # webhook is a fine default
    "hardware": lambda i, k, c: Adapter(i, k, c),
    "custom":   lambda i, k, c: Adapter(i, k, c),
}


class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, Adapter] = {}
        self._load()

    def _load(self):
        if not ADAPTERS_FILE.exists():
            return
        try:
            data = json.loads(ADAPTERS_FILE.read_text())
            for entry in data.get("adapters", []):
                self._build(entry["id"], entry["kind"], entry.get("config", {}),
                            persist=False)
        except Exception:
            pass

    def _persist(self):
        try:
            ADAPTERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            ADAPTERS_FILE.write_text(json.dumps({
                "adapters": [
                    {"id": a.id, "kind": a.kind, "config": a.config}
                    for a in self._adapters.values()
                ],
            }, indent=2))
        except Exception:
            pass

    def _build(self, adapter_id: str, kind: str, config: dict,
               persist: bool = True) -> Adapter:
        if kind not in ADAPTER_KINDS:
            raise ValueError(f"unknown adapter kind: {kind}")
        factory = _ADAPTER_FACTORIES.get(kind, _ADAPTER_FACTORIES["custom"])
        adapter = factory(adapter_id, kind, config)
        self._adapters[adapter_id] = adapter
        if persist:
            self._persist()
            bus.publish("adapter.registered", "adapters", {
                "id": adapter_id, "kind": kind,
            })
        return adapter

    def add(self, adapter_id: str, kind: str, config: dict) -> dict:
        if adapter_id in self._adapters:
            return {"error": f"adapter '{adapter_id}' already registered"}
        try:
            adapter = self._build(adapter_id, kind, config)
            return adapter.status()
        except ValueError as e:
            return {"error": str(e)}

    def remove(self, adapter_id: str) -> dict:
        a = self._adapters.pop(adapter_id, None)
        if not a:
            return {"error": "not found"}
        self._persist()
        bus.publish("adapter.removed", "adapters", {"id": adapter_id})
        return {"ok": True}

    def toggle(self, adapter_id: str, enabled: bool) -> dict:
        a = self._adapters.get(adapter_id)
        if not a:
            return {"error": "not found"}
        a.enabled = enabled
        a.config["enabled"] = enabled
        self._persist()
        return a.status()

    def dispatch(self, adapter_id: str, action: str, payload: dict) -> dict:
        a = self._adapters.get(adapter_id)
        if not a:
            return {"error": "not found"}
        return a.dispatch(action, payload)

    def list(self) -> list[dict]:
        return [a.status() for a in self._adapters.values()]


registry = AdapterRegistry()
