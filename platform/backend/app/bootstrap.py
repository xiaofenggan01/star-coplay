from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from common.settings.env import Settings, load_optional_env_files
from common.db.pool import create_pool
from app.container import Container

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _hosted_audit_path(settings: Settings) -> Path:
    """Keep the formal Hosted audit under the canonical application log root."""
    return Path(settings.log_dir).resolve() / "audit/hosted-mcp-audit.jsonl"


class _HostedSessionManifestProvider:
    """Await the async Hosted registry at the CapabilityRuntime boundary."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    async def entries(self, context: Any):
        return await self._registry.capability_entries(context.game_session_ref)


class _HostedSessionReadinessProbe:
    """Confirms the authenticated game route exposes the Runtime's capabilities."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    async def __call__(self, session: Any, allowed_capabilities: tuple[str, ...]) -> bool:
        from modules.mcp.release_manifest import PROTO_CAPABILITIES, V2_CAPABILITIES

        game_session_id = session.hosted_game_session_id
        route = await self._registry.resolve_authenticated(game_session_id)
        entries = await self._registry.capability_entries(game_session_id)
        if route is None or entries is None:
            return False
        facts = session.hosted_facts
        if route.facts.capability_digest != facts.capability_digest:
            return False
        advertised = {
            (entry.get("source_surface"), entry.get("capability_id"))
            for entry in entries
            if entry.get("capability_contract_version") == "2.4.5"
        }
        grants = set(allowed_capabilities)
        required = {
            ("protocol-2.4.5", capability_id)
            for capability_id in grants.intersection(PROTO_CAPABILITIES)
        }
        required.update(
            ("v2-compat", capability_id)
            for capability_id in grants.intersection(V2_CAPABILITIES)
        )
        return required.issubset(advertised)


