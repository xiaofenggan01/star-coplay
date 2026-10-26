"""Authenticated GameSession -> Hosted Runtime composition.

This module composes existing Runtime Manager boundaries.  It deliberately
owns no durable state and never writes Runtime or Binding tables directly.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from modules.mcp.release_manifest import PROTO_CAPABILITIES, V2_CAPABILITIES
from modules.runtime_manager.domain import (
    BindingState, DesiredState, RuntimeProductSpec, RuntimeState,
)

# Terminal generation contract: FAILED/STOPPED are terminal for their generation.
# A new activation must create generation+1, never reuse a terminal generation.
_TERMINAL_STATES = {RuntimeState.FAILED, RuntimeState.STOPPED}

HOSTED_DEFAULT_SKILL_GRANTS = (
    "skill:stardew.knowledge.search_wiki@1.0.0",
    "skill:stardew.knowledge.read_wiki@1.0.0",
    "skill:stardew.advanced.chop_trees@1.0.0",
    "skill:stardew.advanced.clear_farm@1.0.0",
    "skill:stardew.advanced.find_interactions@1.0.0",
    "skill:stardew.advanced.go_bed@1.0.0",
    "skill:stardew.advanced.go_shopping@1.0.0",
    "skill:stardew.navigation.go_to_place@1.0.0",
    "skill:stardew.advanced.harvest_crops@1.0.0",
    "skill:stardew.advanced.inspect@1.0.0",
    "skill:stardew.advanced.observe@1.0.0",
    "skill:stardew.advanced.pet_animals@1.0.0",
    "skill:stardew.advanced.search_facts@1.0.0",
    "skill:stardew.advanced.ship_items@1.0.0",
    "skill:stardew.advanced.walk_route@1.0.0",
    "skill:stardew.advanced.water_crops@1.0.0",
)
HOSTED_DEFAULT_RUNTIME_GRANTS = (
    *PROTO_CAPABILITIES,
    *V2_CAPABILITIES,
    *HOSTED_DEFAULT_SKILL_GRANTS,
)

log = logging.getLogger("runtime_manager.session_activation")

_IDENTITY_NAMESPACE = uuid.UUID("53544350-4c41-5943-4341-4c4c4f574c49")


@dataclass(frozen=True)
class DefaultAgenteeAuthority:
    agentee_id: str
    persona_artifact_ref: str


class DbDefaultAgenteeResolver:
    """Resolve the existing product-owned default agentee/persona authority."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def resolve(self, user_id: str) -> DefaultAgenteeAuthority:
        row = await self._pool.fetchrow(
            """SELECT p.agentee_id::text AS agentee_id, ps.updated_at, ps.persona_card
                 FROM public.agentee_profiles p
                 JOIN public.agentee_personas ps ON ps.agentee_id=p.agentee_id
                WHERE p.user_id=$1 AND p.is_default=true
                LIMIT 1""",
            user_id,
        )
        if row is None or not row["persona_card"] or row["updated_at"] is None:
            raise RuntimeError("default_agentee_persona_unavailable")
        agentee_id = row["agentee_id"]
        return DefaultAgenteeAuthority(
            agentee_id=agentee_id,
            persona_artifact_ref=f"persona/{agentee_id}/{row['updated_at'].isoformat()}",
        )


def stable_runtime_identity(user_id: str, agentee_id: str) -> tuple[str, str]:
    logical = uuid.uuid5(_IDENTITY_NAMESPACE, f"{user_id}\x1f{agentee_id}").hex
    return f"cc_{logical}", f"cc-default:{logical}"


