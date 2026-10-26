from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from modules.runtime_manager.domain import (
    AuthorityIssuanceIntent,
    AuthorityRevocationIntent,
    BindingState,
    Conflict,
    RuntimeBinding,
)
from modules.runtime_manager.persistence import AsyncpgBindingRepository
from modules.runtime_manager.product_repository import OperationResult


MIGRATION = (
    Path(__file__).parents[3]
    / "supabase"
    / "migrations"
    / "20260713000000_runtime_manager_control_plane.sql"
)
INPUT_OPERATION_MIGRATION = (
    Path(__file__).parents[3] / "supabase" / "migrations"
    / "20260713010000_runtime_input_operation_kind.sql"
)


def normalized_sql() -> str:
    return " ".join(MIGRATION.read_text().lower().split())


def test_migration_is_additive_and_defines_all_product_tables() -> None:
    sql = normalized_sql()
    for table in (
        "runtime_instances",
        "runtime_bindings",
        "runtime_operations",
        "runtime_authority_outbox",
        "gateway_session_projection",
        "gateway_event_dedupe",
    ):
        assert f"create table public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
    assert "alter table public.agent_instances" not in sql
    assert "create table public.agent_instances" not in sql


def test_input_idempotency_additively_extends_the_single_operation_authority() -> None:
    sql = " ".join(INPUT_OPERATION_MIGRATION.read_text().lower().split())
    assert "alter table public.runtime_operations" in sql
    assert "'runtime', 'binding', 'input'" in sql
    assert "create table" not in sql


def test_migration_enforces_generation_status_and_binding_fences() -> None:
    sql = normalized_sql()
    assert "primary key (runtime_id, generation)" in sql
    assert "uniq_runtime_active_activation" in sql
    assert "where actual_state <> 'stopped'" in sql
    assert "unique (runtime_id, generation)" in sql
    assert "binding_version >= 1" in sql
    assert "lease_epoch >= 0" in sql
    assert "status <> 'active' or" in sql
    assert "game_instance_nonce is not null" in sql
    assert "capability_digest is not null" in sql
    assert "resume_identity is not null" in sql
    assert "lease_epoch >= 1" in sql
    assert "activation_intent_digest" in sql
    assert "payload_digest" in sql
    assert "gateway_session_projection" in sql and "resume_identity" in sql


def test_public_schema_has_refs_and_no_credential_or_provider_session_columns() -> None:
    sql = normalized_sql()
    assert "adapter_config_ref" in sql
    assert "persona_artifact_ref" in sql
    forbidden_columns = (
        "credential text",
        "credential_value",
        "access_token text",
        "refresh_token text",
        "api_key text",
        "claude_session",
        "provider_session",
    )
    assert all(column not in sql for column in forbidden_columns)


class _Transaction:
    def __init__(self, conn: "RecordingConnection") -> None:
        self.conn = conn

    async def __aenter__(self):
        self.conn.events.append("tx:begin")

    async def __aexit__(self, exc_type, exc, tb):
        self.conn.events.append("tx:rollback" if exc_type else "tx:commit")


class _Acquire:
    def __init__(self, conn: "RecordingConnection") -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_):
        return None


class RecordingConnection:
    def __init__(self, row: dict, *, cas_succeeds: bool = True) -> None:
        self.row = row
        self.cas_succeeds = cas_succeeds
        self.events: list[str] = []
        self.operation_exists = False

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, sql, *args):
        compact = " ".join(sql.split()).lower()
        if "from public.runtime_operations" in compact:
            self.events.append("operation:lookup")
            return None
        if "update public.runtime_bindings" in compact:
            self.events.append("binding:cas")
            return self.row if self.cas_succeeds else None
        raise AssertionError(compact)

    async def execute(self, sql, *args):
        compact = " ".join(sql.split()).lower()
        if "insert into public.runtime_operations" in compact:
            self.events.append("operation:insert")
        elif "insert into public.runtime_authority_outbox" in compact:
            self.events.append("outbox:insert")
        else:
            raise AssertionError(compact)


class RecordingPool:
    def __init__(self, conn: RecordingConnection) -> None:
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def binding_row(**changes):
    row = {
        "binding_id": "binding-1",
        "runtime_id": "runtime-1",
        "generation": 1,
        "user_id": "user-1",
        "agentee_id": "agentee-1",
        "binding_version": 2,
        "lease_epoch": 1,
        "status": "active",
        "game_session_id": "game-1",
        "game_instance_nonce": "nonce-1",
        "capability_digest": "digest-1",
        "resume_identity": "resume-1",
        "bound_at": None,
        "expires_at": None,
        "suspended_at": None,
        "revoked_at": None,
        "contract_version": "starcoplay.binding.v1",
    }
    row.update(changes)
    return row


