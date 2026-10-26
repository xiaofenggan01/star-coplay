from __future__ import annotations

import hashlib
import json
import stat
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from modules.runtime_manager.adapter_config import DbAdapterConfigResolver, LegacyAdapterConfigResolver
from modules.runtime_manager.adapters.claude.adapter import ClaudeAgentRuntimeAdapter
from modules.runtime_manager.adapters.claude.protocol import resolve_launch_config
from modules.runtime_manager.contracts import (
    PersonaArtifact,
    ReconcileAction,
    RuntimeActualState,
    RuntimeRecord,
    RuntimeSpec,
)
from modules.runtime_manager.adapter_config import AdapterConfigSnapshot


class Pool:
    def __init__(self, row): self.row = row
    async def fetchrow(self, query, *args): return self.row


def test_claude_launch_defaults_to_interactive_permission_mode_without_extra_grants():
    command = resolve_launch_config({"claude_binary": "/controlled/claude"}).command()
    assert command[command.index("--permission-mode") + 1] == "default"
    assert "--allowedTools" not in command


@pytest.mark.asyncio
async def test_explicit_adapter_snapshot_is_owner_bound_hashed_and_resolved():
    row = {
        "runtime_id":"runtime-a", "user_id":"user-a", "agentee_id":"agentee-a",
        "revision":"7", "provider":"claude", "model":"resolved-model",
        "permission_mode":"default", "allowed_tools":["say"],
        "provider_settings":{"ready_deadline_seconds":12},
    }
    resolver = DbAdapterConfigResolver(Pool(row))
    snapshot = await resolver.resolve(
        "runtime-config/config-a/7", "runtime-a", "user-a", "agentee-a", "claude"
    )
    assert snapshot.model == "resolved-model" and snapshot.allowed_tools == ("say",)
    assert len(snapshot.content_hash) == 64
    with pytest.raises(ValueError, match="owner_mismatch"):
        await resolver.resolve(
            "runtime-config/config-a/7", "runtime-a", "user-b", "agentee-a", "claude"
        )


@pytest.mark.asyncio
async def test_explicit_snapshot_accepts_production_asyncpg_json_text_codec():
    row = {
        "runtime_id": "runtime-a", "user_id": "user-a", "agentee_id": "agentee-a",
        "revision": "7", "provider": "claude", "model": "resolved-model",
        "permission_mode": "default", "allowed_tools": '["say", "go_to"]',
        "provider_settings": '{"ready_deadline_seconds": 12}',
    }
    snapshot = await DbAdapterConfigResolver(Pool(row)).resolve(
        "runtime-config/config-a/7", "runtime-a", "user-a", "agentee-a", "claude"
    )
    assert snapshot.allowed_tools == ("go_to", "say")
    assert snapshot.provider_settings == {"ready_deadline_seconds": 12.0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_settings", "not-json"),
        ("provider_settings", "[]"),
        ("allowed_tools", "not-json"),
        ("allowed_tools", '{"say": true}'),
        ("allowed_tools", '["say", 1]'),
        ("allowed_tools", "say"),
    ],
)
async def test_explicit_snapshot_rejects_invalid_json_codec_shapes(field, value):
    row = {
        "runtime_id": "runtime-a", "user_id": "user-a", "agentee_id": "agentee-a",
        "revision": "7", "provider": "claude", "model": "resolved-model",
        "permission_mode": "default", "allowed_tools": '["say"]',
        "provider_settings": '{}',
    }
    row[field] = value
    with pytest.raises(ValueError, match=f"adapter_{field}"):
        await DbAdapterConfigResolver(Pool(row)).resolve(
            "runtime-config/config-a/7", "runtime-a", "user-a", "agentee-a", "claude"
        )


