from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REQUIRE_PRODUCT_EVIDENCE = os.environ.get("STARCOPLAY_REQUIRE_PRODUCT_EVIDENCE") == "1"

if _REQUIRE_PRODUCT_EVIDENCE:
    import asyncpg as _asyncpg_mod
    asyncpg = _asyncpg_mod
else:
    asyncpg = pytest.importorskip("asyncpg")

from modules.gateway.runtime_contracts import GameSessionSnapshot, GatewayEvent, GatewayEventType, InMemoryGameSessionFacts
from modules.runtime_manager.async_application import (
    AsyncBindingApplication, AsyncBindingService, AsyncGatewayProjectionService,
    AsyncRuntimeService, activation_intent_digest,
)
from modules.runtime_manager.domain import (
    AuthorityIssuanceIntent,
    BindingState,
    Conflict,
    RuntimeBinding,
    RuntimeProductRecord,
    RuntimeProductSpec,
    RuntimeState,
    ResourceExhausted,
)
from modules.runtime_manager.admission import RuntimeAdmissionPolicy
from modules.runtime_manager.persistence import (
    AsyncpgBindingRepository,
    AsyncpgGatewayProjectionRepository,
    AsyncpgRuntimeProductRepository,
)
from modules.runtime_manager.coordinator import ActivityKind, EffectiveActivityReporter
from modules.runtime_manager.product_repository import OperationResult


ROOT = Path(__file__).parents[3]
UNBOUNDED_TEST_ADMISSION = RuntimeAdmissionPolicy(
    max_active_global=10_000, max_active_per_user=1_000
)
MIGRATIONS = (
    ROOT / "supabase" / "migrations" / "20260402000001_init.sql",
    ROOT / "supabase" / "migrations" / "20260713000000_runtime_manager_control_plane.sql",
    ROOT / "supabase" / "migrations" / "20260713010000_runtime_input_operation_kind.sql",
    ROOT / "supabase" / "migrations" / "20260714000000_hosted_mcp_authority.sql",
    ROOT / "supabase" / "migrations" / "20260715000000_runtime_adapter_configs.sql",
    ROOT / "supabase" / "migrations" / "20260718000000_hosted_control_selections.sql",
)
INITDB = shutil.which("initdb") or "/opt/homebrew/bin/initdb"
PG_CTL = shutil.which("pg_ctl") or "/opt/homebrew/bin/pg_ctl"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def postgres_dsn(tmp_path_factory):
    if not Path(INITDB).exists() or not Path(PG_CTL).exists():
        if _REQUIRE_PRODUCT_EVIDENCE:
            pytest.fail(
                "STARCOPLAY_REQUIRE_PRODUCT_EVIDENCE=1 requires local PostgreSQL binaries "
                f"(initdb={INITDB}, pg_ctl={PG_CTL})"
            )
        pytest.skip("local PostgreSQL binaries unavailable")
    root = tmp_path_factory.mktemp("runtime-manager-postgres")
    data = root / "data"
    port = _free_port()
    subprocess.run(
        [INITDB, "-D", str(data), "-A", "trust", "-U", "postgres", "--no-locale"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        [PG_CTL, "-D", str(data), "-o", f"-p {port} -h 127.0.0.1", "-w", "start"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"

    async def migrate():
        conn = await asyncpg.connect(dsn)
        try:
            for migration in MIGRATIONS:
                await conn.execute(migration.read_text())
        finally:
            await conn.close()

    asyncio.run(migrate())
    yield dsn
    subprocess.run(
        [PG_CTL, "-D", str(data), "-w", "stop"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


async def fresh_pool(postgres_dsn):
    pool = await asyncpg.create_pool(postgres_dsn, min_size=1, max_size=6)
    async with pool.acquire() as conn:
        await conn.execute(
            """TRUNCATE public.runtime_config_roll_leases, public.runtime_control_claim_nonces,
               public.gateway_event_dedupe, public.gateway_session_projection,
               public.runtime_authority_outbox, public.runtime_operations, public.runtime_bindings,
               public.runtime_instances CASCADE"""
        )
    return pool


def spec(**changes) -> RuntimeProductSpec:
    base = RuntimeProductSpec(
        runtime_id="runtime-1", generation=1, activation_key="activation-1",
        user_id="user-1", agentee_id="agentee-1", adapter_kind="fake",
        adapter_config_ref="adapter/default", persona_artifact_ref="persona/rev-1",
    )
    return replace(base, **changes)


async def _atomic_ensure_scenario(postgres_dsn) -> None:
    pg_pool = await fresh_pool(postgres_dsn)
    service_a = AsyncRuntimeService(AsyncpgRuntimeProductRepository(pg_pool))
    service_b = AsyncRuntimeService(AsyncpgRuntimeProductRepository(pg_pool))

    first, duplicate = await asyncio.gather(service_a.ensure_runtime(spec()), service_b.ensure_runtime(spec()))
    assert first == duplicate
    assert first.generation == 1
    assert await service_a.ensure_runtime(spec(runtime_id="caller-id", generation=99)) == first
    with pytest.raises(Conflict, match="different stable startup intent"):
        await service_b.ensure_runtime(spec(adapter_config_ref="adapter/other"))

    fresh = replace(spec(), runtime_id="runtime-2", activation_key="activation-2", generation=2)
    with pytest.raises(Conflict, match="first activation generation"):
        await service_a.ensure_runtime(fresh)
    await pg_pool.close()


async def _runtime_admission_concurrency_scenario(postgres_dsn) -> None:
    pool = await fresh_pool(postgres_dsn)
    global_policy = RuntimeAdmissionPolicy(max_active_global=1, max_active_per_user=1)
    repo_a = AsyncpgRuntimeProductRepository(pool, global_policy)
    repo_b = AsyncpgRuntimeProductRepository(pool, global_policy)
    results = await asyncio.gather(
        AsyncRuntimeService(repo_a).ensure_runtime(spec(
            runtime_id="global-a", activation_key="global-a", user_id="owner-a",
        )),
        AsyncRuntimeService(repo_b).ensure_runtime(spec(
            runtime_id="global-b", activation_key="global-b", user_id="owner-b",
        )),
        return_exceptions=True,
    )
    assert sum(isinstance(item, RuntimeProductRecord) for item in results) == 1
    assert sum(isinstance(item, ResourceExhausted) for item in results) == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM public.runtime_instances WHERE actual_state NOT IN ('stopped','failed')"
    ) == 1
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_bindings") == 0
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_authority_outbox") == 0
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_operations") == 0

    await pool.execute(
        "UPDATE public.runtime_instances SET actual_state='stopped', desired_state='stopped'"
    )
    per_user_policy = RuntimeAdmissionPolicy(max_active_global=20, max_active_per_user=1)
    repo_a = AsyncpgRuntimeProductRepository(pool, per_user_policy)
    repo_b = AsyncpgRuntimeProductRepository(pool, per_user_policy)
    results = await asyncio.gather(
        AsyncRuntimeService(repo_a).ensure_runtime(spec(
            runtime_id="user-a", activation_key="user-a", user_id="same-owner",
        )),
        AsyncRuntimeService(repo_b).ensure_runtime(spec(
            runtime_id="user-b", activation_key="user-b", user_id="same-owner",
        )),
        return_exceptions=True,
    )
    assert sum(isinstance(item, RuntimeProductRecord) for item in results) == 1
    assert sum(isinstance(item, ResourceExhausted) for item in results) == 1
    assert await pool.fetchval(
        """SELECT count(*) FROM public.runtime_instances
           WHERE user_id='same-owner' AND actual_state NOT IN ('stopped','failed')"""
    ) == 1

    winner = next(item for item in results if isinstance(item, RuntimeProductRecord))
    winner_spec = winner.spec
    replay = await AsyncRuntimeService(repo_a).ensure_runtime(replace(
        winner_spec, runtime_id="ignored-replay-id", generation=99
    ))
    assert replay == winner
    with pytest.raises(Conflict, match="different stable startup intent"):
        await AsyncRuntimeService(repo_b).ensure_runtime(replace(
            winner_spec, adapter_config_ref="adapter/conflict"
        ))
    assert await pool.fetchval(
        "SELECT count(*) FROM public.runtime_instances WHERE user_id='same-owner'"
    ) == 1
    await pool.close()


async def _runtime_admission_inactive_predicate_scenario(postgres_dsn) -> None:
    pool = await fresh_pool(postgres_dsn)
    permissive = RuntimeAdmissionPolicy(max_active_global=20, max_active_per_user=20)
    service = AsyncRuntimeService(AsyncpgRuntimeProductRepository(pool, permissive))
    await service.ensure_runtime(spec(
        runtime_id="stopped-runtime", activation_key="stopped-activation", user_id="stopped-owner"
    ))
    await service.ensure_runtime(spec(
        runtime_id="failed-runtime", activation_key="failed-activation", user_id="failed-owner"
    ))
    await pool.execute(
        """UPDATE public.runtime_instances SET actual_state='stopped',desired_state='stopped'
           WHERE runtime_id='stopped-runtime'"""
    )
    await pool.execute(
        "UPDATE public.runtime_instances SET actual_state='failed' WHERE runtime_id='failed-runtime'"
    )
    limited = AsyncRuntimeService(AsyncpgRuntimeProductRepository(
        pool, RuntimeAdmissionPolicy(max_active_global=1, max_active_per_user=1)
    ))
    created = await limited.ensure_runtime(spec(
        runtime_id="new-runtime", activation_key="new-activation", user_id="new-owner"
    ))
    assert created.runtime_id == "new-runtime"
    assert await pool.fetchval(
        "SELECT count(*) FROM public.runtime_instances WHERE actual_state NOT IN ('stopped','failed')"
    ) == 1
    await pool.close()


async def _normal_and_fenced_admission_race_scenario(postgres_dsn) -> None:
    pool = await fresh_pool(postgres_dsn)

    async def race(policy, ordinary_spec, fenced_spec, token):
        ordinary_repo = AsyncpgRuntimeProductRepository(pool, policy)
        fenced_repo = AsyncpgRuntimeProductRepository(pool, policy)
        epoch = await fenced_repo.acquire_roll_lease(
            fenced_spec.runtime_id, f"operation-{token}", f"holder-{token}", token * 64,
            0, fenced_spec.generation, fenced_spec.adapter_config_ref, token * 64,
        )
        assert epoch == 1
        return await asyncio.gather(
            AsyncRuntimeService(ordinary_repo).ensure_runtime(ordinary_spec),
            fenced_repo.fenced_ensure(
                RuntimeProductRecord(fenced_spec), activation_intent_digest(fenced_spec),
                f"holder-{token}", epoch, token * 64,
            ),
            return_exceptions=True,
        )

    global_results = await race(
        RuntimeAdmissionPolicy(max_active_global=1, max_active_per_user=1),
        spec(runtime_id="ordinary-global", activation_key="ordinary-global", user_id="owner-a"),
        spec(runtime_id="fenced-global", activation_key="fenced-global", user_id="owner-b"),
        "a",
    )
    assert sum(isinstance(item, RuntimeProductRecord) for item in global_results) == 1
    assert sum(isinstance(item, ResourceExhausted) for item in global_results) == 1
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_instances") == 1
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_bindings") == 0
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_authority_outbox") == 0
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_operations") == 0

    await pool.execute(
        "UPDATE public.runtime_instances SET actual_state='stopped', desired_state='stopped'"
    )
    per_user_results = await race(
        RuntimeAdmissionPolicy(max_active_global=20, max_active_per_user=1),
        spec(runtime_id="ordinary-user", activation_key="ordinary-user", user_id="same-owner"),
        spec(runtime_id="fenced-user", activation_key="fenced-user", user_id="same-owner"),
        "b",
    )
    assert sum(isinstance(item, RuntimeProductRecord) for item in per_user_results) == 1
    assert sum(isinstance(item, ResourceExhausted) for item in per_user_results) == 1
    assert await pool.fetchval(
        """SELECT count(*) FROM public.runtime_instances
           WHERE user_id='same-owner' AND actual_state NOT IN ('stopped','failed')"""
    ) == 1
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_bindings") == 0
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_authority_outbox") == 0
    assert await pool.fetchval("SELECT count(*) FROM public.runtime_operations") == 0
    await pool.close()


async def _concurrent_ensure_binding_scenario(postgres_dsn) -> None:
    pool = await fresh_pool(postgres_dsn)
    runtimes_a = AsyncpgRuntimeProductRepository(pool)
    runtimes_b = AsyncpgRuntimeProductRepository(pool)
    await AsyncRuntimeService(runtimes_a).ensure_runtime(spec())
    service_a = AsyncBindingService(runtimes_a, AsyncpgBindingRepository(pool))
    service_b = AsyncBindingService(runtimes_b, AsyncpgBindingRepository(pool))

    first, second = await asyncio.gather(
        service_a.ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "binding-operation-a"
        ),
        service_b.ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "binding-operation-b"
        ),
    )
    assert first.binding_id == second.binding_id
    assert first.status is second.status is BindingState.PENDING
    assert await service_a.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "binding-operation-a"
    ) == first
    assert await service_b.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "binding-operation-b"
    ) == second
    with pytest.raises(Conflict, match="operation_id reused with different payload"):
        await service_a.ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "binding-operation-a",
            game_session_id="forged-game-session",
        )
    assert await pool.fetchval(
        "SELECT count(*) FROM public.runtime_bindings WHERE runtime_id=$1 AND generation=$2",
        "runtime-1", 1,
    ) == 1
    assert await pool.fetchval(
        """SELECT count(*) FROM public.runtime_operations
           WHERE owner_ref=$1 AND operation_id=ANY($2::text[])""",
        "runtime-binding:runtime-1:1",
        ["binding-operation-a", "binding-operation-b"],
    ) == 2
    await pool.close()