@pytest.mark.asyncio
async def test_binding_cas_operation_snapshot_and_outboxes_are_one_transaction() -> None:
    conn = RecordingConnection(binding_row())
    repository = AsyncpgBindingRepository(RecordingPool(conn))
    binding = RuntimeBinding(**{
        key: value for key, value in binding_row().items() if key not in {"status"}
    }, status=BindingState.ACTIVE)
    operation = OperationResult("binding-1", "activate-1", "a" * 64, binding)
    revoke = AuthorityRevocationIntent("revoke-1", "binding-1", 1, 1, "replace")
    issue = AuthorityIssuanceIntent("issue-1", binding, ("game:read",), ("say",), "jti-1")

    result = await repository.transition_with_operation(binding, 1, operation, revoke, issue)

    assert result == binding
    assert conn.events == [
        "tx:begin",
        "operation:lookup",
        "binding:cas",
        "operation:insert",
        "outbox:insert",
        "outbox:insert",
        "tx:commit",
    ]


@pytest.mark.asyncio
async def test_stale_binding_cas_rolls_back_before_operation_or_outbox() -> None:
    conn = RecordingConnection(binding_row(), cas_succeeds=False)
    repository = AsyncpgBindingRepository(RecordingPool(conn))
    binding = RuntimeBinding(**{
        key: value for key, value in binding_row().items() if key not in {"status"}
    }, status=BindingState.ACTIVE)
    operation = OperationResult("binding-1", "activate-1", "a" * 64, binding)

    with pytest.raises(Conflict, match="binding_version"):
        await repository.transition_with_operation(binding, 1, operation, None)
    assert conn.events == [
        "tx:begin", "operation:lookup", "binding:cas", "operation:lookup", "tx:rollback"
    ]


@pytest.mark.asyncio
async def test_two_stale_cas_attempts_cannot_both_publish_results() -> None:
    winner = RecordingConnection(binding_row())
    loser = RecordingConnection(binding_row(), cas_succeeds=False)
    binding = RuntimeBinding(**{
        key: value for key, value in binding_row().items() if key not in {"status"}
    }, status=BindingState.ACTIVE)
    operation = OperationResult("binding-1", "activate-1", "a" * 64, binding)

    outcomes = await asyncio.gather(
        AsyncpgBindingRepository(RecordingPool(winner)).transition_with_operation(binding, 1, operation, None),
        AsyncpgBindingRepository(RecordingPool(loser)).transition_with_operation(binding, 1, operation, None),
        return_exceptions=True,
    )
    assert sum(isinstance(value, RuntimeBinding) for value in outcomes) == 1
    assert sum(isinstance(value, Conflict) for value in outcomes) == 1
    assert winner.events[-1] == "tx:commit"
    assert loser.events[-1] == "tx:rollback"


def test_authority_outbox_payload_is_retryable_but_contains_no_authority_material() -> None:
    source = Path(__file__).parents[2] / "modules" / "runtime_manager" / "persistence.py"
    text = source.read_text().lower()
    assert "claim_pending_intents" in text
    assert "skip locked" in text
    assert "retry_intent" in text
    for forbidden in ("credential_ref", "access_token", "refresh_token", "api_key"):
        assert forbidden not in text


class OutboxConnection:
    def __init__(self, state: dict) -> None:
        self.state = state

    def transaction(self):
        return _Transaction(self)

    @property
    def events(self):
        return self.state.setdefault("events", [])

    async def fetch(self, sql, limit):
        assert "skip locked" in sql.lower()
        if self.state.get("processed"):
            return []
        self.state["claimed"] = True
        self.state["attempt_count"] = self.state.get("attempt_count", 0) + 1
        return [{"intent_id": "issue-1", "attempt_count": self.state["attempt_count"]}][:limit]


class OutboxPool:
    def __init__(self) -> None:
        self.state = {}

    def acquire(self):
        return _Acquire(OutboxConnection(self.state))

    async def execute(self, sql, *args):
        compact = " ".join(sql.split()).lower()
        if "available_at=now()+make_interval" in compact:
            self.state["claimed"] = False
            self.state["last_error"] = args[1]
        elif "processed_at=now()" in compact:
            self.state["processed"] = True
            self.state["claimed"] = False
        else:
            raise AssertionError(compact)


@pytest.mark.asyncio
async def test_outbox_retry_and_ack_survive_repository_reconstruction() -> None:
    pool = OutboxPool()
    first_process = AsyncpgBindingRepository(pool)
    claimed = await first_process.claim_pending_intents()
    assert claimed[0]["attempt_count"] == 1
    await first_process.retry_intent("issue-1", "issuer unavailable", 1)

    restarted_process = AsyncpgBindingRepository(pool)
    claimed_again = await restarted_process.claim_pending_intents()
    assert claimed_again[0]["attempt_count"] == 2
    await restarted_process.acknowledge_intent("issue-1")
    assert await AsyncpgBindingRepository(pool).claim_pending_intents() == []