@pytest.mark.asyncio
async def test_profile_fallback_accepts_json_text_but_rejects_non_object():
    row = {
        "config": json.dumps({"runtime_adapter": {
            "model": "m", "allowed_tools": '["say"]',
            "provider_settings": '{"turn_deadline_seconds": 20}',
        }}),
        "updated_at": object(),
    }
    snapshot = await DbAdapterConfigResolver(Pool(row)).resolve(
        "adapter/default", "r", "u", "a", "claude"
    )
    assert snapshot.allowed_tools == ("say",)
    assert snapshot.provider_settings == {"turn_deadline_seconds": 20.0}

    row["config"] = "[]"
    with pytest.raises(ValueError, match="adapter_profile_config_invalid"):
        await DbAdapterConfigResolver(Pool(row)).resolve(
            "adapter/default", "r", "u", "a", "claude"
        )


@pytest.mark.asyncio
async def test_legacy_fallback_is_narrow_and_opaque_refs_are_rejected():
    resolver = LegacyAdapterConfigResolver()
    fallback = await resolver.resolve("adapter/default", "r", "u", "a", "claude")
    assert fallback.revision == "legacy-default-v1"
    assert fallback.permission_mode == "default"
    with pytest.raises(ValueError, match="not_found"):
        await resolver.resolve("opaque/provider/string", "r", "u", "a", "claude")


@pytest.mark.asyncio
async def test_empty_profile_fallback_defaults_to_safe_permission_mode():
    snapshot = await DbAdapterConfigResolver(Pool({"config": {}, "updated_at": object()})).resolve(
        "adapter/default", "r", "u", "a", "claude"
    )
    assert snapshot.permission_mode == "default"


@pytest.mark.asyncio
async def test_explicit_profile_permission_mode_remains_compatible():
    snapshot = await DbAdapterConfigResolver(Pool({
        "config": {"runtime_adapter": {"permission_mode": "bypassPermissions"}},
        "updated_at": object(),
    })).resolve("adapter/default", "r", "u", "a", "claude")
    assert snapshot.permission_mode == "bypassPermissions"


@pytest.mark.asyncio
async def test_profile_fallback_revision_depends_only_on_runtime_adapter_document():
    row = {"config":{"runtime_adapter":{"model":"m"}}, "updated_at":object()}
    first = await DbAdapterConfigResolver(Pool(row)).resolve("adapter/default", "r", "u", "a", "claude")
    row["updated_at"] = object()  # unrelated nickname/profile write
    second = await DbAdapterConfigResolver(Pool(row)).resolve("adapter/default", "r", "u", "a", "claude")
    assert first.revision == second.revision and first.content_hash == second.content_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [
    {"model":"evil"}, {"claude_binary":"/tmp/evil"}, {"claude_settings_path":"/secret"},
    {"ready_deadline_seconds":0}, {"turn_deadline_seconds":"slow"},
])
async def test_provider_settings_cannot_override_reserved_fields(settings):
    row = {"runtime_id":"r","user_id":"u","agentee_id":"a","revision":"1",
           "provider":"claude","model":"m","permission_mode":"default",
           "allowed_tools":[],"provider_settings":settings}
    with pytest.raises(ValueError, match="provider_setting"):
        await DbAdapterConfigResolver(Pool(row)).resolve("runtime-config/c/1", "r", "u", "a", "claude")


@pytest.mark.asyncio
async def test_claude_launch_consumes_resolved_config_and_instruction_only_prompt_file(tmp_path):
    payload = "You are a careful farming companion."
    artifact = PersonaArtifact("agentee-a", "persona-9", hashlib.sha256(payload.encode()).hexdigest(), payload)
    spec = RuntimeSpec(
        "runtime-a", 3, "user-a", "agentee-a", "claude", tmp_path,
        persona_artifact=artifact,
        provider_private_metadata={
            "claude_binary":"/controlled/claude", "model":"resolved-model",
            "permission_mode":"default", "tools":"say,observe",
            "provider_secret":"must-not-enter-prompt",
        },
    )
    prepared = await ClaudeAgentRuntimeAdapter().prepare(spec)
    metadata = {**spec.provider_private_metadata, **prepared.provider_private_metadata}
    command = resolve_launch_config(metadata).command()
    prompt_path = Path(metadata["append_system_prompt_file"])
    assert command[:4] == ["/controlled/claude", "-p", "--model", "resolved-model"]
    assert command[command.index("--permission-mode") + 1] == "default"
    assert command[command.index("--tools") + 1] == "say,observe"
    assert command[command.index("--append-system-prompt-file") + 1] == str(prompt_path)
    assert prompt_path.read_text() == payload
    assert "must-not-enter-prompt" not in prompt_path.read_text()
    assert (prepared.workdir / "persona-artifact.json").exists()
    owner = json.loads((prepared.workdir / "runtime-owner.json").read_text())
    assert "adapter_config_hash" in owner and "adapter_config_revision" in owner