async def _gateway_runtime_state_projection_scenario(postgres_dsn) -> None:
    from modules.runtime_manager.coordinator import HostedGatewayEventConsumer

    async def ready_runtime(pool):
        runtimes = AsyncpgRuntimeProductRepository(pool)
        record = await AsyncRuntimeService(runtimes).ensure_runtime(spec())
        return runtimes, await runtimes.compare_and_swap(
            replace(record, actual_state=RuntimeState.READY_UNBOUND, ready=True),
            record.status_version,
        )

    def event(event_type, version, message_id):
        closed = event_type is GatewayEventType.CLOSED
        suspended = event_type is GatewayEventType.SUSPENDED
        return GatewayEvent(
            event_type,
            GameSessionSnapshot(
                "game-runtime-projection", "nonce-runtime-projection", "user-1",
                None if suspended or closed else "connection-runtime-projection",
                version, "digest-runtime-projection", not suspended and not closed,
                not closed, "resume-runtime-projection",
            ),
            message_id=message_id,
        )

    pool = await fresh_pool(postgres_dsn)
    runtimes, runtime = await ready_runtime(pool)
    bindings = AsyncpgBindingRepository(pool)
    await AsyncBindingService(runtimes, bindings).ensure_binding(
        runtime.runtime_id, runtime.generation, runtime.spec.user_id,
        runtime.spec.agentee_id, "projection-binding",
    )
    projection = AsyncGatewayProjectionService(
        AsyncpgGatewayProjectionRepository(pool), InMemoryGameSessionFacts(),
    )
    consumer = HostedGatewayEventConsumer(projection, bindings, runtimes)
    transitions = (
        (GatewayEventType.AVAILABLE, RuntimeState.RUNNING_BOUND),
        (GatewayEventType.SUSPENDED, RuntimeState.IDLE),
        (GatewayEventType.RESUMED, RuntimeState.RUNNING_BOUND),
        (GatewayEventType.CLOSED, RuntimeState.IDLE),
    )
    for version, (event_type, expected_state) in enumerate(transitions, start=1):
        current_event = event(event_type, version, f"projection-{version}")
        await consumer.consume(current_event)
        projected = await runtimes.get_generation(runtime.runtime_id, runtime.generation)
        assert projected.actual_state is expected_state
        status_version = projected.status_version
        binding_version = (await bindings.find_for_runtime(runtime.runtime_id, runtime.generation)).binding_version
        duplicate = await consumer.consume(current_event)
        assert duplicate.duplicate
        assert (await runtimes.get_generation(runtime.runtime_id, runtime.generation)).status_version == status_version
        assert (await bindings.find_for_runtime(runtime.runtime_id, runtime.generation)).binding_version == binding_version
    assert (await runtimes.get_generation(runtime.runtime_id, runtime.generation)).desired_state.value == "running"
    await pool.close()

    # Prove recovery when Binding/event persistence commits but Runtime CAS is
    # exhausted.  The same-message duplicate reads the terminal-aware current
    # Binding and repairs Runtime without another Binding transition.
    pool = await fresh_pool(postgres_dsn)
    durable_runtimes, runtime = await ready_runtime(pool)
    bindings = AsyncpgBindingRepository(pool)
    await AsyncBindingService(durable_runtimes, bindings).ensure_binding(
        runtime.runtime_id, runtime.generation, runtime.spec.user_id,
        runtime.spec.agentee_id, "projection-recovery-binding",
    )

    class ConflictingRuntimeRepository:
        def __init__(self, delegate): self.delegate = delegate; self.conflicts = 3
        async def get_generation(self, runtime_id, generation):
            return await self.delegate.get_generation(runtime_id, generation)
        async def compare_and_swap(self, record, expected):
            if self.conflicts:
                self.conflicts -= 1
                raise Conflict("forced runtime projection conflict")
            return await self.delegate.compare_and_swap(record, expected)

    conflicting = ConflictingRuntimeRepository(durable_runtimes)
    projection = AsyncGatewayProjectionService(
        AsyncpgGatewayProjectionRepository(pool), InMemoryGameSessionFacts(),
    )
    consumer = HostedGatewayEventConsumer(projection, bindings, conflicting)
    available = event(GatewayEventType.AVAILABLE, 1, "projection-recovery")
    with pytest.raises(Conflict, match="CAS exhausted"):
        await consumer.consume(available)
    committed_binding = await bindings.find_for_runtime(runtime.runtime_id, runtime.generation)
    assert committed_binding.status is BindingState.ACTIVE
    assert committed_binding.binding_version == 2
    assert (await durable_runtimes.get_generation(runtime.runtime_id, runtime.generation)).actual_state is RuntimeState.READY_UNBOUND
    repaired = await consumer.consume(available)
    assert repaired.duplicate
    assert (await bindings.find_for_runtime(runtime.runtime_id, runtime.generation)).binding_version == 2
    assert (await durable_runtimes.get_generation(runtime.runtime_id, runtime.generation)).actual_state is RuntimeState.RUNNING_BOUND
    await pool.close()


async def _gateway_claim_scenario(postgres_dsn) -> None:
    pg_pool = await fresh_pool(postgres_dsn)
    repository = AsyncpgGatewayProjectionRepository(pg_pool)
    facts = InMemoryGameSessionFacts()
    service = AsyncGatewayProjectionService(repository, facts)
    snapshot = GameSessionSnapshot(
        "game-1", "nonce-1", "user-1", "connection-1", 1, "digest-1", True, True, "resume-1"
    )
    event = GatewayEvent(GatewayEventType.AVAILABLE, snapshot, message_id="message-1")

    first, duplicate = await asyncio.gather(service.consume(event), service.consume(event))
    if first.duplicate:
        first, duplicate = duplicate, first
    assert first.snapshot == snapshot
    assert duplicate.duplicate
    assert await repository.get("game-1") == snapshot

    collision = GatewayEvent(
        GatewayEventType.CAPABILITIES_CHANGED,
        replace(snapshot, capability_digest="attacker"),
        message_id="message-1",
    )
    with pytest.raises(Conflict, match="different payload"):
        await service.consume(collision)
    assert await repository.get("game-1") == snapshot
    await pg_pool.close()


async def _gateway_gap_scenario(postgres_dsn) -> None:
    pg_pool = await fresh_pool(postgres_dsn)
    repository = AsyncpgGatewayProjectionRepository(pg_pool)
    facts = InMemoryGameSessionFacts()
    service = AsyncGatewayProjectionService(repository, facts)
    gap_snapshot = GameSessionSnapshot(
        "game-1", "nonce-1", "user-1", "connection-1", 3, "digest-3", True, True, "resume-3"
    )
    gap = GatewayEvent(GatewayEventType.RESUMED, gap_snapshot, message_id="message-gap")
    with pytest.raises(ValueError, match="snapshot_unavailable"):
        await service.consume(gap)
    assert not await repository.has_message("message-gap")

    await facts.publish(gap)
    result = await service.consume(gap)
    assert result.converged_from_snapshot
    assert result.snapshot.resume_identity == "resume-3"
    assert await repository.has_message("message-gap")
    await pg_pool.close()


