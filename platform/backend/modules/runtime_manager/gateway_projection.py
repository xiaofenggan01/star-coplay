"""Persistent projection consumer for versioned Gateway GameSession facts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Set

from modules.gateway.runtime_contracts import (
    GATEWAY_EVENT_CONTRACT_VERSION,
    GameSessionSnapshot,
    GameSessionSnapshotProvider,
    GatewayEvent,
)


@dataclass(frozen=True)
class ProjectionResult:
    snapshot: Optional[GameSessionSnapshot]
    duplicate: bool = False
    stale: bool = False
    converged_from_snapshot: bool = False


class GatewayProjectionRepository(Protocol):
    async def has_message(self, message_id: str) -> bool: ...
    async def get(self, game_session_id: str) -> Optional[GameSessionSnapshot]: ...
    async def commit(self, message_id: str, snapshot: Optional[GameSessionSnapshot]) -> None: ...


class InMemoryGatewayProjectionRepository:
    """Reference for a durable DB table: dedupe and snapshot commit atomically."""

    def __init__(self) -> None:
        self.seen: Set[str] = set()
        self.projections: Dict[str, GameSessionSnapshot] = {}

    async def has_message(self, message_id: str) -> bool:
        return message_id in self.seen

    async def get(self, game_session_id: str) -> Optional[GameSessionSnapshot]:
        return self.projections.get(game_session_id)

    async def find_available_for_owner(self, owner_user_id: str) -> Optional[GameSessionSnapshot]:
        """Return the sole available session projection for an owner."""
        candidates = sorted(
            (s for s in self.projections.values() if s.owner_user_id == owner_user_id and s.available),
            key=lambda s: s.session_version, reverse=True,
        )
        return candidates[0] if len(candidates) == 1 else None

    async def commit(self, message_id: str, snapshot: Optional[GameSessionSnapshot]) -> None:
        if snapshot is not None:
            self.projections[snapshot.game_session_id] = snapshot
        self.seen.add(message_id)


class GatewaySessionProjection:
    def __init__(self, snapshots: GameSessionSnapshotProvider, repository: GatewayProjectionRepository | None = None) -> None:
        self._snapshots = snapshots
        self._repository = repository or InMemoryGatewayProjectionRepository()

    async def consume(self, event: GatewayEvent) -> ProjectionResult:
        self._validate_event(event)
        if await self._repository.has_message(event.message_id):
            return ProjectionResult(await self._repository.get(event.aggregate_id), duplicate=True)
        current = await self._repository.get(event.aggregate_id)
        current_version = current.session_version if current else 0
        if event.aggregate_version <= current_version:
            await self._repository.commit(event.message_id, None)
            return ProjectionResult(current, stale=True)
        if event.aggregate_version > current_version + 1:
            snapshot = self._snapshots.get_snapshot(event.aggregate_id)
            if snapshot is None or snapshot.session_version < event.aggregate_version:
                # Do not consume message_id: a later snapshot refresh may retry.
                raise ValueError("gateway_snapshot_unavailable")
            self._validate_snapshot_identity(event.aggregate_id, snapshot)
            await self._repository.commit(event.message_id, snapshot)
            return ProjectionResult(snapshot, converged_from_snapshot=True)
        await self._repository.commit(event.message_id, event.snapshot)
        return ProjectionResult(event.snapshot)

    async def get(self, game_session_id: str) -> Optional[GameSessionSnapshot]:
        return await self._repository.get(game_session_id)

    @staticmethod
    def _validate_snapshot_identity(aggregate_id: str, snapshot: GameSessionSnapshot) -> None:
        if not aggregate_id or snapshot.game_session_id != aggregate_id or snapshot.session_version < 1:
            raise ValueError("invalid_gateway_snapshot_identity")

    def _validate_event(self, event: GatewayEvent) -> None:
        if event.contract_version != GATEWAY_EVENT_CONTRACT_VERSION:
            raise ValueError("unsupported_contract_version")
        if not event.message_id or event.occurred_at.tzinfo is None:
            raise ValueError("invalid_gateway_event_envelope")
        self._validate_snapshot_identity(event.aggregate_id, event.snapshot)
        if event.aggregate_version != event.snapshot.session_version:
            raise ValueError("aggregate_version_mismatch")