def test_claude_launch_can_pin_node_for_the_installed_cli():
    command = resolve_launch_config({
        "claude_binary": "C:/nvm/v22/node.exe",
        "claude_cli_path": "C:/tools/claude-code/cli.js",
    }).command()

    assert command[:3] == [
        "C:/nvm/v22/node.exe",
        "C:/tools/claude-code/cli.js",
        "-p",
    ]


@pytest.mark.asyncio
async def test_hosted_claude_prompt_requires_spoken_replies_through_say(tmp_path):
    payload = "You are a friendly farming companion."
    artifact = PersonaArtifact("agentee-a", "persona-9", hashlib.sha256(payload.encode()).hexdigest(), payload)
    spec = RuntimeSpec(
        "runtime-a", 3, "user-a", "agentee-a", "claude", tmp_path,
        persona_artifact=artifact,
        provider_private_metadata={
            "claude_binary": "/controlled/claude",
            "hosted_mcp_endpoint": "ws://127.0.0.1:8766/mcp/hosted/ws",
        },
    )

    prepared = await ClaudeAgentRuntimeAdapter().prepare(spec)
    prompt_path = Path(prepared.provider_private_metadata["append_system_prompt_file"])
    prompt = prompt_path.read_text(encoding="utf-8")

    assert payload in prompt
    assert "mcp__starcoplay-hosted__say" in prompt
    assert "tool's `message` argument" in prompt
    assert "tool's `content` argument" not in prompt
    assert "Do not return the spoken reply as assistant text" in prompt
    assert "Assistant text is private" in prompt
    assert "at most one `say` call" in prompt
    assert "Steering preview" in prompt
    assert "must not call `say`" in prompt
    assert "Internal background game task completion" in prompt
    assert "exactly one `say`" in prompt
    assert "independent turn" in prompt
    assert "Never tell the player it succeeded until the completion result arrives" in prompt
    assert "do not guess game state, routes, exits, or the player's location" in prompt
    assert "do not call `interact` for that warp" in prompt
    assert "query the current game state before deciding whether to retry" in prompt
    assert "`warp_triggered=true`" in prompt
    assert "Never retry `INVALID_PARAMS` with identical arguments" in prompt
    assert "retry at most once" in prompt
    assert "bounded number of alternative approaches" in prompt
    assert "treat the old background job as cancelled" in prompt
    assert "stop the old plan immediately" in prompt
    assert "named destination" in prompt
    assert "go_to_place" in prompt
    assert "exact map id and destination tile" in prompt
    assert "Never replace a failed named-place navigation with repeated `move_to`" in prompt


@pytest.mark.skipif(os.name != "nt", reason="Windows process liveness regression")
@pytest.mark.asyncio
async def test_claude_reconcile_recognizes_live_windows_pid(tmp_path):
    workdir = tmp_path / "runtime" / "generation-1"
    workdir.mkdir(parents=True)
    (workdir / "runtime-owner.json").write_text(json.dumps({
        "runtime_id": "runtime", "generation": 1,
        "nonce": "owner-nonce", "pid": os.getpid(),
    }))
    record = RuntimeRecord(
        "runtime", 1, RuntimeActualState.READY, RuntimeActualState.READY,
        workdir, "owner-nonce",
    )
    adapter = ClaudeAgentRuntimeAdapter()
    adapter._terminate_orphan = AsyncMock()

    decision = await adapter.reconcile(record)

    assert decision.action is ReconcileAction.TERMINATE_ORPHAN
    adapter._terminate_orphan.assert_awaited_once_with(os.getpid())


