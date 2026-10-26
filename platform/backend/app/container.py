from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from modules.ai_relay_account.service import AIRelayAccountService
    from modules.gateway.server import Gateway
    from modules.http_api.server import HttpApiServer
    from modules.llm.system_router import SystemLLMRouter
    from modules.runtime.agent_worker import AgentSessionWorker

from common.settings.env import Settings


@dataclass
class Container:
    """Holds all shared resources and top-level module instances.

    TRANSITIONAL: Gateway 当前内部自建 auth/registry/telemetry。
    后续统一认证、ConfigService、统一 telemetry 就绪后，
    应改为外部注入（Gateway 构造函数已支持可选参数）。
    """

    settings: Settings
    db_pool: Any  # asyncpg.Pool | None
    ai_relay_account_service: AIRelayAccountService | None
    system_llm_router: SystemLLMRouter | None
    gateway: Gateway
    http_api: HttpApiServer
    agent_worker: AgentSessionWorker
    runtime_manager: Any = None
    runtime_manager_workers: Any = None
    runtime_authority_issuer: Any = None
    gateway_event_consumer: Any = None
    game_session_event_hook: Any = None
    emergency_runtime_terminator: Any = None
