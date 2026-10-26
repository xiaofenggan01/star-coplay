"""Product lifecycle ordering above provider adapters."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, Protocol

from modules.runtime_manager.domain import DesiredState, RuntimeProductRecord, RuntimeState
from modules.runtime_manager.product_repository import BindingRepository, RuntimeProductRepository
from modules.runtime_manager.services import BindingService, RuntimeService


class RuntimeProcessLifecycle(Protocol):
    async def drain(self, runtime_id: str, generation: int, timeout_seconds: float) -> None: ...
    async def checkpoint(self, runtime_id: str, generation: int, deadline_seconds: float) -> None: ...
    async def stop(self, runtime_id: str, generation: int, reason: str, grace_seconds: float) -> None: ...
    async def cleanup(self, runtime_id: str, generation: int) -> None: ...
    async def is_alive(self, runtime_id: str, generation: int) -> bool: ...


class RuntimeLifecycleCoordinator:
    def __init__(
        self,
        runtimes: RuntimeProductRepository,
        bindings: BindingRepository,
        runtime_service: RuntimeService,
        binding_service: BindingService,
        process: RuntimeProcessLifecycle,
    ) -> None:
        self.runtimes = runtimes
        self.bindings = bindings
        self.runtime_service = runtime_service
        self.binding_service = binding_service
        self.process = process

    async def stop_revoke_first(self, runtime_id: str, generation: int, reason: str) -> RuntimeProductRecord:
        runtime = self.runtimes.get_generation(runtime_id, generation)
        if runtime is None:
            raise LookupError(runtime_id)
        if runtime.actual_state is RuntimeState.STOPPED:
            return runtime
        base_operation = f"lifecycle:{runtime_id}:{generation}:{reason}"
        binding = self.bindings.find_for_runtime(runtime_id, generation)
        if binding is not None and binding.status.value not in {"revoked", "expired"}:
            self.binding_service.revoke(
                binding.binding_id, binding.binding_version, reason,
                operation_id=f"{base_operation}:revoke",
            )
        # Revocation outbox is flushed by BindingService before process control.
        current = self.runtimes.get_generation(runtime_id, generation)
        assert current is not None
        if current.actual_state not in {RuntimeState.DRAINING, RuntimeState.STOPPING}:
            current = self.runtime_service.set_desired_state(
                runtime_id, generation, DesiredState.STOPPED,
                operation_id=f"{base_operation}:desired-stopped",
                expected_status_version=current.status_version,
            )
        if current.actual_state is RuntimeState.DRAINING:
            try:
                await asyncio.wait_for(
                    self.process.drain(runtime_id, generation, timeout_seconds=5.0), timeout=5.1
                )
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.wait_for(
                    self.process.checkpoint(runtime_id, generation, deadline_seconds=5.0), timeout=5.1
                )
            except Exception:
                # Checkpoint is best effort; authority is already fenced.
                pass
            current = self.runtime_service.transition_actual(
                runtime_id, generation, RuntimeState.STOPPING, current.status_version, ready=False
            )
        await asyncio.wait_for(
            self.process.stop(runtime_id, generation, reason, grace_seconds=5.0), timeout=5.1
        )
        await self.process.cleanup(runtime_id, generation)
        return self.runtime_service.transition_actual(
            runtime_id, generation, RuntimeState.STOPPED, current.status_version,
            ready=False, exit_reason=reason,
        )

    async def enforce_idle_ttl(self, runtime_id: str, now: Optional[datetime] = None) -> bool:
        runtime = self.runtimes.get(runtime_id)
        if runtime is None or not runtime.active or runtime.last_effective_activity_at is None:
            return False
        moment = now or datetime.now(timezone.utc)
        if (moment - runtime.last_effective_activity_at).total_seconds() < runtime.spec.idle_ttl_seconds:
            return False
        await self.stop_revoke_first(runtime.runtime_id, runtime.generation, "idle_ttl_expired")
        return True

    async def reconcile_runtime(self, runtime_id: str) -> RuntimeProductRecord:
        runtime = self.runtimes.get(runtime_id)
        if runtime is None:
            raise LookupError(runtime_id)
        alive = await self.process.is_alive(runtime.runtime_id, runtime.generation)
        if runtime.desired_state is DesiredState.STOPPED:
            if alive:
                return await self.stop_revoke_first(runtime.runtime_id, runtime.generation, "reconcile_desired_stopped")
            current = runtime
            if current.actual_state is RuntimeState.DRAINING:
                current = self.runtime_service.transition_actual(
                    current.runtime_id, current.generation, RuntimeState.STOPPING,
                    current.status_version, ready=False,
                )
            if current.actual_state is RuntimeState.STOPPING:
                await self.process.cleanup(current.runtime_id, current.generation)
                return self.runtime_service.transition_actual(
                    current.runtime_id, current.generation, RuntimeState.STOPPED,
                    current.status_version, ready=False, exit_reason="reconcile_process_missing",
                )
            return current
        if runtime.desired_state is DesiredState.RUNNING and not alive and runtime.active:
            return self.runtime_service.transition_actual(
                runtime.runtime_id, runtime.generation, RuntimeState.FAILED,
                runtime.status_version, ready=False, exit_reason="reconcile_process_missing",
            )
        return runtime