@pytest.mark.skipif(os.name != "nt", reason="Windows process liveness regression")
@pytest.mark.asyncio
async def test_claude_reconcile_handles_exited_windows_pid(tmp_path):
    workdir = tmp_path / "runtime" / "generation-1"
    workdir.mkdir(parents=True)
    exited_pid = 2_147_483_647
    (workdir / "runtime-owner.json").write_text(json.dumps({
        "runtime_id": "runtime", "generation": 1,
        "nonce": "owner-nonce", "pid": exited_pid,
    }))
    record = RuntimeRecord(
        "runtime", 1, RuntimeActualState.READY, RuntimeActualState.READY,
        workdir, "owner-nonce",
    )

    decision = await ClaudeAgentRuntimeAdapter().reconcile(record)

    assert decision.action is ReconcileAction.MARK_FAILED
    assert decision.detail["pid"] == exited_pid


def test_persona_revision_gets_generation_scoped_workdir(tmp_path):
    adapter = ClaudeAgentRuntimeAdapter()
    async def prepare(generation, revision, content):
        artifact = PersonaArtifact("a", revision, hashlib.sha256(content.encode()).hexdigest(), content)
        return await adapter.prepare(RuntimeSpec("runtime", generation, "u", "a", "claude", tmp_path,
            persona_artifact=artifact, provider_private_metadata={"claude_binary":"claude"}))
    import asyncio
    first = asyncio.run(prepare(1, "rev-1", "one"))
    second = asyncio.run(prepare(2, "rev-2", "two"))
    assert first.workdir != second.workdir
    assert (first.workdir / "persona-instructions.md").read_text() == "one"
    assert (second.workdir / "persona-instructions.md").read_text() == "two"


def test_persona_prompt_rejects_symlink_target(tmp_path):
    from modules.runtime_manager.workspace import materialize_persona_prompt
    workdir = tmp_path / "work"; workdir.mkdir()
    outside = tmp_path / "outside"; outside.write_text("do not overwrite")
    (workdir / "persona-instructions.md").symlink_to(outside)
    artifact = PersonaArtifact("a", "r", hashlib.sha256(b"safe").hexdigest(), "safe")
    with pytest.raises(ValueError, match="symlink"):
        materialize_persona_prompt(workdir, artifact)
    assert outside.read_text() == "do not overwrite"


@pytest.mark.asyncio
async def test_controlled_claude_process_receives_resolved_argv_persona_path_and_input(tmp_path):
    executable = tmp_path / "controlled-claude"
    argv_log, input_log = tmp_path / "argv.json", tmp_path / "input.jsonl"
    executable.write_text(f"""#!/usr/bin/env python3
import json,os,sys
open({str(argv_log)!r},'w').write(json.dumps(sys.argv[1:]))
for line in sys.stdin:
 open({str(input_log)!r},'a').write(line)
 print(json.dumps({{'type':'system','subtype':'init','session_id':'controlled'}}), flush=True)
 print(json.dumps({{'type':'result','subtype':'success','is_error':False,'result':'OK'}}), flush=True)
""")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    payload = "Persona instructions actually consumed from this file."
    artifact = PersonaArtifact("a", "rev", hashlib.sha256(payload.encode()).hexdigest(), payload)
    spec = RuntimeSpec(
        "runtime-process", 1, "u", "a", "claude", tmp_path / "workspaces",
        persona_artifact=artifact,
        provider_private_metadata={"claude_binary":str(executable), "model":"resolved-process-model",
                                   "permission_mode":"default", "tools":"say"},
    )
    adapter = ClaudeAgentRuntimeAdapter()
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    try:
        assert (await adapter.wait_ready(handle, 10)).ready
        argv = json.loads(argv_log.read_text())
        prompt_path = argv[argv.index("--append-system-prompt-file") + 1]
        assert argv[argv.index("--model") + 1] == "resolved-process-model"
        assert Path(prompt_path).read_text() == payload
        assert "internal readiness check" in input_log.read_text()
    finally:
        await adapter.stop(handle, "test", 1)


