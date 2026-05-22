from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

def _resolve_jarvis_home() -> Path:
    env = os.environ.get("JARVIS_HOME", "").strip()
    if env: return Path(env).expanduser().resolve()
    return (Path.home() / "jarvis").resolve()

JARVIS_HOME: Path = _resolve_jarvis_home()
CONFIG_PATH: Path = JARVIS_HOME / "config" / "jarvis.toml"
DATA_DIR: Path    = JARVIS_HOME / "data"
LOG_DIR: Path     = JARVIS_HOME / "logs"

@dataclass(frozen=True)
class LMStudioConfig:
    base_url: str = "http://localhost:1234/v1"
    primary_model: str = "qwen2.5-coder-7b-instruct"
    secondary_model: str = ""
    timeout_seconds: int = 120
    max_retries: int = 3
    context_window: int = 7000
    max_output_tokens: int = 2048
    stream: bool = True

@dataclass(frozen=True)
class RoutingConfig:
    auto_route: bool = True
    complexity_threshold_medium: float = 0.60
    complexity_threshold_complex: float = 0.85
    enable_specialized_agents: bool = True

@dataclass(frozen=True)
class SecurityConfig:
    internet_access: bool = False
    sandbox_by_default: bool = True
    bwrap_path: str = "/usr/bin/bwrap"
    confirm_caution: bool = True
    confirm_danger: bool = True
    confirm_critical: bool = True
    audit_log: bool = True
    audit_log_path: str = str(DATA_DIR / "audit.db")
    max_session_tokens: int = 50_000
    max_tool_calls_per_loop: int = 20
    wrap_untrusted_content: bool = True

@dataclass(frozen=True)
class AgentConfig:
    workspace: str = str(Path.home() / "jarvis" / "workspace")
    per_project_context: bool = True
    heartbeat_enabled: bool = False
    heartbeat_interval_minutes: int = 60
    show_reasoning: bool = True
    rag_top_k: int = 8

@dataclass(frozen=True)
class UIConfig:
    theme: str = "cyber-clarity"
    global_hotkey: str = "Super+J"
    greeting_window: bool = True
    agent_port: int = 7478

@dataclass(frozen=True)
class JarvisConfig:
    lm_studio: LMStudioConfig
    routing:   RoutingConfig
    security:  SecurityConfig
    agent:     AgentConfig
    ui:        UIConfig
    def summary(self) -> str:
        return f"LM Studio: {self.lm_studio.base_url} model={self.lm_studio.primary_model}"

def _load_toml_file(path: Path) -> dict[str, Any]:
    if not path.exists(): return {}
    with open(path, "rb") as f: return tomllib.load(f)

def _build_config(raw: dict[str, Any]) -> JarvisConfig:
    def sec(n: str): return raw.get(n, {})
    return JarvisConfig(
        lm_studio=LMStudioConfig(**sec("lm_studio")),
        routing=RoutingConfig(**sec("routing")),
        security=SecurityConfig(**sec("security")),
        agent=AgentConfig(**sec("agent")),
        ui=UIConfig(**sec("ui")),
    )

def load_config(path: Optional[Path] = None) -> JarvisConfig:
    return _build_config(_load_toml_file(path or CONFIG_PATH))

config: JarvisConfig = load_config()