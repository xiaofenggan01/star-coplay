from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class AgentConfig:
    root_dir: Path
    bridge_dir: Path
    poll_interval: float
    result_timeout: float
    macro_result_timeout: float
    state_heartbeat_seconds: float
    reflect_mode: str
    memory_limit: int
    recent_limit: int
    llm_provider: str
    llm_model: str
    llm_api_key: str
    llm_base_url: str
    dry_run: bool
    evomap_enabled: bool
    memory_isolate_sessions: bool

    @property
    def memory_store_dir(self) -> Path:
        return self.root_dir / "agent" / "memory" / "store"

    @property
    def knowledge_dir(self) -> Path:
        return self.root_dir / "agent" / "knowledge"

    @property
    def debug_dir(self) -> Path:
        return self.root_dir / "agent" / "debug" / "store"

    @property
    def evomap_store_dir(self) -> Path:
        return self.root_dir / "agent" / "evomap" / "store"

    def ensure_dirs(self) -> None:
        if not self.dry_run:
            self.bridge_dir.mkdir(parents=True, exist_ok=True)
        self.memory_store_dir.mkdir(parents=True, exist_ok=True)
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        if self.evomap_enabled:
            self.evomap_store_dir.mkdir(parents=True, exist_ok=True)


def _env_truthy(name: str) -> bool:
    v = os.environ.get(name, "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _env_default_true(name: str) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return True
    return v not in {"0", "false", "no", "off"}


def load_config(root_dir: Path | None = None) -> AgentConfig:
    base_dir = root_dir or Path(__file__).resolve().parent.parent
    load_dotenv(base_dir / ".env")
    bridge_dir = Path(
        os.environ.get(
            "STARDEW_BRIDGE_DIR",
            r"D:\SteamLibrary\steamapps\common\Stardew Valley\Mods\StarCoPlay\bridge",
        )
    )
    dry_run = _env_truthy("STARCoplay_AGENT_DRY_RUN")
    _rt = float(os.environ.get("STARDEW_AGENT_RESULT_TIMEOUT", "12.0"))
    _mrt = os.environ.get("STARDEW_AGENT_MACRO_RESULT_TIMEOUT", "").strip()
    return AgentConfig(
        root_dir=base_dir,
        bridge_dir=bridge_dir,
        poll_interval=float(os.environ.get("STARDEW_AGENT_POLL", "0.15")),
        result_timeout=_rt,
        macro_result_timeout=float(_mrt) if _mrt else max(_rt * 3.0, 30.0),
        state_heartbeat_seconds=float(os.environ.get("STARDEW_AGENT_STATE_HEARTBEAT", "30.0")),
        reflect_mode=(os.environ.get("STARDEW_AGENT_REFLECT_MODE", "auto").strip().lower() or "auto"),
        memory_limit=int(os.environ.get("STARDEW_AGENT_MEMORY_LIMIT", "100")),
        recent_limit=int(os.environ.get("STARDEW_AGENT_RECENT_LIMIT", "10")),
        llm_provider=(
            os.environ.get("STARDEW_AGENT_LLM_PROVIDER", "").strip()
            or os.environ.get("LLM_PROVIDER", "").strip()
            or "openai_compatible"
        ),
        llm_model=(
            os.environ.get("LLM_MODEL", "").strip()
            or os.environ.get("STARDEW_AGENT_LLM_MODEL", "").strip()
        ),
        llm_api_key=(
            os.environ.get("API_KEY", "").strip()
            or os.environ.get("STARDEW_AGENT_LLM_API_KEY", "").strip()
        ),
        llm_base_url=(
            os.environ.get("BASE_URL", "").strip()
            or os.environ.get("STARDEW_AGENT_LLM_BASE_URL", "").strip()
        ),
        dry_run=dry_run,
        evomap_enabled=_env_truthy("EVOMAP_ENABLED"),
        memory_isolate_sessions=_env_default_true("STARDEW_AGENT_MEMORY_ISOLATE_SESSIONS"),
    )