@pytest.mark.asyncio
async def test_cancel_during_real_process_readiness_terminates_and_cleans_runtime(tmp_path):
    from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
    from modules.runtime_manager.domain import RuntimeProductSpec, RuntimeState
    from tests.runtime_manager.test_product_composition import MemoryRepository, Authority, PersonaReader
    executable, pid_file = tmp_path / "blocking-claude", tmp_path / "pid"
    executable.write_text(f"""#!/usr/bin/env python3
import os,time
open({str(pid_file)!r},'w').write(str(os.getpid()))
for _ in __import__('sys').stdin:
 time.sleep(60)
""")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    repo, authority, adapter = MemoryRepository(), Authority(), ClaudeAgentRuntimeAdapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"claude":adapter}, tmp_path / "runtimes",
        adapter_metadata={"claude":{"claude_binary":str(executable)}},
    )
    product = RuntimeProductSpec("cancel-runtime",1,"cancel-activation","user-1","agentee-1","claude",
        "adapter/default","persona/rev-1",idle_ttl_seconds=600)
    task = asyncio.create_task(manager.ensure(product))
    for _ in range(200):
        if pid_file.exists(): break
        await asyncio.sleep(.01)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    for _ in range(100):
        try: os.kill(pid, 0)
        except ProcessLookupError: break
        await asyncio.sleep(.01)
    else: pytest.fail("cancelled readiness process survived")
    record = await repo.get_generation("cancel-runtime", 1)
    assert record.actual_state is RuntimeState.FAILED and record.exit_reason == "startup_cancelled"
    assert ("revoke","cancel-runtime",1,"startup_cancelled") in authority.events
    assert ("cancel-runtime",1) not in manager._handles
    assert not (tmp_path / "runtimes/cancel-runtime/generation-1").exists()


@pytest.mark.asyncio
async def test_double_cancel_during_real_process_cleanup_completes_all_steps(tmp_path):
    """Second cancel during _cleanup_cancelled_start must not leak the cleanup
    task.  Every critical step — authority revoke, real subprocess stop,
    handle/workdir teardown, FAILED audit — must complete, and the original
    CancelledError must be preserved."""
    from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
    from modules.runtime_manager.domain import RuntimeProductSpec, RuntimeState
    from tests.runtime_manager.test_product_composition import MemoryRepository, Authority, PersonaReader

    executable, pid_file = tmp_path / "blocking-double", tmp_path / "pid-double"
    executable.write_text(f"""#!/usr/bin/env python3
import os,time
open({str(pid_file)!r},'w').write(str(os.getpid()))
for _ in __import__('sys').stdin:
 time.sleep(60)
""")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    repo, authority, adapter = MemoryRepository(), Authority(), ClaudeAgentRuntimeAdapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"claude": adapter}, tmp_path / "runtimes",
        adapter_metadata={"claude": {"claude_binary": str(executable)}},
        stop_grace_seconds=1.0,
    )
    product = RuntimeProductSpec("dc-runtime", 1, "dc-activation", "user-1", "agentee-1", "claude",
                                  "adapter/default", "persona/rev-1", idle_ttl_seconds=600)
    task = asyncio.create_task(manager.ensure(product))
    for _ in range(200):
        if pid_file.exists(): break
        await asyncio.sleep(.01)
    assert pid_file.exists()
    pid = int(pid_file.read_text())

    # First cancel — triggers _cleanup_cancelled_start
    task.cancel()
    # Yield so the event loop delivers the first CancelledError and cleanup
    # enters its critical section (authority revoke, then adapter.stop which
    # takes at least stop_grace_seconds=1.0 to escalate past the ignored
    # app-stop frame).
    await asyncio.sleep(0.05)

    # Second cancel — must not leak the cleanup task or skip any cleanup step
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # ── assertions ──────────────────────────────────────────────────────
    # 1. PID exited — the real subprocess was stopped
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail("double-cancelled runtime process survived (pid={})".format(pid))

    # 2. Authority revoked before any other cleanup
    assert ("revoke", "dc-runtime", 1, "startup_cancelled") in authority.events

    # 3. DB state is auditable FAILED
    record = await repo.get_generation("dc-runtime", 1)
    assert record.actual_state is RuntimeState.FAILED
    assert record.exit_reason == "startup_cancelled"

    # 4. No leaked handle
    assert ("dc-runtime", 1) not in manager._handles

    # 5. Workdir cleaned
    assert not (tmp_path / "runtimes/dc-runtime/generation-1").exists()