class HostedRuntimeSessionActivator:
    """Fail-closed orchestration around an authenticated Hosted GameSession."""

    def __init__(self, coordinator, agentee_resolver, authority_worker, *,
                 credential_timeout_seconds: float = 10.0,
                 capability_probe: Callable[[object, tuple[str, ...]], Awaitable[bool]] | None = None,
                 capability_timeout_seconds: float = 10.0) -> None:
        self._coordinator = coordinator
        self._agentee_resolver = agentee_resolver
        self._authority_worker = authority_worker
        self._credential_timeout_seconds = credential_timeout_seconds
        self._capability_probe = capability_probe
        self._capability_timeout_seconds = capability_timeout_seconds

    async def prepare(self, session) -> RuntimeProductSpec:
        """Prepare an explicitly selected controller.

        Existing callers remain source compatible.  The trusted GameSession
        identity already carried by ``session`` is sufficient to distinguish
        an exact resume from a replacement; transport/controller selection
        remains Gateway-owned.
        """
        user_id = session.instance.user_id
        if not user_id:
            raise RuntimeError("authenticated_user_unavailable")
        authority = await self._agentee_resolver.resolve(user_id)
        runtime_id, activation_key = stable_runtime_identity(user_id, authority.agentee_id)
        current = await self._coordinator.repository.get(runtime_id)
        if current is None:
            generation = 1
        elif current.actual_state in _TERMINAL_STATES:
            generation = current.generation + 1
        else:
            generation = await self._generation_for_session(current, session)
        spec = RuntimeProductSpec(
            runtime_id=runtime_id,
            generation=generation,
            activation_key=activation_key,
            user_id=user_id,
            agentee_id=authority.agentee_id,
            adapter_kind="claude",
            adapter_config_ref="adapter/default",
            persona_artifact_ref=authority.persona_artifact_ref,
            idle_ttl_seconds=3600,
            allowed_capabilities=HOSTED_DEFAULT_RUNTIME_GRANTS,
        )
        # Hosted defaults are server-owned release policy. A stopped prior
        # generation may legitimately carry an older grant/persona snapshot.
        record = await self._coordinator.provision(
            spec, allow_terminal_intent_change=True,
        )
        await self._coordinator.ensure_binding(
            record.runtime_id, record.generation, user_id, authority.agentee_id,
            operation_id=(
                f"cc-binding:{activation_key}:{record.generation}:"
                f"{record.status_version}"
            ),
            game_session_id=session.hosted_game_session_id,
        )
        session.hosted_runtime_spec = record.spec
        return record.spec

    async def _generation_for_session(self, current, session) -> int:
        """Reuse only an exact GameSession; otherwise close the old generation."""
        if current.desired_state is DesiredState.STOPPED:
            raise RuntimeError("hosted_runtime_stop_in_progress")
        binding_service = getattr(self._coordinator, "binding_service", None)
        bindings = getattr(binding_service, "bindings", None)
        if bindings is None:
            # Compatibility for isolated tests/compositions without the
            # product Binding repository. Production always has this port.
            return current.generation
        binding = await bindings.find_for_runtime(current.runtime_id, current.generation)
        if binding is None or binding.game_session_id is None:
            return current.generation
        game_session_id = session.hosted_game_session_id
        nonce = getattr(getattr(session, "hosted_facts", None), "game_instance_nonce", None)
        same_instance = (
            binding.game_session_id == game_session_id
            and (binding.game_instance_nonce is None or nonce is None
                 or binding.game_instance_nonce == nonce)
        )
        if same_instance:
            return current.generation
        if binding.status is not BindingState.REVOKED:
            raise RuntimeError("replacement_binding_not_revoked")
        stopped = await self._coordinator.stop(
            current.runtime_id, current.generation,
            operation_id=(
                f"controller-replacement:{current.runtime_id}:"
                f"{current.generation}:{game_session_id}"
            ),
            reason="controller_replacement",
            require_durable_revocation=True,
        )
        if stopped.actual_state is not RuntimeState.STOPPED:
            raise RuntimeError("replacement_runtime_stop_incomplete")
        return current.generation + 1

    async def close(self, user_id: str, *, operation_id: str) -> None:
        """Apply an explicit ``/agent_control close`` without a successor.

        Gateway must first publish CLOSED so the Binding is durably REVOKED.
        This method then fences authority, waits for its durable ack and stops
        the current generation. A later explicit ``prepare`` creates gen+1.
        """
        if not user_id or not operation_id:
            raise ValueError("user_id and operation_id are required")
        authority = await self._agentee_resolver.resolve(user_id)
        runtime_id, _ = stable_runtime_identity(user_id, authority.agentee_id)
        current = await self._coordinator.repository.get(runtime_id)
        if current is None or current.actual_state is RuntimeState.STOPPED:
            return
        if current.spec.user_id != user_id or current.spec.agentee_id != authority.agentee_id:
            raise RuntimeError("hosted_runtime_owner_mismatch")
        binding_service = getattr(self._coordinator, "binding_service", None)
        bindings = getattr(binding_service, "bindings", None)
        if bindings is None:
            raise RuntimeError("binding_service_unavailable")
        binding = await bindings.find_for_runtime(runtime_id, current.generation)
        if binding is not None and binding.status is not BindingState.REVOKED:
            raise RuntimeError("explicit_close_binding_not_revoked")
        stopped = await self._coordinator.stop(
            runtime_id, current.generation, operation_id,
            reason="agent_control_closed", require_durable_revocation=True,
        )
        if stopped.actual_state is not RuntimeState.STOPPED:
            raise RuntimeError("explicit_close_runtime_stop_incomplete")

    async def activate(self, session) -> None:
        spec = session.hosted_runtime_spec
        if spec is None:
            raise RuntimeError("hosted_runtime_not_prepared")
        deadline = asyncio.get_running_loop().time() + self._credential_timeout_seconds
        while True:
            await self._authority_worker.run_once()
            if await self._coordinator.authority.is_credential_issued(spec.runtime_id, spec.generation):
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("hosted_runtime_credential_timeout")
            await asyncio.sleep(0.05)
        await self._coordinator.ensure(spec)
        if self._capability_probe is not None:
            deadline = asyncio.get_running_loop().time() + self._capability_timeout_seconds
            while not await self._capability_probe(session, tuple(spec.allowed_capabilities)):
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("hosted_runtime_capabilities_timeout")
                await asyncio.sleep(0.05)
        log.info(
            "Hosted Claude Runtime owns authenticated GameSession",
            extra={"event": "runtime.session.activated", "runtime_id": spec.runtime_id,
                   "generation": spec.generation, "user_id": spec.user_id,
                   "game_session_id": session.hosted_game_session_id, "outcome": "success"},
        )