async def create_container(*, runtime_authority_issuer: Any = None,
                           gateway_event_consumer: Any = None,
                           game_session_event_hook: Any = None,
                           hosted_session_registry: Any = None,
                           emergency_runtime_terminator: Any = None) -> Container:
    """Create all shared resources and top-level module instances.

    TRANSITIONAL: Gateway 当前内部自建 auth/registry/telemetry。
    后续统一认证、ConfigService、统一 telemetry 就绪后，
    应改为外部注入（Gateway 构造函数已支持可选参数）。
    """
    load_optional_env_files(REPO_ROOT)

    settings = Settings.from_env()

    db_pool = await create_pool(settings.database_url)
    from modules.config.system_config_client import DbConfigRepository, SystemConfigClient
    from modules.runtime_manager.rollout import HostedRolloutProvider

    db_config_repository = DbConfigRepository(db_pool)
    system_config_client = SystemConfigClient(
        repository=db_config_repository,
        default_environment=settings.config_environment,
    )
    hosted_rollout_provider = HostedRolloutProvider(
        system_config_client,
        environment=settings.config_environment,
    )

    from modules.gateway.server import Gateway, GatewayConfig
    from modules.http_api.server import HttpApiServer, HttpApiConfig
    from modules.runtime.agent_worker import AgentSessionWorker
    from modules.agentee.repository import DbAgenteeRepository

    agentee_repo = DbAgenteeRepository(db_pool)
    worker = AgentSessionWorker(platform_url=settings.platform_url, agentee_repo=agentee_repo, db_pool=db_pool)

    gw_config = GatewayConfig.from_env()

    ai_relay_account_service = None
    from modules.agentee.persona_service import PersonaInternalService
    persona_service = PersonaInternalService(
        newapi_client=None,
        newapi_base_url=settings.newapi_base_url,
        newapi_sys_key=settings.newapi_sys_key,
    )
    system_llm_router = None
    if settings.newapi_base_url and settings.newapi_admin_access_token:
        from common.relay import NewAPIClient, NewAPIClientConfig
        from modules.ai_relay_account.config import AIRelayConfigProvider
        from modules.ai_relay_account.service import (
            AIRelayAccountService,
            DbQuotaAdjustmentRepository,
            DbRelayAccountRepository,
        )
        from modules.llm.system_router import SystemLLMRouter

        ai_relay_config_provider = AIRelayConfigProvider(
            config_client=system_config_client,
            environment=settings.config_environment,
        )
        relay_account_repository = DbRelayAccountRepository(db_pool)
        newapi_client = NewAPIClient(
            NewAPIClientConfig(
                base_url=settings.newapi_base_url,
                access_token=settings.newapi_admin_access_token,
                user_id=settings.newapi_admin_user_id or None,
                timeout_seconds=30.0,
            )
        )
        ai_relay_account_service = AIRelayAccountService(
            repository=relay_account_repository,
            newapi_client=newapi_client,
            config_provider=ai_relay_config_provider,
            quota_adjustment_repository=DbQuotaAdjustmentRepository(db_pool),
        )
        persona_service = PersonaInternalService(
            newapi_client=newapi_client,
            newapi_base_url=settings.newapi_base_url,
            newapi_sys_key=settings.newapi_sys_key,
            system_config_client=system_config_client,
        )
        system_llm_router = SystemLLMRouter(
            account_reader=relay_account_repository,
            config_provider=ai_relay_config_provider,
            newapi_base_url=settings.newapi_base_url,
            db_pool=db_pool,
        )

    runtime_manager = None
    runtime_manager_workers = None
    hosted_mcp_endpoint = None
    runtime_manager_health = None
    hosted_runtime_activator = None
    if settings.runtime_manager_enabled:
        if db_pool is None:
            raise RuntimeError("Runtime Manager requires DATABASE_URL")
        if not settings.runtime_control_claims_secret or not settings.runtime_control_services:
            raise RuntimeError("Runtime Manager requires dedicated control claims secret and service allowlist")
        from modules.runtime_manager.adapters.claude.adapter import ClaudeAgentRuntimeAdapter
        from modules.runtime_manager.adapter_config import DbAdapterConfigResolver
        from modules.gateway.hosted_session import HostedSessionCoordinator, HostedSessionRegistry
        from modules.mcp.context import HostedClaimsAuthority, SessionExposurePort, hosted_risk_policy
        from modules.mcp.skills.models import ExposureContext
        from modules.mcp.context.hosted_credential import HostedCredentialSigner, HostedCredentialVerifier, RevocationStore
        from modules.mcp.core.service import McpCore
        from modules.mcp.core.audit import DurableJsonlAuditSink
        from modules.mcp.manifest import build_manifest, build_tool_catalog
        from modules.mcp.release_manifest import default_mounted_skill_refs
        from modules.mcp.capabilities.runtime import RouterBackedCapabilityRuntime
        from modules.mcp.composition import build_mcp_composition
        from modules.mcp.capabilities.contracts import build_capability_contract_catalog
        from modules.mcp.transports.hosted_server import HostedMcpWebSocketEndpoint
        from modules.runtime_manager.async_application import AuthorityOutboxWorker, AsyncGatewayProjectionService
        from modules.runtime_manager.coordinator import (
            BindingOutboxAuthorityController, DbPersonaArtifactReader,
            BindingExpiryWorker,
            DurableGatewayEventPublisher, EffectiveActivityReporter, GatewayEventOutboxWorker,
            HostedGatewayEventConsumer, HostedMcpActivityReporter,
            RuntimeManagerCoordinator, RuntimeManagerWorkers,
        )
        from modules.runtime_manager.emergency_terminator import PosixEmergencyRuntimeTerminator
        from modules.runtime_manager.hosted_mcp_authority import (
            AsyncpgHostedMcpSecurityRepository, DurableHostedCredentialVerifier,
            HostedAuthorityWorker, HostedCredentialFileAuthority,
        )
        from modules.runtime_manager.persistence import (
            AsyncpgBindingRepository, AsyncpgGatewayEventOutboxRepository, AsyncpgGatewayProjectionRepository,
            AsyncpgRuntimeProductRepository,
        )
        from modules.runtime_manager.admission import RuntimeAdmissionPolicy
        from modules.llm.runtime_relay import (
            DbRuntimeLLMEnvironmentResolver, RuntimeLLMTokenSigner,
        )
        runtime_llm_signer = RuntimeLLMTokenSigner(
            settings.runtime_llm_relay_secret or settings.runtime_control_claims_secret,
        )
        runtime_llm_environment = DbRuntimeLLMEnvironmentResolver(
            db_pool,
            runtime_llm_signer,
            os.environ.get(
                "STARCOPLAY_RUNTIME_LLM_RELAY_BASE_URL",
                f"http://127.0.0.1:{settings.http_port}/internal/runtime-llm",
            ),
            default_model_tier=os.environ.get("AI_RELAY_AGENT_MODEL_TIER", "balanced"),
        )
        runtime_admission_policy = RuntimeAdmissionPolicy(
            allowed_user_ids=settings.hosted_runtime_allowlist,
            allow_all_authenticated=(
                settings.hosted_runtime_admission_mode == "all_authenticated"
            ),
            rollout_provider=hosted_rollout_provider,
            max_active_global=settings.runtime_max_active_global,
            max_active_per_user=settings.runtime_max_active_per_user,
        )
        runtime_repository = AsyncpgRuntimeProductRepository(db_pool, runtime_admission_policy)
        binding_repository = AsyncpgBindingRepository(db_pool)
        gateway_projection_repository = AsyncpgGatewayProjectionRepository(db_pool)
        gateway_event_outbox_repository = AsyncpgGatewayEventOutboxRepository(db_pool)
        from modules.gateway.hosted_runtime_input import (
            HostedBackgroundJobCompletionRelay,
            HostedRuntimeInputDispatcher,
            HostedSayReflectionRegistry,
            HostedTurnCompletionRelay,
        )
        hosted_say_reflections = HostedSayReflectionRegistry()
        hosted_background_completion_relay = HostedBackgroundJobCompletionRelay()
        hosted_turn_completion_relay = HostedTurnCompletionRelay()
        hosted_session_registry = hosted_session_registry or HostedSessionRegistry(
            say_reflections=hosted_say_reflections,
        )
        durable_publisher = DurableGatewayEventPublisher(gateway_event_outbox_repository)
        game_session_event_hook = game_session_event_hook or HostedSessionCoordinator(
            hosted_session_registry, durable_publisher,
        )
        projection = AsyncGatewayProjectionService(
            gateway_projection_repository, game_session_event_hook,
        )
        gateway_event_consumer = gateway_event_consumer or HostedGatewayEventConsumer(
            projection, binding_repository, runtime_repository,
        )
        durable_publisher.bind(gateway_event_consumer)
        gateway_event_worker = GatewayEventOutboxWorker(
            gateway_event_outbox_repository, gateway_event_consumer,
        )

        security_repository = AsyncpgHostedMcpSecurityRepository(db_pool)
        key_store = await security_repository.load_or_create_keys()
        credential_signer = HostedCredentialSigner(key_store)
        credential_verifier = DurableHostedCredentialVerifier(
            HostedCredentialVerifier(key_store, RevocationStore()), security_repository,
        )
        from modules.runtime_manager.correlation import ActiveTurnCorrelationRegistry
        turn_correlation_registry = ActiveTurnCorrelationRegistry()
        hosted_claims = HostedClaimsAuthority(
            credential_verifier, binding_repository,
            turn_correlation_registry=turn_correlation_registry,
        )
        hosted_exposure_port = SessionExposurePort(
            hosted_claims,
            lambda context: hosted_session_registry.capability_entries(context.game_session_ref),
            build_capability_contract_catalog(REPO_ROOT),
            lambda context: ExposureContext(
                game="stardew-valley",
                mounted_skill_refs=default_mounted_skill_refs(
                    REPO_ROOT / "platform/backend/modules/mcp/skills/builtin"
                ),
                risk_policy=hosted_risk_policy(context), confirmation_available=False,
                mount_revision="empty", policy_revision="empty",
                binding_ref=context.binding_ref, binding_version=context.binding_version,
                game_session_ref=context.game_session_ref,
            ),
        )
        catalog = build_tool_catalog(build_manifest(REPO_ROOT))
        from modules.gateway.runtime_contracts import GatewayRouter
        from modules.runtime_manager.router_validation import RuntimeBindingProjectionValidator
        hosted_router = GatewayRouter(
            hosted_session_registry, game_session_event_hook,
            RuntimeBindingProjectionValidator(binding_repository),
        )
        hosted_audit_sink = DurableJsonlAuditSink(
            _hosted_audit_path(settings),
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backup_count,
        )
        hosted_capability_runtime = RouterBackedCapabilityRuntime(
            hosted_router,
            _HostedSessionManifestProvider(hosted_session_registry),
            build_capability_contract_catalog(REPO_ROOT),
            context_ref=lambda context: context.game_session_ref,
            activity=HostedMcpActivityReporter(EffectiveActivityReporter(runtime_repository)),
            audit=hosted_audit_sink,
        )
        hosted_composition = build_mcp_composition(
            repo_root=REPO_ROOT,
            exposure_port=hosted_exposure_port,
            capability_runtime=hosted_capability_runtime,
            authority=hosted_claims,
            controlled_enabled=False,
            audit=hosted_audit_sink,
            background_completion_hook=hosted_background_completion_relay,
        )
        hosted_core = McpCore(
            hosted_claims, hosted_router, catalog,
            activity=HostedMcpActivityReporter(EffectiveActivityReporter(runtime_repository)),
            adapt_results=True, projection=hosted_composition.projection,
        )
        hosted_mcp_endpoint = HostedMcpWebSocketEndpoint(
            hosted_core, hosted_claims, hosted_exposure_port,
            max_inflight_calls=settings.hosted_mcp_max_inflight_calls,
            queue_size=settings.hosted_mcp_queue_size,
        )

        workspace_root = Path(settings.runtime_manager_workspace_root)
        runtime_authority_issuer = runtime_authority_issuer or HostedCredentialFileAuthority(
            credential_signer, security_repository, binding_repository, workspace_root,
        )
        emergency_runtime_terminator = emergency_runtime_terminator or PosixEmergencyRuntimeTerminator(
            workspace_root
        )
        hosted_endpoint_url = os.environ.get(
            "STARCOPLAY_HOSTED_MCP_ENDPOINT",
            f"ws://127.0.0.1:{settings.http_port}/mcp/hosted/ws",
        )
        claude_adapter_metadata = {"hosted_mcp_endpoint": hosted_endpoint_url}
        if settings.claude_binary:
            claude_adapter_metadata["claude_binary"] = settings.claude_binary
        if settings.claude_cli_path:
            claude_adapter_metadata["claude_cli_path"] = settings.claude_cli_path
        runtime_manager = RuntimeManagerCoordinator(
            runtime_repository, DbPersonaArtifactReader(db_pool),
            BindingOutboxAuthorityController(binding_repository, workspace_root),
            {"claude": ClaudeAgentRuntimeAdapter()},
            workspace_root,
            emergency_terminator=emergency_runtime_terminator,
            adapter_metadata={"claude": claude_adapter_metadata},
            adapter_config_resolver=DbAdapterConfigResolver(db_pool),
            runtime_env_resolver=runtime_llm_environment,
            binding_repository=binding_repository,
            gateway_projection_repository=gateway_projection_repository,
            gateway_snapshot_converger=gateway_event_consumer,
            turn_correlation_registry=turn_correlation_registry,
            turn_completion_hook=hosted_turn_completion_relay,
            max_input_bytes=settings.runtime_max_input_bytes,
            max_inflight_inputs=settings.runtime_max_inflight_inputs,
        )
        outbox_worker = AuthorityOutboxWorker(binding_repository, runtime_authority_issuer)
        authority_worker = (
            HostedAuthorityWorker(outbox_worker, runtime_authority_issuer, runtime_repository)
            if isinstance(runtime_authority_issuer, HostedCredentialFileAuthority) else outbox_worker
        )
        runtime_manager_workers = RuntimeManagerWorkers(
            runtime_manager,
            authority_worker,
            gateway_event_worker=gateway_event_worker,
            binding_expiry_worker=BindingExpiryWorker(binding_repository, gateway_event_consumer),
        )
        from modules.runtime_manager.health import RuntimeManagerHealthService
        runtime_manager_health = RuntimeManagerHealthService(
            runtime_manager,
            workers=runtime_manager_workers,
            authority_outbox=binding_repository,
            gateway_outbox=gateway_event_outbox_repository,
            audit_sink=hosted_audit_sink,
        )
        from modules.runtime_manager.session_activation import (
            DbDefaultAgenteeResolver, HostedRuntimeSessionActivator,
        )
        hosted_runtime_activator = HostedRuntimeSessionActivator(
            runtime_manager, DbDefaultAgenteeResolver(db_pool), authority_worker,
            capability_probe=_HostedSessionReadinessProbe(hosted_session_registry),
        )
        hosted_runtime_input_dispatcher = HostedRuntimeInputDispatcher(
            runtime_manager,
            hosted_say_reflections,
            background_jobs=hosted_composition.projection,
        )
        hosted_background_completion_relay.bind(hosted_runtime_input_dispatcher)
        hosted_turn_completion_relay.bind(hosted_runtime_input_dispatcher)

    gateway = Gateway(
        gw_config,
        worker_handle=worker.on_message,
        on_session_end=worker.on_session_end,
        game_session_event_hook=game_session_event_hook if gw_config.hosted_route_enabled else None,
        hosted_session_registry=hosted_session_registry if gw_config.hosted_route_enabled else None,
        hosted_user_ids=settings.hosted_runtime_allowlist,
        hosted_all_authenticated=(
            settings.hosted_runtime_admission_mode == "all_authenticated"
        ),
        hosted_rollout_provider=hosted_rollout_provider,
        hosted_runtime_activator=hosted_runtime_activator,
        hosted_runtime_input_dispatcher=(
            hosted_runtime_input_dispatcher if settings.runtime_manager_enabled else None
        ),
    )
    if runtime_manager_health is not None and settings.runtime_manager_enabled:
        runtime_manager_health.bind_hosted_diagnostics(
            gateway_reader=gateway.hosted_diagnostics_snapshot,
            input_reader=hosted_runtime_input_dispatcher.diagnostics_snapshot,
            mcp_reader=hosted_mcp_endpoint.diagnostics_snapshot,
        )
    http_config = HttpApiConfig.from_env()
    http_api = HttpApiServer(
        gateway,
        gateway.authenticator,
        http_config,
        db_pool=db_pool,
        agent_worker=worker,
        ai_relay_account_service=ai_relay_account_service,
        persona_service=persona_service,
        system_llm_router=system_llm_router,
        internal_api_token=settings.internal_api_token,
        config_environment=settings.config_environment,
        runtime_manager=runtime_manager,
        runtime_manager_enabled=settings.runtime_manager_enabled,
        hosted_mcp_endpoint=hosted_mcp_endpoint,
        runtime_control_claims_secret=settings.runtime_control_claims_secret,
        runtime_control_services=settings.runtime_control_services,
        runtime_admission_policy=(runtime_admission_policy if settings.runtime_manager_enabled else None),
        runtime_manager_health=runtime_manager_health,
        runtime_llm_token_signer=(runtime_llm_signer if settings.runtime_manager_enabled else None),
    )

    return Container(
        settings=settings,
        db_pool=db_pool,
        ai_relay_account_service=ai_relay_account_service,
        system_llm_router=system_llm_router,
        gateway=gateway,
        http_api=http_api,
        agent_worker=worker,
        runtime_manager=runtime_manager,
        runtime_manager_workers=runtime_manager_workers,
        runtime_authority_issuer=runtime_authority_issuer,
        gateway_event_consumer=gateway_event_consumer,
        game_session_event_hook=game_session_event_hook,
        emergency_runtime_terminator=emergency_runtime_terminator,
    )