@pytest.mark.asyncio
async def test_live_config_change_rolls_generation_but_same_snapshot_does_not(tmp_path):
    from tests.runtime_manager.test_product_composition import (
        Adapter, Authority, MemoryRepository, PersonaReader, spec,
    )
    from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
    class Resolver:
        async def resolve(self, reference, runtime_id, user_id, agentee_id, provider):
            revision = "1" if reference in {"adapter/default", "runtime-config/c/1"} else "2"
            return AdapterConfigSnapshot(runtime_id, user_id, agentee_id, revision,
                revision * 64, provider, "model-" + revision, "default", (), {})
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake":adapter}, tmp_path,
        adapter_config_resolver=Resolver(),
    )
    first = await manager.ensure(spec())
    unchanged = await manager.roll_adapter_config("runtime-1", "runtime-config/c/1", "roll-same")
    assert unchanged.generation == first.generation and "stop" not in adapter.events
    rolled = await manager.roll_adapter_config("runtime-1", "runtime-config/c/2", "roll-new")
    assert rolled.generation == first.generation + 1
    assert authority.events[-2][0] == "revoke"
    assert "stop" in adapter.events and adapter.events.count("start") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("loss_point", ["before_allow", "after_allow"])
async def test_ingress_is_revoked_when_roll_fence_is_lost_around_allow(tmp_path, loss_point):
    from tests.runtime_manager.test_product_composition import (
        Adapter, Authority, MemoryRepository, PersonaReader, spec,
    )
    from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
    class FencedRepo(MemoryRepository):
        valid = True
        async def validate_roll_lease(self, *args): return self.valid
        async def fenced_ensure(self, record, digest, holder, epoch, config_hash):
            return await self.ensure(record, digest)
        async def fenced_compare_and_swap(self, record, version, holder, epoch):
            if not self.valid: raise RuntimeError("operation_lease_lost")
            result = await self.compare_and_swap(record, version)
            if loss_point == "before_allow" and result.ready: self.valid = False
            return result
    repo, adapter = FencedRepo(), Adapter()
    class RacingAuthority(Authority):
        async def allow_ingress(self, *identity):
            await super().allow_ingress(*identity)
            if loss_point == "after_allow": repo.valid = False
    authority = RacingAuthority()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake":adapter}, tmp_path)
    lost = asyncio.Event()
    with pytest.raises(RuntimeError, match="operation_lease_lost"):
        await manager.ensure(spec(), roll_fence=("holder",1,lost))
    if loss_point == "before_allow":
        assert not any(event[0] == "allow" for event in authority.events)
    else:
        assert any(event[0] == "allow" for event in authority.events)
    assert any(event[0] == "revoke" for event in authority.events)
    assert "stop" in adapter.events
    assert ("runtime-1",1) not in manager._handles
    assert not (tmp_path / "runtime-1/generation-1").exists()