async def _outbox_order_scenario(postgres_dsn) -> None:
    pg_pool = await fresh_pool(postgres_dsn)
    runtime_service = AsyncRuntimeService(AsyncpgRuntimeProductRepository(pg_pool))
    await runtime_service.ensure_runtime(spec())
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO public.runtime_bindings(
                 binding_id,runtime_id,generation,user_id,agentee_id,status,contract_version)
               VALUES('binding-1','runtime-1',1,'user-1','agentee-1','pending','starcoplay.binding.v1')"""
        )
        await conn.execute(
            """INSERT INTO public.runtime_authority_outbox(
                 intent_id,intent_kind,binding_id,binding_version,lease_epoch,payload,created_at)
               VALUES
                 ('issue-2','issue','binding-1',2,1,'{}'::jsonb,now()+interval '1 millisecond'),
                 ('revoke-2','revoke','binding-1',2,1,'{}'::jsonb,now())"""
        )
    worker_a = AsyncpgBindingRepository(pg_pool)
    worker_b = AsyncpgBindingRepository(pg_pool)
    claimed_a, claimed_b = await asyncio.gather(
        worker_a.claim_pending_intents(), worker_b.claim_pending_intents()
    )
    claims = claimed_a + claimed_b
    assert [row["intent_id"] for row in claims] == ["revoke-2"]
    assert await AsyncpgBindingRepository(pg_pool).claim_pending_intents() == []

    # Simulate worker death after claim.  Once its claim lease is stale, a new
    # process recovers the same revoke; the later issue remains blocked.
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.runtime_authority_outbox SET claimed_at=now()-interval '6 minutes' WHERE intent_id='revoke-2'"
        )
    recovered = await AsyncpgBindingRepository(pg_pool).claim_pending_intents()
    assert [row["intent_id"] for row in recovered] == ["revoke-2"]
    assert await AsyncpgBindingRepository(pg_pool).claim_pending_intents() == []

    await worker_a.acknowledge_intent("revoke-2")
    after_restart = await AsyncpgBindingRepository(pg_pool).claim_pending_intents()
    assert [row["intent_id"] for row in after_restart] == ["issue-2"]
    await pg_pool.close()


async def _binding_application_scenario(postgres_dsn) -> None:
    pg_pool = await fresh_pool(postgres_dsn)
    await AsyncRuntimeService(AsyncpgRuntimeProductRepository(pg_pool)).ensure_runtime(spec())
    application = AsyncBindingApplication(AsyncpgBindingRepository(pg_pool))
    pending = RuntimeBinding(
        "binding-1", "runtime-1", 1, "user-1", "agentee-1", status=BindingState.PENDING,
        game_session_id="game-1",
    )
    inserted = await application.insert(
        pending, OperationResult("runtime-binding:runtime-1:1", "ensure-1", "a" * 64, pending)
    )
    active = replace(
        inserted, binding_version=2, lease_epoch=1, status=BindingState.ACTIVE,
        game_instance_nonce="nonce-1", capability_digest="digest-1", resume_identity="resume-1",
    )
    issuance = AuthorityIssuanceIntent("issue-1", active, ("game:read",), ("say",), "jti-1")
    transitioned = await application.transition(
        active, 1, OperationResult("binding-1", "activate-1", "b" * 64, active), issuance=issuance
    )
    restarted = AsyncBindingApplication(AsyncpgBindingRepository(pg_pool))
    assert await restarted.get("binding-1") == transitioned
    cached = await restarted.get_operation("binding-1", "activate-1")
    assert cached is not None and cached.result == transitioned
    assert [row["intent_id"] for row in await AsyncpgBindingRepository(pg_pool).claim_pending_intents()] == ["issue-1"]
    await pg_pool.close()


async def _gateway_owned_session_claim_scenario(postgres_dsn) -> None:
    pg_pool = await fresh_pool(postgres_dsn)
    repository = AsyncpgBindingRepository(pg_pool)
    await AsyncRuntimeService(AsyncpgRuntimeProductRepository(pg_pool)).ensure_runtime(
        spec(runtime_id="runtime-pending", activation_key="activation-pending",
             user_id="owner-a", agentee_id="agentee-a")
    )
    pending = RuntimeBinding(
        "binding-pending", "runtime-pending", 1, "owner-a", "agentee-a",
    )
    await repository.insert_with_operation(
        pending, OperationResult("binding:binding-pending", "create-pending", "a" * 64, None)
    )
    claimed = await repository.find_for_gateway_event("gateway-owned-session", "owner-a")
    assert claimed is not None and claimed.binding_id == pending.binding_id
    assert await repository.find_for_gateway_event("gateway-owned-session", "owner-b") is None
    await pg_pool.close()


async def _input_operation_scenario(postgres_dsn) -> None:
    pg_pool = await fresh_pool(postgres_dsn)
    first = AsyncpgRuntimeProductRepository(pg_pool)
    claimed = await first.claim_input_operation("runtime-input:r1:1", "op-1", "a" * 64)
    assert claimed == {"status": "claimed"}
    restarted = AsyncpgRuntimeProductRepository(pg_pool)
    assert await restarted.claim_input_operation("runtime-input:r1:1", "op-1", "a" * 64) == {
        "status": "pending"
    }
    await first.complete_input_operation(
        "runtime-input:r1:1", "op-1",
        {"ok": True, "request_id": "op-1", "runtime_id": "r1", "generation": 1,
         "turn": 1, "content": "ok", "detail": {}},
    )
    completed = await restarted.claim_input_operation("runtime-input:r1:1", "op-1", "a" * 64)
    assert completed["status"] == "completed" and completed["result"]["content"] == "ok"
    with pytest.raises(Conflict):
        await restarted.claim_input_operation("runtime-input:r1:1", "op-1", "b" * 64)
    await pg_pool.close()


def test_real_postgres_atomic_ensure_is_retry_safe_and_rejects_conflicts(postgres_dsn) -> None:
    asyncio.run(_atomic_ensure_scenario(postgres_dsn))


def test_real_postgres_runtime_admission_is_atomic_and_replay_precedes_quota(postgres_dsn) -> None:
    asyncio.run(_runtime_admission_concurrency_scenario(postgres_dsn))


def test_real_postgres_runtime_admission_excludes_stopped_and_failed(postgres_dsn) -> None:
    asyncio.run(_runtime_admission_inactive_predicate_scenario(postgres_dsn))


def test_real_postgres_normal_and_fenced_inserts_share_atomic_admission(postgres_dsn) -> None:
    asyncio.run(_normal_and_fenced_admission_race_scenario(postgres_dsn))


def test_real_postgres_concurrent_ensure_binding_converges_and_replays(postgres_dsn) -> None:
    asyncio.run(_concurrent_ensure_binding_scenario(postgres_dsn))


def test_real_postgres_gateway_binding_projects_runtime_state_and_recovers_cas(postgres_dsn) -> None:
    asyncio.run(_gateway_runtime_state_projection_scenario(postgres_dsn))


def test_real_postgres_gateway_claim_is_atomic_collision_safe_and_resume_lossless(postgres_dsn) -> None:
    asyncio.run(_gateway_claim_scenario(postgres_dsn))


def test_real_postgres_gap_failure_rolls_back_claim_then_snapshot_retry_converges(postgres_dsn) -> None:
    asyncio.run(_gateway_gap_scenario(postgres_dsn))


def test_real_postgres_outbox_enforces_revoke_before_issue_across_workers(postgres_dsn) -> None:
    asyncio.run(_outbox_order_scenario(postgres_dsn))


def test_real_postgres_async_binding_application_is_composable_and_restart_persistent(postgres_dsn) -> None:
    asyncio.run(_binding_application_scenario(postgres_dsn))


def test_real_postgres_gateway_owned_session_claims_only_matching_pending_owner(postgres_dsn) -> None:
    asyncio.run(_gateway_owned_session_claim_scenario(postgres_dsn))


def test_real_postgres_input_operation_is_restart_persistent_and_collision_safe(postgres_dsn) -> None:
    asyncio.run(_input_operation_scenario(postgres_dsn))


async def _legacy_m2_hosted_transport_scenario(postgres_dsn, workspace) -> None:
    import base64
    import json
    import os
    import sys
    import time
    from aiohttp import web
    import websockets
    from agent.generated.protocol import capability_pb2, transport_pb2
    from modules.gateway.hosted_session import HostedSessionCoordinator, HostedSessionRegistry, compute_capability_digest
    from modules.gateway.server import Gateway, GatewayConfig
    from modules.gateway.runtime_contracts import GatewayRouter
    from modules.mcp.context import HostedClaimsAuthority
    from modules.mcp.context.hosted_credential import HostedCredentialSigner, HostedCredentialVerifier, RevocationStore
    from modules.mcp.contracts.models import CommandState, SideEffect
    from modules.mcp.manifest import build_manifest, build_tool_catalog
    from modules.mcp.mapping_authority import load_mapping_vectors
    from modules.mcp.core.service import McpCore
    from modules.mcp.transports.hosted_server import HostedMcpWebSocketEndpoint
    from modules.runtime_manager.async_application import AuthorityOutboxWorker, AsyncGatewayProjectionService
    from modules.runtime_manager.coordinator import DurableGatewayEventPublisher, HostedGatewayEventConsumer
    from modules.runtime_manager.hosted_mcp_authority import AsyncpgHostedMcpSecurityRepository, DurableHostedCredentialVerifier, HostedCredentialFileAuthority
    from modules.runtime_manager.router_validation import RuntimeBindingProjectionValidator

    pool = await fresh_pool(postgres_dsn)
    await pool.execute("CREATE TABLE IF NOT EXISTS public.profiles(user_id text PRIMARY KEY,user_type text)")
    await pool.execute("CREATE TABLE IF NOT EXISTS public.platform_tokens(token text PRIMARY KEY,user_id text)")
    await pool.execute("""CREATE TABLE IF NOT EXISTS public.system_configs(
        domain text NOT NULL, config_key text NOT NULL, environment text NOT NULL,
        status integer NOT NULL, config_value jsonb NOT NULL,
        PRIMARY KEY(domain,config_key,environment))""")
    await pool.execute("""INSERT INTO public.system_configs VALUES(
        'platform_access','onboarding_policy','prod',1,
        '{"key_access_user_types":["user","member","beta_user"]}'::jsonb)
        ON CONFLICT(domain,config_key,environment) DO UPDATE SET
        status=excluded.status,config_value=excluded.config_value""")
    await pool.execute("""INSERT INTO public.users(user_id,platform_token,invite_code_used)
        VALUES('owner-a','platform-owner-a','test') ON CONFLICT DO NOTHING""")
    await pool.execute("INSERT INTO public.profiles VALUES('owner-a','user') ON CONFLICT DO NOTHING")
    await pool.execute("INSERT INTO public.platform_tokens VALUES('token-a','owner-a') ON CONFLICT DO NOTHING")
    await pool.execute("INSERT INTO public.profiles VALUES('owner-b','user') ON CONFLICT DO NOTHING")
    await pool.execute("INSERT INTO public.platform_tokens VALUES('token-b','owner-b') ON CONFLICT DO NOTHING")
    runtimes = AsyncpgRuntimeProductRepository(pool)
    bindings = AsyncpgBindingRepository(pool)
    canonical_catalog = build_tool_catalog(build_manifest(ROOT.parent))
    canonical_names = tuple(sorted(canonical_catalog._tools))
    await AsyncRuntimeService(runtimes).ensure_runtime(spec(
        runtime_id="runtime-e2e", activation_key="activation-e2e", user_id="owner-a",
        agentee_id="agentee-e2e", allowed_capabilities=canonical_names,
    ))
    pending = RuntimeBinding("binding-e2e", "runtime-e2e", 1, "owner-a", "agentee-e2e")
    await bindings.insert_with_operation(
        pending, OperationResult("binding:binding-e2e", "create-e2e", "b" * 64, None)
    )
    await AsyncRuntimeService(runtimes).ensure_runtime(spec(
        runtime_id="runtime-e2e-b", activation_key="activation-e2e-b", user_id="owner-b",
        agentee_id="agentee-e2e-b", allowed_capabilities=canonical_names,
    ))
    pending_b = RuntimeBinding("binding-e2e-b", "runtime-e2e-b", 1, "owner-b", "agentee-e2e-b")
    await bindings.insert_with_operation(
        pending_b, OperationResult("binding:binding-e2e-b", "create-e2e-b", "c" * 64, None)
    )

    registry = HostedSessionRegistry()
    publisher = DurableGatewayEventPublisher()
    coordinator = HostedSessionCoordinator(registry, publisher)
    projection = AsyncGatewayProjectionService(AsyncpgGatewayProjectionRepository(pool), coordinator)
    consumer = HostedGatewayEventConsumer(projection, bindings, runtimes)
    publisher.bind(consumer)
    security = AsyncpgHostedMcpSecurityRepository(pool)
    keys = await security.load_or_create_keys()
    authority = HostedClaimsAuthority(
        DurableHostedCredentialVerifier(HostedCredentialVerifier(keys, RevocationStore()), security), bindings
    )
    router = GatewayRouter(registry, coordinator, RuntimeBindingProjectionValidator(bindings))
    catalog = canonical_catalog
    endpoint = HostedMcpWebSocketEndpoint(McpCore(authority, router, catalog), authority)
    app = web.Application(); app.router.add_get("/mcp/hosted/ws", endpoint.handle)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    http_port = site._server.sockets[0].getsockname()[1]
    gateway = Gateway(
        GatewayConfig(dsn=postgres_dsn, hosted_route_enabled=True),
        worker_handle=lambda *_: asyncio.sleep(0), game_session_event_hook=coordinator,
        hosted_session_registry=registry,
    )
    gateway_server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    gateway_port = gateway_server.sockets[0].getsockname()[1]
    proxy = None
    proxy_b = None
    mod_b = None
    first_credential = None
    tenant_b_credential = None
    try:
        mod = await websockets.connect(f"ws://127.0.0.1:{gateway_port}")
        entries = tuple(("canonical", name, "canonical.v1") for name in canonical_names)
        digest = compute_capability_digest(entries)
        await mod.send(json.dumps({
            "type": "hello", "platform_token": "token-a",
            "route_contract_version": "starcoplay.hosted-route.v1",
            "game_instance_nonce": "nonce-e2e", "capability_digest": digest,
            "capability_entries": [{"source_surface": a, "capability_id": b,
                                    "capability_contract_version": c} for a,b,c in entries],
            "player_id": "1", "agent_control_enabled": True,
            "agent_control_target_player_id": "1", "agent_control_revision": 1,
        }))
        ack = json.loads(await mod.recv())
        assert ack["type"] == "hello_ok" and ack["resumed"] is False
        assert ack["game_session_id"].startswith("gs_") and ack["resume_identity"].startswith("gri_")
        active = await bindings.get("binding-e2e")
        assert active.status is BindingState.ACTIVE and active.game_session_id == ack["game_session_id"]
        file_authority = HostedCredentialFileAuthority(
            HostedCredentialSigner(keys), security, bindings, workspace, ttl_seconds=300,
        )
        assert await AuthorityOutboxWorker(bindings, file_authority).run_once() == 1
        credential = workspace / "runtime-e2e" / "generation-1" / "mcp-credential"
        assert credential.exists() and credential.stat().st_mode & 0o777 == 0o600
        first_credential = credential.read_text()
        # Every forged authority dimension is rejected through the real WS
        # transport and must emit no command frame to the Mod socket.
        from dataclasses import replace as dc_replace
        from datetime import datetime, timedelta, timezone
        from aiohttp import ClientSession
        from modules.runtime_manager.authority import AuthorityMaterial
        valid_material = AuthorityMaterial(
            "ephemeral", "starcoplay-runtime-manager", "starcoplay-mcp", active.runtime_id,
            "negative-base", active.runtime_id, active.generation, active.binding_id,
            active.binding_version, active.lease_epoch, active.game_session_id,
            active.game_instance_nonce, active.capability_digest, active.user_id, active.agentee_id,
            ("game:read", "game:write"), canonical_names,
            datetime.now(timezone.utc) + timedelta(minutes=2),
        )
        mutations = (
            {"generation": 99}, {"binding_version": active.binding_version + 1},
            {"lease_epoch": active.lease_epoch + 1}, {"game_instance_nonce": "wrong"},
            {"capability_digest": "sha256:" + "0" * 64}, {"user_id": "owner-b"},
        )
        async with ClientSession() as client:
            for index, changes in enumerate(mutations):
                forged = dc_replace(valid_material, jti=f"negative-{index}", **changes)
                denied_ws = await client.ws_connect(
                    f"ws://127.0.0.1:{http_port}/mcp/hosted/ws",
                    headers={"Authorization": f"Bearer {HostedCredentialSigner(keys).sign(forged)}"},
                )
                await denied_ws.send_json({"jsonrpc":"2.0","id":20+index,"method":"initialize","params":{}})
                assert "error" in await denied_ws.receive_json()
                await denied_ws.close()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(mod.recv(), .03)
        env = dict(os.environ)
        parent_pythonpath = env.get("PYTHONPATH", "")
        pythonpath_entries = [str(ROOT.parent), str(ROOT / "backend")]
        pythonpath_entries.extend(
            entry for entry in parent_pythonpath.split(os.pathsep)
            if entry and entry not in pythonpath_entries
        )
        env.update({
            "PYTHONPATH": os.pathsep.join(pythonpath_entries),
            "STARCOPLAY_HOSTED_MCP_ENDPOINT": f"ws://127.0.0.1:{http_port}/mcp/hosted/ws",
            "STARCOPLAY_MCP_CREDENTIAL_PATH": str(credential),
        })
        assert env["PYTHONPATH"].split(os.pathsep)[:2] == [str(ROOT.parent), str(ROOT / "backend")]
        assert all(entry in env["PYTHONPATH"].split(os.pathsep)
                   for entry in parent_pythonpath.split(os.pathsep) if entry)
        proxy = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "modules.mcp.transports.stdio_proxy",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env,
        )
        async def rpc(frame):
            proxy.stdin.write((json.dumps(frame) + "\n").encode()); await proxy.stdin.drain()
            while True:
                line = await asyncio.wait_for(proxy.stdout.readline(), 10)
                if not line:
                    raise AssertionError((await proxy.stderr.read()).decode())
                response = json.loads(line.decode())
                if response.get("id") == frame["id"]: return response
        async def wait_tools(call, start_id, expected, attempts=100):
            last = None
            for offset in range(attempts):
                last = await call({"jsonrpc":"2.0","id":start_id+offset,
                                   "method":"tools/list","params":{}})
                if [tool["name"] for tool in last.get("result", {}).get("tools", [])] == expected:
                    return last
                await asyncio.sleep(.01)
            raise AssertionError(last)
        assert "result" in await rpc({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})
        listed = await wait_tools(rpc, 2000, list(canonical_names))
        assert [tool["name"] for tool in listed["result"]["tools"]] == list(canonical_names)
        vectors = load_mapping_vectors()
        assert set(vectors) == set(canonical_names)
        # Test-local structural assertions: verify every tool reaches the Mod
        # peer with correct wire framing and returns a successful MCP result.
        # The production mapper (canonical_surface.py) is NOT used for
        # per-field payload assertions — only for determining the wire format
        # and capability name the test needs to simulate a valid response.
        dispatched_command_ids: dict[str, str] = {}
        for offset, name in enumerate(canonical_names, start=10_000):
            command_id = f"hosted-full-{offset}"
            dispatched_command_ids[name] = command_id
            call_task = asyncio.create_task(rpc({
                "jsonrpc":"2.0", "id":offset, "method":"tools/call",
                "params":{"name":name, "arguments":vectors[name]["input"],
                          "_meta":{"commandId":command_id,"toolCallId":f"tc-{offset}"}},
            }))
            wire = json.loads(await asyncio.wait_for(mod.recv(), 3))
            assert wire["hosted_fence"]["game_instance_nonce"] == "nonce-e2e"
            assert isinstance(wire.get("hosted_fence"), dict)
            wire_format = "protocol-2.4.5" if wire.get("type") == "protocol_envelope" else "v2-json"
            if wire_format == "protocol-2.4.5":
                envelope = transport_pb2.Envelope()
                envelope.ParseFromString(base64.b64decode(wire["payload"]))
                assert envelope.WhichOneof("body") == "command_request"
                result_env = transport_pb2.Envelope(
                    protocol_version="2.4.5", message_id=f"result-{offset}",
                    request_id=envelope.request_id, session_id=ack["game_session_id"],
                    timestamp_ms=int(time.time()*1000),
                )
                result_env.command_result.command_id = command_id
                result_env.command_result.capability = envelope.command_request.capability
                result_env.command_result.success = True
                result_env.command_result.status = capability_pb2.COMMAND_STATUS_COMPLETED
                await mod.send(json.dumps({"type":"protocol_envelope","encoding":"base64",
                    "payload":base64.b64encode(result_env.SerializeToString()).decode()}))
            else:
                assert wire.get("type") == "command"
                await mod.send(json.dumps({"type":"command_result", "action_id":command_id,
                    "status":"COMPLETED", "success":True, "data":{}}))
            called = await call_task
            assert called["result"]["isError"] is False, (name, called)
            content = json.loads(called["result"]["content"][0]["text"])
            assert content["state"] == "SUCCEEDED", (name, content)
            assert content["command_id"] == command_id

        # ---- Status query through real Gateway route ----
        # Dispatch a command that the Mod holds open; query its status
        # through the production Gateway route before returning a result.
        status_command_id = "hosted-status-query"
        status_call = asyncio.create_task(rpc({
            "jsonrpc":"2.0","id":9001,"method":"tools/call",
            "params":{"name":"say","arguments":{"message":"status-test"},
                      "_meta":{"commandId":status_command_id,"toolCallId":"tc-status"}},
        }))
        status_wire = json.loads(await asyncio.wait_for(mod.recv(), 3))
        assert status_wire["hosted_fence"]["game_instance_nonce"] == "nonce-e2e"
        status_env = transport_pb2.Envelope()
        status_env.ParseFromString(base64.b64decode(status_wire["payload"]))
        assert status_env.WhichOneof("body") == "command_request"
        # Resolve the Gateway route and query status — this triggers a real
        # command_status_request envelope on the Mod socket.
        route = await coordinator.registry.resolve_authenticated(ack["game_session_id"])
        assert route is not None
        status_task = asyncio.create_task(route.get_status(status_command_id))
        status_req_frame = json.loads(await asyncio.wait_for(mod.recv(), 3))
        status_req_env = transport_pb2.Envelope()
        status_req_env.ParseFromString(base64.b64decode(status_req_frame["payload"]))
        assert status_req_env.WhichOneof("body") == "command_status_request"
        assert status_req_env.command_status_request.command_id == status_command_id
        # Mod responds with RUNNING status, then a terminal COMPLETED result.
        status_resp_env = transport_pb2.Envelope(
            protocol_version="2.4.5",
            message_id=f"status-resp-msg",
            request_id=status_req_env.request_id,
            session_id=ack["game_session_id"],
            timestamp_ms=int(time.time()*1000),
        )
        status_resp_env.command_status_response.command_id = status_command_id
        status_resp_env.command_status_response.status = capability_pb2.COMMAND_STATUS_RUNNING
        await mod.send(json.dumps({"type":"protocol_envelope","encoding":"base64",
            "payload":base64.b64encode(status_resp_env.SerializeToString()).decode()}))
        status_state = await asyncio.wait_for(status_task, 3)
        assert status_state is CommandState.RUNNING, f"expected RUNNING, got {status_state}"
        # Now complete the original command normally.
        finish_env = transport_pb2.Envelope(
            protocol_version="2.4.5", message_id=f"status-finish",
            request_id=status_env.request_id, session_id=ack["game_session_id"],
            timestamp_ms=int(time.time()*1000),
        )
        finish_env.command_result.command_id = status_command_id
        finish_env.command_result.capability = "say"
        finish_env.command_result.success = True
        finish_env.command_result.status = capability_pb2.COMMAND_STATUS_COMPLETED
        await mod.send(json.dumps({"type":"protocol_envelope","encoding":"base64",
            "payload":base64.b64encode(finish_env.SerializeToString()).decode()}))
        status_result = await status_call
        assert status_result["result"]["isError"] is False

        # ---- Cancel + CANCELLED terminal state + response gate ----
        # sleep_for_night is a cancellable V2 renamed-adapter capability with
        # lifecycle_support = ("dispatch","result","status","cancel").
        cancel_cid = "hosted-cancel-e2e"
        cancel_call = asyncio.create_task(rpc({
            "jsonrpc":"2.0","id":9002,"method":"tools/call",
            "params":{"name":"sleep_for_night","arguments":{},
                      "_meta":{"commandId":cancel_cid,"toolCallId":"tc-cancel"}},
        }))
        cancel_wire = json.loads(await asyncio.wait_for(mod.recv(), 3))
        assert cancel_wire["type"] == "command"
        assert cancel_wire.get("action_id") == cancel_cid
        # Send notifications/cancelled through the proxy (raw write, no rpc id).
        cancel_note = {"jsonrpc":"2.0","method":"notifications/cancelled",
                       "params":{"requestId":9002,"reason":"e2e cancel test"}}
        proxy.stdin.write((json.dumps(cancel_note)+"\n").encode())
        await proxy.stdin.drain()
        # The Gateway sends command_cancel to the V2 Mod peer.
        cancel_control = json.loads(await asyncio.wait_for(mod.recv(), 3))
        assert cancel_control.get("cancel") is True
        assert cancel_control.get("action_id") == cancel_cid
        # Mod signals cancellation with CANCELLED terminal state.
        await mod.send(json.dumps({"type":"command_result","action_id":cancel_cid,
            "status":"CANCELLED","success":False,"data":{}}))
        # Response gate: cancellation accepted → the transport intentionally
        # suppresses the response (CancelledError with cancel_requested=True).
        # The frozen contract guarantees no response is written.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(cancel_call, 5)

        # ---- FAILED error mapping ----
        # Mod sends COMMAND_STATUS_FAILED; the MCP response must reflect
        # isError: true and state: FAILED.
        failed_cid = "hosted-failed-e2e"
        failed_call = asyncio.create_task(rpc({
            "jsonrpc":"2.0","id":9003,"method":"tools/call",
            "params":{"name":"say","arguments":{"message":"will-fail"},
                      "_meta":{"commandId":failed_cid,"toolCallId":"tc-failed"}},
        }))
        failed_wire = json.loads(await asyncio.wait_for(mod.recv(), 3))
        failed_env = transport_pb2.Envelope()
        failed_env.ParseFromString(base64.b64decode(failed_wire["payload"]))
        # Mod responds with FAILED.
        fail_result_env = transport_pb2.Envelope(
            protocol_version="2.4.5", message_id="fail-result",
            request_id=failed_env.request_id, session_id=ack["game_session_id"],
            timestamp_ms=int(time.time()*1000),
        )
        fail_result_env.command_result.command_id = failed_cid
        fail_result_env.command_result.capability = "say"
        fail_result_env.command_result.success = False
        fail_result_env.command_result.status = capability_pb2.COMMAND_STATUS_FAILED
        fail_result_env.command_result.error.code = 1
        fail_result_env.command_result.error.message = "simulated command failure"
        await mod.send(json.dumps({"type":"protocol_envelope","encoding":"base64",
            "payload":base64.b64encode(fail_result_env.SerializeToString()).decode()}))
        failed_result = await failed_call
        assert failed_result["result"]["isError"] is True
        failed_content = json.loads(failed_result["result"]["content"][0]["text"])
        assert failed_content["state"] == "FAILED", f"expected FAILED, got {failed_content}"

        # ---- Mod disconnect makes in-flight call converge to UNKNOWN ----
        # Start a command, then close the Mod socket before it completes.
        # The in-flight call must converge to UNKNOWN/EXPIRED_UNKNOWN without
        # blind replay on the new connection.
        unknown_cid = "hosted-unknown-e2e"
        unknown_call = asyncio.create_task(rpc({
            "jsonrpc":"2.0","id":9004,"method":"tools/call",
            "params":{"name":"say","arguments":{"message":"unknown-test"},
                      "_meta":{"commandId":unknown_cid,"toolCallId":"tc-unknown"}},
        }))
        unknown_wire = json.loads(await asyncio.wait_for(mod.recv(), 3))
        assert unknown_wire["hosted_fence"]["game_instance_nonce"] == "nonce-e2e"
        # Close the Mod socket before sending any result.
        await mod.close()
        # The in-flight call must resolve to an error/UNKNOWN state, not hang.
        try:
            unknown_result = await asyncio.wait_for(unknown_call, 10)
            unknown_content = json.loads(unknown_result["result"]["content"][0]["text"])
            assert unknown_content["state"] == "UNKNOWN", (
                f"disconnect in-flight call must converge to UNKNOWN, got {unknown_content['state']}")
            assert unknown_result["result"]["isError"] is True
        except asyncio.TimeoutError:
            pytest.fail("in-flight call did not converge after Mod disconnect")
        # Reconnect Mod for the remaining sections (resume tested below).
        mod = await websockets.connect(f"ws://127.0.0.1:{gateway_port}")
        await mod.send(json.dumps({
            "type": "hello", "platform_token": "token-a",
            "route_contract_version": "starcoplay.hosted-route.v1",
            "game_instance_nonce": "nonce-e2e", "capability_digest": digest,
            "capability_entries": [{"source_surface": a, "capability_id": b,
                                    "capability_contract_version": c} for a,b,c in entries],
            "player_id": "1", "agent_control_enabled": True,
            "agent_control_target_player_id": "1", "agent_control_revision": 1,
            "resume_token": ack["resume_token"],
        }))
        resumed_after_unknown = json.loads(await mod.recv())
        assert resumed_after_unknown["type"] == "hello_ok"
        # Verify no blind replay: the Mod must not receive the orphaned
        # command on the new connection.
        recovery_worker = AuthorityOutboxWorker(bindings, file_authority)
        # Durable ordering is revoke-before-issue: the first claim removes the
        # credential bound to the detached socket; only the next claim may
        # publish the resumed binding credential.
        assert await recovery_worker.run_once() == 1
        assert not credential.exists()
        assert await recovery_worker.run_once() == 1
        assert credential.exists()
        # Surface is restored after resume.
        after_unknown_surface = await wait_tools(rpc, 5000, list(canonical_names))
        assert [tool["name"] for tool in after_unknown_surface["result"]["tools"]] == list(canonical_names)
        # Dispatch a fresh command to prove the reconnected Mod is healthy.
        health_cid = "hosted-post-unknown-health"
        health_call = asyncio.create_task(rpc({
            "jsonrpc":"2.0","id":9005,"method":"tools/call",
            "params":{"name":"say","arguments":{"message":"post-disconnect"},
                      "_meta":{"commandId":health_cid,"toolCallId":"tc-health"}},
        }))
        health_wire = json.loads(await asyncio.wait_for(mod.recv(), 3))
        health_env = transport_pb2.Envelope()
        health_env.ParseFromString(base64.b64decode(health_wire["payload"]))
        health_reply = transport_pb2.Envelope(
            protocol_version="2.4.5", message_id="health-reply",
            request_id=health_env.request_id, session_id=ack["game_session_id"],
            timestamp_ms=int(time.time()*1000),
        )
        health_reply.command_result.command_id = health_cid
        health_reply.command_result.capability = "say"
        health_reply.command_result.success = True
        health_reply.command_result.status = capability_pb2.COMMAND_STATUS_COMPLETED
        await mod.send(json.dumps({"type":"protocol_envelope","encoding":"base64",
            "payload":base64.b64encode(health_reply.SerializeToString()).decode()}))
        health_result = await health_call
        assert health_result["result"]["isError"] is False

        # A second tenant owns an independent Runtime, Gateway session,
        # credential and real proxy subprocess.  Concurrent calls stay on
        # their respective Mod sockets.
        mod_b = await websockets.connect(f"ws://127.0.0.1:{gateway_port}")
        await mod_b.send(json.dumps({
            "type":"hello", "platform_token":"token-b",
            "route_contract_version":"starcoplay.hosted-route.v1",
            "game_instance_nonce":"nonce-e2e-b", "capability_digest":digest,
            "capability_entries":[{"source_surface":a,"capability_id":b,
                                   "capability_contract_version":c} for a,b,c in entries],
            "player_id":"2", "agent_control_enabled":True,
            "agent_control_target_player_id":"2", "agent_control_revision":1,
        }))
        ack_b = json.loads(await mod_b.recv())
        assert ack_b["type"] == "hello_ok" and ack_b["resumed"] is False
        assert ack_b["game_session_id"] != ack["game_session_id"]
        assert await AuthorityOutboxWorker(bindings, file_authority).run_once() == 1
        credential_b = workspace / "runtime-e2e-b" / "generation-1" / "mcp-credential"
        assert credential_b.exists()
        tenant_b_credential = credential_b.read_text()
        env_b = dict(env); env_b["STARCOPLAY_MCP_CREDENTIAL_PATH"] = str(credential_b)
        proxy_b = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "modules.mcp.transports.stdio_proxy",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env_b,
        )
        async def rpc_b(frame):
            proxy_b.stdin.write((json.dumps(frame)+"\n").encode()); await proxy_b.stdin.drain()
            while True:
                line = await asyncio.wait_for(proxy_b.stdout.readline(), 30)
                if not line: raise AssertionError((await proxy_b.stderr.read()).decode())
                response = json.loads(line)
                if response.get("id") == frame["id"]: return response
        assert "result" in await rpc_b({"jsonrpc":"2.0","id":101,"method":"initialize","params":{}})
        await wait_tools(rpc_b, 3000, list(canonical_names))
        call_a = asyncio.create_task(rpc({"jsonrpc":"2.0","id":102,"method":"tools/call",
                                         "params":{"name":"say","arguments":{"message":"tenant-a"}}}))
        call_b = asyncio.create_task(rpc_b({"jsonrpc":"2.0","id":103,"method":"tools/call",
                                           "params":{"name":"say","arguments":{"message":"tenant-b"}}}))
        wire_a, wire_b = json.loads(await mod.recv()), json.loads(await mod_b.recv())
        env_a = transport_pb2.Envelope(); env_a.ParseFromString(base64.b64decode(wire_a["payload"]))
        env_b_wire = transport_pb2.Envelope(); env_b_wire.ParseFromString(base64.b64decode(wire_b["payload"]))
        assert wire_a["hosted_fence"]["game_instance_nonce"] == "nonce-e2e"
        assert wire_b["hosted_fence"]["game_instance_nonce"] == "nonce-e2e-b"
        for peer, request, session_id, suffix in (
            (mod, env_a, ack["game_session_id"], "a"),
            (mod_b, env_b_wire, ack_b["game_session_id"], "b"),
        ):
            reply = transport_pb2.Envelope(protocol_version="2.4.5", message_id=f"dual-{suffix}",
                request_id=request.request_id, session_id=session_id, timestamp_ms=int(time.time()*1000))
            reply.command_result.command_id=request.command_request.command_id
            reply.command_result.capability="say"; reply.command_result.success=True
            reply.command_result.status=capability_pb2.COMMAND_STATUS_COMPLETED
            await peer.send(json.dumps({"type":"protocol_envelope","encoding":"base64",
                "payload":base64.b64encode(reply.SerializeToString()).decode()}))
        assert (await call_a)["result"]["isError"] is False
        assert (await call_b)["result"]["isError"] is False

        # A Mod disconnect suspends only the binding.  Durable revocation is
        # applied before credential removal and the live proxy loses surface.
        await mod.close()
        for _ in range(100):
            suspended = await bindings.get("binding-e2e")
            if suspended.status is BindingState.SUSPENDED:
                break
            await asyncio.sleep(.01)
        assert suspended.status is BindingState.SUSPENDED
        assert (await runtimes.get_generation("runtime-e2e", 1)).desired_state.value == "running"
        assert await AuthorityOutboxWorker(bindings, file_authority).run_once() == 1
        assert not credential.exists()
        no_surface = await rpc({"jsonrpc":"2.0","id":4,"method":"tools/list","params":{}})
        assert no_surface["result"]["tools"] == []

        # Resume preserves the Gateway-owned gameSession but advances binding
        # version and issues a new credential/MCP session.  The old token is
        # rejected at the real Hosted websocket boundary.
        mod = await websockets.connect(f"ws://127.0.0.1:{gateway_port}")
        await mod.send(json.dumps({
            "type": "hello", "platform_token": "token-a",
            "route_contract_version": "starcoplay.hosted-route.v1",
            "game_instance_nonce": "nonce-e2e", "capability_digest": digest,
            "capability_entries": [{"source_surface": a, "capability_id": b,
                                    "capability_contract_version": c} for a,b,c in entries],
            "player_id": "1", "agent_control_enabled": True,
            "agent_control_target_player_id": "1", "agent_control_revision": 1,
            "resume_token": resumed_after_unknown["resume_token"],
        }))
        resumed_ack = json.loads(await mod.recv())
        assert resumed_ack["type"] == "hello_ok"
        assert resumed_ack["resumed"] is True
        assert resumed_ack["game_session_id"] == ack["game_session_id"]
        resumed = await bindings.get("binding-e2e")
        assert resumed.status is BindingState.ACTIVE
        assert resumed.binding_version > suspended.binding_version
        assert await AuthorityOutboxWorker(bindings, file_authority).run_once() == 1
        assert credential.read_text() != first_credential
        surfaced = await wait_tools(rpc, 4000, list(canonical_names))
        assert "result" in surfaced, surfaced
        assert [tool["name"] for tool in surfaced["result"]["tools"]] == list(canonical_names)
        resumed_credential = credential.read_text()
        changed_entries = tuple(
            (surface, capability, "canonical.v2" if capability == "say" else version)
            for surface, capability, version in entries
        )
        changed_digest = compute_capability_digest(changed_entries)
        await mod.send(json.dumps({
            "type": "capabilities_changed", "capability_digest": changed_digest,
            "capability_entries": [{"source_surface": a, "capability_id": b,
                                    "capability_contract_version": c}
                                   for a,b,c in changed_entries],
        }))
        for _ in range(100):
            changed = await bindings.get("binding-e2e")
            if changed.capability_digest == changed_digest:
                break
            await asyncio.sleep(.01)
        assert changed.binding_version > resumed.binding_version
        changed_worker = AuthorityOutboxWorker(bindings, file_authority)
        assert await changed_worker.run_once() == 1
        assert not credential.exists()  # revoke is durably ordered before issue
        assert await changed_worker.run_once() == 1
        assert credential.read_text() != resumed_credential
        changed_surface = await wait_tools(rpc, 5000, list(canonical_names))
        assert [tool["name"] for tool in changed_surface["result"]["tools"]] == list(canonical_names)
        # Rotation must produce a working credential: prove the new
        # credential can dispatch a real command, not just list tools.
        rot_call = asyncio.create_task(rpc({"jsonrpc":"2.0","id":9090,"method":"tools/call",
            "params":{"name":"say","arguments":{"message":"post-rotation"},
                      "_meta":{"commandId":"hosted-post-rot","toolCallId":"tc-rot"}}}))
        rot_wire = json.loads(await asyncio.wait_for(mod.recv(), 3))
        rot_env = transport_pb2.Envelope()
        rot_env.ParseFromString(base64.b64decode(rot_wire["payload"]))
        rot_reply = transport_pb2.Envelope(
            protocol_version="2.4.5", message_id="rot-reply",
            request_id=rot_env.request_id, session_id=ack["game_session_id"],
            timestamp_ms=int(time.time()*1000),
        )
        rot_reply.command_result.command_id = "hosted-post-rot"
        rot_reply.command_result.capability = "say"
        rot_reply.command_result.success = True
        rot_reply.command_result.status = capability_pb2.COMMAND_STATUS_COMPLETED
        await mod.send(json.dumps({"type":"protocol_envelope","encoding":"base64",
            "payload":base64.b64encode(rot_reply.SerializeToString()).decode()}))
        assert (await rot_call)["result"]["isError"] is False
        # Old credential is revoked: verify it's denied.
        async with ClientSession() as client:
            old_ws = await client.ws_connect(
                f"ws://127.0.0.1:{http_port}/mcp/hosted/ws",
                headers={"Authorization": f"Bearer {first_credential}"},
            )
            await old_ws.send_json({"jsonrpc":"2.0","id":9,"method":"initialize","params":{}})
            old_denied = await old_ws.receive_json()
            assert "error" in old_denied
            await old_ws.close()
        await mod.close()
    finally:
        if proxy:
            proxy.terminate(); await proxy.wait()
            proxy_stderr = (await proxy.stderr.read()).decode()
            assert not first_credential or first_credential not in proxy_stderr
        if proxy_b:
            proxy_b.terminate(); await proxy_b.wait()
            proxy_b_stderr = (await proxy_b.stderr.read()).decode()
            assert not tenant_b_credential or tenant_b_credential not in proxy_b_stderr
        if mod_b:
            await mod_b.close()
        gateway_server.close(); await gateway_server.wait_closed()
        gateway.stop(); await gateway.authenticator.close(); await runner.cleanup(); await pool.close()


async def _real_hosted_transport_scenario(postgres_dsn, workspace) -> None:
    """M3/M4 authority: real Postgres/Gateway with the dynamic Proto Skill surface."""
    import base64
    import json
    import time
    from aiohttp import ClientSession, web
    import websockets
    from agent.generated.protocol import capability_pb2, transport_pb2
    from modules.gateway.hosted_session import HostedSessionCoordinator, HostedSessionRegistry, compute_capability_digest
    from modules.gateway.server import Gateway, GatewayConfig
    from modules.gateway.runtime_contracts import GatewayRouter
    from modules.mcp.capabilities.contracts import build_capability_contract_catalog
    from modules.mcp.capabilities.runtime import RouterBackedCapabilityRuntime
    from modules.mcp.composition import build_mcp_composition
    from modules.mcp.context import HostedClaimsAuthority, SessionExposurePort
    from modules.mcp.context.hosted_credential import HostedCredentialSigner, HostedCredentialVerifier, RevocationStore
    from modules.mcp.core.audit import DurableJsonlAuditSink
    from modules.mcp.core.catalog import ToolCatalog
    from modules.mcp.core.service import McpCore
    from modules.mcp.manifest import build_proto_capability_entries
    from modules.mcp.release_manifest import default_mounted_skill_refs
    from modules.mcp.skills.models import ExposureContext
    from modules.mcp.transports.hosted_server import HostedMcpWebSocketEndpoint
    from modules.runtime_manager.async_application import AuthorityOutboxWorker, AsyncGatewayProjectionService
    from modules.runtime_manager.coordinator import DurableGatewayEventPublisher, HostedGatewayEventConsumer
    from modules.runtime_manager.hosted_mcp_authority import AsyncpgHostedMcpSecurityRepository, DurableHostedCredentialVerifier, HostedCredentialFileAuthority
    from modules.runtime_manager.router_validation import RuntimeBindingProjectionValidator

    pool = await fresh_pool(postgres_dsn)
    await pool.execute("CREATE TABLE IF NOT EXISTS public.profiles(user_id text PRIMARY KEY,user_type text)")
    await pool.execute("CREATE TABLE IF NOT EXISTS public.platform_tokens(token text PRIMARY KEY,user_id text)")
    await pool.execute("""CREATE TABLE IF NOT EXISTS public.system_configs(
        domain text NOT NULL, config_key text NOT NULL, environment text NOT NULL,
        status integer NOT NULL, config_value jsonb NOT NULL,
        PRIMARY KEY(domain,config_key,environment))""")
    await pool.execute("""INSERT INTO public.system_configs VALUES(
        'platform_access','onboarding_policy','prod',1,
        '{"key_access_user_types":["user","member","beta_user"]}'::jsonb)
        ON CONFLICT(domain,config_key,environment) DO UPDATE SET
        status=excluded.status,config_value=excluded.config_value""")
    await pool.execute("""INSERT INTO public.users(user_id,platform_token,invite_code_used)
        VALUES('owner-a','platform-owner-a','test') ON CONFLICT DO NOTHING""")
    await pool.execute("INSERT INTO public.profiles VALUES('owner-a','user') ON CONFLICT DO NOTHING")
    await pool.execute("INSERT INTO public.platform_tokens VALUES('token-a','owner-a') ON CONFLICT DO NOTHING")

    grants = (
        "query_runtime_snapshot", "say",
        "skill:stardew.basic.observe_current_view@1.0.0",
    )
    runtimes = AsyncpgRuntimeProductRepository(pool)
    bindings = AsyncpgBindingRepository(pool)
    await AsyncRuntimeService(runtimes).ensure_runtime(spec(
        runtime_id="runtime-e2e", activation_key="activation-e2e", user_id="owner-a",
        agentee_id="agentee-e2e", allowed_capabilities=grants,
    ))
    await bindings.insert_with_operation(
        RuntimeBinding("binding-e2e", "runtime-e2e", 1, "owner-a", "agentee-e2e"),
        OperationResult("binding:binding-e2e", "create-e2e", "b" * 64, None),
    )
    registry = HostedSessionRegistry()
    publisher = DurableGatewayEventPublisher()
    coordinator = HostedSessionCoordinator(registry, publisher)
    publisher.bind(HostedGatewayEventConsumer(
        AsyncGatewayProjectionService(AsyncpgGatewayProjectionRepository(pool), coordinator),
        bindings, runtimes,
    ))
    security = AsyncpgHostedMcpSecurityRepository(pool)
    keys = await security.load_or_create_keys()
    authority = HostedClaimsAuthority(
        DurableHostedCredentialVerifier(HostedCredentialVerifier(keys, RevocationStore()), security), bindings
    )
    router = GatewayRouter(registry, coordinator, RuntimeBindingProjectionValidator(bindings))
    repo_root = ROOT.parent
    contracts = build_capability_contract_catalog(repo_root)
    exposure = SessionExposurePort(
        authority, lambda context: registry.capability_entries(context.game_session_ref), contracts,
        lambda context: ExposureContext(
            "stardew-valley",
            default_mounted_skill_refs(repo_root / "platform/backend/modules/mcp/skills/builtin"),
            {}, False, "hosted-mount", "hosted-policy", context.binding_ref,
            context.binding_version, context.game_session_ref,
        ),
    )
    class ManifestProvider:
        async def entries(self, context):
            return await registry.capability_entries(context.game_session_ref)
    audit_path = (workspace / "audit" / "hosted.jsonl").resolve()
    audit = DurableJsonlAuditSink(audit_path)
    capability_runtime = RouterBackedCapabilityRuntime(
        router, ManifestProvider(), contracts, lambda context: context.game_session_ref,
        audit=audit,
    )
    composition = build_mcp_composition(
        repo_root=repo_root, exposure_port=exposure,
        capability_runtime=capability_runtime, authority=authority, audit=audit,
    )
    endpoint = HostedMcpWebSocketEndpoint(
        McpCore(authority, router, ToolCatalog(()), projection=composition.projection),
        authority, exposure,
    )
    app = web.Application(); app.router.add_get("/mcp/hosted/ws", endpoint.handle)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    http_port = site._server.sockets[0].getsockname()[1]
    gateway = Gateway(
        GatewayConfig(dsn=postgres_dsn, hosted_route_enabled=True),
        worker_handle=lambda *_: asyncio.sleep(0), game_session_event_hook=coordinator,
        hosted_session_registry=registry,
    )
    gateway_server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    mod = None
    try:
        mod = await websockets.connect(f"ws://127.0.0.1:{gateway_server.sockets[0].getsockname()[1]}")
        entry_dicts = build_proto_capability_entries(repo_root)
        entries = tuple((e["source_surface"], e["capability_id"], e["capability_contract_version"])
                        for e in entry_dicts)
        digest = compute_capability_digest(entries)
        await mod.send(json.dumps({
            "type":"hello", "platform_token":"token-a",
            "route_contract_version":"starcoplay.hosted-route.v1",
            "game_instance_nonce":"nonce-e2e", "capability_digest":digest,
            "capability_entries":[{"source_surface":a,"capability_id":b,
                                   "capability_contract_version":c} for a,b,c in entries],
            "player_id":"1", "agent_control_enabled":True,
            "agent_control_target_player_id":"1", "agent_control_revision":1,
        }))
        ack = json.loads(await mod.recv()); assert ack["type"] == "hello_ok"
        active = await bindings.get("binding-e2e"); assert active.status is BindingState.ACTIVE
        file_authority = HostedCredentialFileAuthority(
            HostedCredentialSigner(keys), security, bindings, workspace, ttl_seconds=300,
        )
        assert await AuthorityOutboxWorker(bindings, file_authority).run_once() == 1
        credential_path = workspace / "runtime-e2e/generation-1/mcp-credential"
        credential = credential_path.read_text()

        async with ClientSession() as client:
            ws = await client.ws_connect(
                f"ws://127.0.0.1:{http_port}/mcp/hosted/ws",
                headers={"Authorization": f"Bearer {credential}"},
            )
            async def rpc(frame):
                await ws.send_json(frame)
                while True:
                    response = await ws.receive_json()
                    if response.get("id") == frame["id"]: return response
            assert "result" in await rpc({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})
            listed = await rpc({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
            expected = {
                "skill:stardew.proto.query_runtime_snapshot@1.0.0",
                "skill:stardew.proto.say@1.0.0",
                "skill:stardew.basic.observe_current_view@1.0.0",
            }
            assert {tool["name"] for tool in listed["result"]["tools"]} == expected

            denied = await rpc({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{
                "name":"skill:stardew.proto.emote@1.0.0", "arguments":{"emote_id":1},
                "_meta":{"toolCallId":"forged"}}})
            assert denied["error"]["data"]["code"] == "capability_denied"
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(mod.recv(), .03)

            async def succeed_call(request_id, name, arguments, tool_call_id, steps=1):
                task = asyncio.create_task(rpc({"jsonrpc":"2.0","id":request_id,
                    "method":"tools/call","params":{"name":name,"arguments":arguments,
                    "_meta":{"toolCallId":tool_call_id}}}))
                for index in range(steps):
                    wire = json.loads(await asyncio.wait_for(mod.recv(), 3))
                    envelope = transport_pb2.Envelope()
                    envelope.ParseFromString(base64.b64decode(wire["payload"]))
                    reply = transport_pb2.Envelope(
                        protocol_version="2.4.5", message_id=f"reply-{request_id}-{index}",
                        request_id=envelope.request_id, session_id=ack["game_session_id"],
                        timestamp_ms=int(time.time()*1000),
                    )
                    reply.command_result.command_id = envelope.command_request.command_id
                    reply.command_result.capability = envelope.command_request.capability
                    reply.command_result.success = True
                    reply.command_result.status = capability_pb2.COMMAND_STATUS_COMPLETED
                    await mod.send(json.dumps({"type":"protocol_envelope","encoding":"base64",
                        "payload":base64.b64encode(reply.SerializeToString()).decode()}))
                result = await task
                assert result["result"]["isError"] is False, result
                return result

            await succeed_call(4, "skill:stardew.proto.query_runtime_snapshot@1.0.0", {}, "read-1")
            await succeed_call(5, "skill:stardew.proto.say@1.0.0", {"message":"hosted"}, "action-1")
            await succeed_call(6, "skill:stardew.basic.observe_current_view@1.0.0", {}, "composite-1", 4)
            await ws.close()

        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        composite = [record for record in records if record.get("tool_call_id") == "composite-1"]
        assert {record.get("request_id") for record in composite if record.get("request_id")} == {"6"}
        assert any(record.get("skill_execution_id") for record in composite)
        assert any(record.get("command_id") for record in composite)
        await security.revoke_jti((await HostedCredentialVerifier(keys, RevocationStore()).verify(credential)).jti)
        async with ClientSession() as client:
            revoked = await client.ws_connect(
                f"ws://127.0.0.1:{http_port}/mcp/hosted/ws",
                headers={"Authorization": f"Bearer {credential}"},
            )
            await revoked.send_json({"jsonrpc":"2.0","id":9,"method":"initialize","params":{}})
            assert "error" in await revoked.receive_json()
            await revoked.close()
    finally:
        if mod is not None: await mod.close()
        gateway_server.close(); await gateway_server.wait_closed()
        gateway.stop(); await gateway.authenticator.close()
        await runner.cleanup(); await pool.close()


def test_real_product_transport_proxy_to_gateway_mod_result(postgres_dsn, tmp_path) -> None:
    asyncio.run(_real_hosted_transport_scenario(postgres_dsn, tmp_path))


def test_production_bootstrap_flags_off_preserves_legacy_composition(postgres_dsn, monkeypatch) -> None:
    from app.bootstrap import create_container
    monkeypatch.setenv("DATABASE_URL", postgres_dsn)
    monkeypatch.setenv("STARCOPLAY_RUNTIME_MANAGER_ENABLED", "0")
    monkeypatch.setenv("HOSTED_GAME_SESSION_ROUTE_ENABLED", "0")
    monkeypatch.setenv("WS_GATEWAY_PORT", "0")
    monkeypatch.setenv("WS_HTTP_PORT", "0")

    async def scenario():
        container = await create_container()
        try:
            assert container.runtime_manager is None
            assert container.runtime_manager_workers is None
            assert container.gateway._config.hosted_route_enabled is False
            assert container.gateway._game_session_event_hook is None
            assert container.http_api._runtime_manager is None
        finally:
            await container.gateway.authenticator.close()
            await container.db_pool.close()
    asyncio.run(scenario())


def test_real_postgres_idle_claim_linearizes_against_effective_activity(postgres_dsn):
    async def scenario():
        pool = await fresh_pool(postgres_dsn)
        repo = AsyncpgRuntimeProductRepository(pool, UNBOUNDED_TEST_ADMISSION)
        reporter = EffectiveActivityReporter(repo)
        baseline = datetime.now(timezone.utc) - timedelta(seconds=601)

        async def ready_runtime(runtime_id, activation_key):
            created = await AsyncRuntimeService(repo).ensure_runtime(
                spec(runtime_id=runtime_id, activation_key=activation_key)
            )
            return await repo.compare_and_swap(
                replace(
                    created, actual_state=RuntimeState.READY_UNBOUND, ready=True,
                    started_at=baseline, last_effective_activity_at=baseline,
                ),
                created.status_version,
            )

        activity_first = await ready_runtime("idle-activity-first", "idle-activity-first")
        activity_done = asyncio.Event()

        async def win_activity():
            result = await reporter.report(
                activity_first.runtime_id, 1, ActivityKind.MCP_SUCCESS,
                baseline + timedelta(seconds=602),
            )
            activity_done.set()
            return result

        async def losing_claim():
            await activity_done.wait()
            return await repo.claim_idle_stop(
                activity_first.runtime_id, 1, activity_first.status_version,
                activity_first.actual_state, baseline + timedelta(seconds=1),
            )

        activity_result, claim_result = await asyncio.gather(win_activity(), losing_claim())
        assert activity_result is True and claim_result is None
        current = await repo.get_generation(activity_first.runtime_id, 1)
        assert current.desired_state.value == "running"
        assert current.actual_state is RuntimeState.READY_UNBOUND

        claim_first = await ready_runtime("idle-claim-first", "idle-claim-first")
        claim_done = asyncio.Event()

        async def win_claim():
            result = await repo.claim_idle_stop(
                claim_first.runtime_id, 1, claim_first.status_version,
                claim_first.actual_state, baseline + timedelta(seconds=1),
            )
            claim_done.set()
            return result

        async def losing_activity():
            await claim_done.wait()
            return await reporter.report(
                claim_first.runtime_id, 1, ActivityKind.USER_INPUT,
                baseline + timedelta(seconds=602),
            )

        claimed, activity_result = await asyncio.gather(win_claim(), losing_activity())
        assert claimed is not None and claimed.actual_state is RuntimeState.DRAINING
        assert activity_result is False
        assert await repo.get_generation(claim_first.runtime_id, 1) == claimed
        await pool.close()

    asyncio.run(scenario())


def test_real_process_fixed_600s_idle_revoke_stop_cleanup_and_no_orphan(postgres_dsn, tmp_path) -> None:
    async def scenario():
        import os
        from datetime import timedelta
        from modules.mcp.context.hosted_credential import HostedCredentialSigner
        from modules.runtime_manager.async_application import AuthorityOutboxWorker
        from modules.runtime_manager.coordinator import BindingOutboxAuthorityController, RuntimeManagerCoordinator
        from modules.runtime_manager.domain import RuntimeState
        from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
        from modules.runtime_manager.hosted_mcp_authority import AsyncpgHostedMcpSecurityRepository, HostedCredentialFileAuthority
        from modules.runtime_manager.persona import PersonaArtifactSnapshot

        pool = await fresh_pool(postgres_dsn)
        runtimes, bindings = AsyncpgRuntimeProductRepository(pool), AsyncpgBindingRepository(pool)
        class Persona:
            async def read(self, reference, user_id, agentee_id):
                import hashlib
                payload = "persona"
                return PersonaArtifactSnapshot(
                    agentee_id, "rev-1", hashlib.sha256(payload.encode()).hexdigest(),
                    "now", payload, (reference,),
                )
        manager = RuntimeManagerCoordinator(
            runtimes, Persona(), BindingOutboxAuthorityController(bindings),
            {"fake": FakeAgentRuntimeAdapter()}, tmp_path,
        )
        product = spec(runtime_id="runtime-ttl-real", activation_key="activation-ttl-real",
                       adapter_kind="fake", user_id="ttl-user", agentee_id="ttl-agentee")
        await AsyncRuntimeService(runtimes).ensure_runtime(product)
        active = RuntimeBinding(
            "binding-ttl-real", "runtime-ttl-real", 1, "ttl-user", "ttl-agentee",
            2, 1, BindingState.ACTIVE, "game-ttl", "nonce-ttl", "digest-ttl", "resume-ttl",
        )
        await bindings.insert_with_operation(
            active, OperationResult("binding:binding-ttl-real", "create-ttl", "d"*64, None)
        )
        record = await manager.ensure(product)
        assert record.actual_state is RuntimeState.RUNNING_BOUND
        handle = manager._handles[(record.runtime_id, record.generation)]
        pid = handle.transport.pid
        security = AsyncpgHostedMcpSecurityRepository(pool)
        keys = await security.load_or_create_keys()
        file_authority = HostedCredentialFileAuthority(
            HostedCredentialSigner(keys), security, bindings, tmp_path,
        )
        await file_authority.issue(active, allowed_tools=("say",), jti="ttl-jti")
        workdir = tmp_path / "runtime-ttl-real" / "generation-1"
        assert pid and workdir.exists() and (workdir / "mcp-credential").exists()
        assert await manager.sweep_idle(record.last_effective_activity_at + timedelta(seconds=3601)) == 1
        stopped = await runtimes.get_generation("runtime-ttl-real", 1)
        assert stopped.actual_state is RuntimeState.STOPPED
        assert not workdir.exists()
        for _ in range(100):
            try: os.kill(pid, 0)
            except ProcessLookupError: break
            await asyncio.sleep(.01)
        else: pytest.fail("idle-stopped runtime process survived")
        assert await AuthorityOutboxWorker(bindings, file_authority).run_once() == 1
        assert await manager.reconcile_all() == 0
        await pool.close()
    asyncio.run(scenario())


def test_real_postgres_config_roll_recovers_failed_generation_and_serializes(postgres_dsn, tmp_path) -> None:
    async def scenario():
        from modules.runtime_manager.adapter_config import AdapterConfigSnapshot
        from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
        from tests.runtime_manager.test_product_composition import Adapter, Authority, PersonaReader
        pool = await fresh_pool(postgres_dsn)
        from modules.http_api.runtime_control_claims import RuntimeControlClaimsVerifier, issue_runtime_control_claim
        claim = issue_runtime_control_claim("pg-claims-secret", "runtime-operator", jti="pg-once")
        verifier = RuntimeControlClaimsVerifier(
            "pg-claims-secret", {"runtime-operator":{"all_tenants":True}}, pool)
        assert (await verifier.verify(claim))["all_tenants"] is True
        with pytest.raises(ValueError, match="invalid_runtime_control_claim"):
            await verifier.verify(claim)
        repo, authority, adapter = (
            AsyncpgRuntimeProductRepository(pool, UNBOUNDED_TEST_ADMISSION), Authority(), Adapter()
        )
        class Resolver:
            async def resolve(self, reference, runtime_id, user_id, agentee_id, provider):
                revision = reference.rsplit("/", 1)[-1] if reference != "adapter/default" else "default"
                return AdapterConfigSnapshot(runtime_id,user_id,agentee_id,revision,
                    (revision[0] if revision else "d")*64,provider,"model-"+revision,"default",(),{})
        manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake":adapter}, tmp_path,
                                             adapter_config_resolver=Resolver())
        base = spec(runtime_id="roll-real", activation_key="roll-activation", adapter_kind="fake")
        first = await manager.ensure(base)
        complete = repo.complete_roll_operation
        async def crash_before_complete(*args, **kwargs): return False
        repo.complete_roll_operation = crash_before_complete
        with pytest.raises(RuntimeError, match="operation_lease_lost"):
            await manager.roll_adapter_config("roll-real", "runtime-config/c/2", "roll-crash", 1)
        assert (await repo.get("roll-real")).generation == 2
        repo.complete_roll_operation = complete
        crash_recovered = await manager.roll_adapter_config(
            "roll-real", "runtime-config/c/2", "roll-crash", 1
        )
        assert crash_recovered.generation == 2
        adapter.ready = False
        with pytest.raises(RuntimeError, match="ready_timeout"):
            await manager.roll_adapter_config("roll-real", "runtime-config/c/3", "roll-fail", 2)
        failed = await repo.get("roll-real")
        assert failed.generation == 3
        failed_reason = failed.exit_reason
        adapter.ready = True
        recovered = await manager.roll_adapter_config("roll-real", "runtime-config/c/3", "roll-retry", 3)
        assert recovered.generation == 4
        assert (await repo.get_generation("roll-real", 3)).exit_reason == failed_reason
        rollback = await manager.roll_adapter_config("roll-real", "adapter/default", "roll-back", 4)
        assert rollback.generation == 5
        newer = await manager.roll_adapter_config("roll-real", "runtime-config/c/4", "roll-new", 5)
        assert newer.generation == 6
        exact_replay = await manager.roll_adapter_config("roll-real", "adapter/default", "roll-back", 4)
        assert exact_replay.generation == 5 and exact_replay.actual_state is RuntimeState.READY_UNBOUND

        # Two managers with independent pools cannot bypass the durable lease,
        # for either the same operation or a different operation.
        pool2 = await asyncpg.create_pool(postgres_dsn, min_size=1, max_size=2)
        repo2 = AsyncpgRuntimeProductRepository(pool2, UNBOUNDED_TEST_ADMISSION)
        manager2 = RuntimeManagerCoordinator(repo2, PersonaReader(), Authority(), {"fake":Adapter()},
            tmp_path / "manager-2", adapter_config_resolver=Resolver())
        entered, release_complete = asyncio.Event(), asyncio.Event()
        original_complete = repo.complete_roll_operation
        async def delayed_complete(*args, **kwargs):
            entered.set(); await release_complete.wait()
            return await original_complete(*args, **kwargs)
        repo.complete_roll_operation = delayed_complete
        first_manager_task = asyncio.create_task(manager.roll_adapter_config(
            "roll-real", "runtime-config/alias/4", "two-manager-same", 6))
        await entered.wait()
        with pytest.raises(RuntimeError, match="operation_in_progress"):
            await manager2.roll_adapter_config("roll-real", "runtime-config/alias/4", "two-manager-same", 6)
        with pytest.raises(RuntimeError, match="operation_in_progress"):
            await manager2.roll_adapter_config("roll-real", "runtime-config/other/4", "two-manager-different", 6)
        release_complete.set()
        held_result = await first_manager_task
        repo.complete_roll_operation = original_complete
        replayed = await manager2.roll_adapter_config(
            "roll-real", "runtime-config/alias/4", "two-manager-same", 6)
        assert replayed.generation == held_result.generation == 6

        # A successor epoch fences every stale runtime mutation, not only final completion.
        epoch1 = await repo.acquire_roll_lease("roll-real", "fence-1", "holder-1", "a"*64,
            6, 7, "runtime-config/c/5", "5"*64)
        await pool.execute("UPDATE runtime_config_roll_leases SET expires_at=now()-interval '1 second' WHERE runtime_id='roll-real'")
        epoch2 = await repo2.acquire_roll_lease("roll-real", "fence-2", "holder-2", "b"*64,
            6, 7, "runtime-config/c/6", "6"*64)
        assert epoch2 == epoch1 + 1
        latest = await repo.get("roll-real")
        with pytest.raises(Conflict, match="roll lease lost"):
            await repo.fenced_compare_and_swap(replace(latest, ready=False), latest.status_version,
                                                "holder-1", epoch1)
        await repo2.release_roll_lease("roll-real", "holder-2", epoch2)
        await pool2.close()

        outcomes = await asyncio.gather(
            manager.roll_adapter_config("roll-real", "runtime-config/c/5", "roll-race-a", 6),
            manager.roll_adapter_config("roll-real", "runtime-config/c/6", "roll-race-b", 6),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert (await repo.get("roll-real")).generation == 7
        assert len([record for record in await repo.list_active() if record.runtime_id == "roll-real"]) == 1

        # Crash recovery never promotes an in-progress generation. The same
        # durable operation becomes completable only after persisted readiness evidence exists.
        starting_base = spec(runtime_id="roll-starting", activation_key="roll-starting-key", adapter_kind="fake")
        starting_first = await manager.ensure(starting_base)
        await manager.stop("roll-starting", 1, "prepare-starting-crash", "test")
        starting_spec = replace(starting_base, generation=2, adapter_config_ref="runtime-config/c/9")
        starting = await AsyncRuntimeService(repo).ensure_runtime(starting_spec)
        await pool.execute(
            "UPDATE runtime_instances SET adapter_config_hash=$3 WHERE runtime_id=$1 AND generation=$2",
            "roll-starting", 2, "9"*64)
        starting = await repo.compare_and_swap(
            replace(starting, actual_state=RuntimeState.STARTING), starting.status_version)
        with pytest.raises(Conflict, match="generation changed"):
            await manager.roll_adapter_config("roll-starting", "runtime-config/c/9", "starting-crash", 1)
        pending = await repo.get_operation("runtime-roll:roll-starting", "starting-crash")
        assert pending.result["status"] == "pending"
        ready = await repo.compare_and_swap(
            replace(starting, actual_state=RuntimeState.READY_UNBOUND, ready=True), starting.status_version)
        recovered_ready = await manager.roll_adapter_config(
            "roll-starting", "runtime-config/c/9", "starting-crash", 1)
        assert recovered_ready.generation == 2 and recovered_ready.ready
        await pool.close()
    asyncio.run(scenario())
