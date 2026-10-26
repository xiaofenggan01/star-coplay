"""Hosted M4 evidence through credential -> Registry -> Skill/Capability Runtime."""
from __future__ import annotations

import copy
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from modules.mcp.capabilities.contracts import build_capability_contract_catalog
from modules.mcp.capabilities.models import ProtocolCapabilityEntry
from modules.mcp.capabilities.runtime import RouterBackedCapabilityRuntime
from modules.mcp.capabilities.snapshot import compute_capability_snapshot_digest
from modules.mcp.composition import build_mcp_composition
from modules.mcp.context import HostedClaimsAuthority, SessionExposurePort, hosted_risk_policy
from modules.mcp.context.hosted_credential import (
    HostedCredentialSigner, HostedCredentialVerifier, KeyStore, RevocationStore,
    generate_signing_key,
)
from modules.mcp.contracts.models import CommandState, GameCommandResult
from modules.mcp.core.audit import MemoryAuditSink
from modules.mcp.core.catalog import ToolCatalog
from modules.mcp.core.service import McpCore
from modules.mcp.fakes.router import FakeGameSessionRouter, RouteFence
from modules.mcp.manifest import build_proto_capability_entries
from modules.mcp.release_manifest import default_mounted_skill_refs
from modules.mcp.skills.models import ExposureContext, RiskDecision
from modules.mcp.transports.hosted_server import HostedMcpJsonRpcSession
import modules.mcp.transports.hosted_server as hosted_server
from modules.runtime_manager.authority import AuthorityMaterial
from modules.runtime_manager.domain import BindingState, RuntimeBinding


ROOT = Path(__file__).resolve().parents[4]
VECTORS = json.loads(
    (ROOT / "agent/tests/fixtures/canonical-result-vectors.json").read_text()
)["vectors"]


class _Bindings:
    def __init__(self, binding): self.binding = binding
    async def get(self, binding_id):
        return self.binding if binding_id == self.binding.binding_id else None


class _Entries:
    def __init__(self, entries): self.value = entries
    async def entries(self, _context): return self.value


class _ResultRouter(FakeGameSessionRouter):
    def __init__(self, fence, *, fail_capability=None, dispatch_gate=None):
        super().__init__(fence)
        self.fail_capability = fail_capability
        self.dispatch_gate = dispatch_gate
        self.dispatch_entered = asyncio.Event()

    async def dispatch(self, context, command):
        if self.dispatch_gate is not None:
            self.dispatch_entered.set()
            await self.dispatch_gate.wait()
        receipt = await super().dispatch(context, command)
        if self.fail_capability == command.capability_id:
            source = copy.deepcopy(VECTORS["_shared_proto_failed"]["result"]["output"])
            source["command_id"] = command.command_id
            source["capability"] = command.capability_id
            result = GameCommandResult(command.command_id, CommandState.FAILED, source)
        else:
            vector = VECTORS[f"proto_{command.capability_id}_completed"]
            source = copy.deepcopy(vector["result"]["output"])
            source["command_id"] = command.command_id
            result = GameCommandResult(command.command_id, CommandState.SUCCEEDED, source)
        self.results[self._key(context, command.command_id)] = result
        return receipt


async def _stack(*, fail_capability=None, scopes=None, grants=None, dispatch_gate=None):
    raw_entries = build_proto_capability_entries(ROOT)
    entries = tuple(ProtocolCapabilityEntry(**entry) for entry in raw_entries)
    digest = compute_capability_snapshot_digest(entries)
    binding = RuntimeBinding(
        "binding-hosted", "runtime-hosted", 1, "user-hosted", "agentee-hosted",
        2, 1, BindingState.ACTIVE, "game-hosted", "nonce-hosted", digest,
        "resume-hosted", datetime.now(timezone.utc),
    )
    grants = grants if grants is not None else (
        "skill:stardew.proto.query_runtime_snapshot@1.0.0",
        "skill:stardew.proto.say@1.0.0",
        "skill:stardew.basic.observe_current_view@1.0.0",
    )
    material = AuthorityMaterial(
        "ephemeral", "starcoplay-runtime-manager", "starcoplay-mcp", binding.runtime_id,
        "jti-hosted", binding.runtime_id, binding.generation, binding.binding_id,
        binding.binding_version, binding.lease_epoch, binding.game_session_id,
        binding.game_instance_nonce, binding.capability_digest, binding.user_id,
        binding.agentee_id,
        tuple(("game:read", "game:write") if scopes is None else scopes),
        tuple(grants),
        datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    keys = KeyStore(generate_signing_key(kid="hosted-chain", lifetime_seconds=3600))
    credential = HostedCredentialSigner(keys).sign(material)
    authority = HostedClaimsAuthority(
        HostedCredentialVerifier(keys, RevocationStore()), _Bindings(binding),
    )
    contracts = build_capability_contract_catalog(ROOT)
    entry_provider = _Entries(entries)
    exposure = SessionExposurePort(
        authority, lambda _context: entries, contracts,
        lambda context: ExposureContext(
            "stardew-valley",
            default_mounted_skill_refs(ROOT / "platform/backend/modules/mcp/skills/builtin"),
            hosted_risk_policy(context), False, "hosted-mount-v1", "hosted-policy-v1",
            context.binding_ref, context.binding_version, context.game_session_ref,
        ),
    )
    router = _ResultRouter(RouteFence(
        binding.game_session_id, binding.game_instance_nonce, binding.generation,
        binding.binding_version, binding.lease_epoch, digest, binding.user_id,
        binding.runtime_id, binding.binding_id,
    ), fail_capability=fail_capability, dispatch_gate=dispatch_gate)
    audit = MemoryAuditSink()
    capability_runtime = RouterBackedCapabilityRuntime(
        router, entry_provider, contracts, lambda context: context.game_session_ref,
        audit=audit,
    )
    composition = build_mcp_composition(
        repo_root=ROOT, exposure_port=exposure,
        capability_runtime=capability_runtime, authority=authority,
        controlled_enabled=False, audit=audit,
    )
    core = McpCore(
        authority, router, ToolCatalog(()),
        adapt_results=True, projection=composition.projection,
    )
    return HostedMcpJsonRpcSession(core, authority, credential), router, audit


@pytest.mark.asyncio
async def test_hosted_top_level_tool_call_gate_rejects_before_real_product_chain(
    monkeypatch, caplog,
):
    metrics = []
    monkeypatch.setattr(
        hosted_server, "emit_metric",
        lambda *args, **kwargs: metrics.append((args, kwargs)),
    )
    gate = asyncio.Event()
    session, router, audit = await _stack(dispatch_gate=gate)
    try:
        assert "result" in await session.request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        assert "result" in await session.request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        first = asyncio.create_task(session.request({
            "jsonrpc": "2.0", "id": "first", "method": "tools/call",
            "params": {
                "name": "skill:stardew.proto.query_runtime_snapshot@1.0.0",
                "arguments": {},
                "_meta": {"toolCallId": "tool-first"},
            },
        }))
        await asyncio.wait_for(router.dispatch_entered.wait(), timeout=1)
        assert router.dispatch_count == 0
        audit_before = len(audit.events)

        with caplog.at_level("WARNING", logger="mcp.hosted.transport"):
            rejected = await session.request({
                "jsonrpc": "2.0", "id": "second", "method": "tools/call",
                "params": {
                    "name": "skill:stardew.proto.query_runtime_snapshot@1.0.0",
                    "arguments": {},
                    "_meta": {"toolCallId": "tool-second"},
                },
            })
        assert rejected["error"]["data"] == {
            "code": "resource_exhausted", "retryable": True,
            "message": "hosted MCP capacity is exhausted",
        }
        assert router.dispatch_count == 0
        assert len(audit.events) == audit_before
        rejections = [record for record in caplog.records
                      if getattr(record, "event", "") == "mcp.hosted.tool_call.rejected"]
        assert len(rejections) == 1
        assert rejections[0].limit_kind == "hosted_mcp_inflight_calls"
        assert len(metrics) == 1
        assert metrics[0][1]["limit_kind"] == "hosted_mcp_inflight_calls"

        # Non-ToolCall control methods stay live while the slot is occupied.
        listed = await session.request(
            {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}}
        )
        assert "result" in listed
        gate.set()
        assert (await first)["result"]["isError"] is False

        # Terminal completion releases the Hosted-only gate.
        after = await session.request({
            "jsonrpc": "2.0", "id": "after", "method": "tools/call",
            "params": {
                "name": "skill:stardew.proto.query_runtime_snapshot@1.0.0",
                "arguments": {},
                "_meta": {"toolCallId": "tool-after"},
            },
        })
        assert after["result"]["isError"] is False
    finally:
        gate.set()
        await session.close()


@pytest.mark.asyncio
async def test_hosted_credential_drives_dynamic_registry_runtime_router_and_correlated_audit():
    session, router, audit = await _stack()
    try:
        assert "result" in await session.request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        listed = await session.request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert "result" in listed, listed
        names = [tool["name"] for tool in listed["result"]["tools"]]
        assert names == sorted(names)
        assert set(names) == {
            "skill:stardew.proto.query_runtime_snapshot@1.0.0",
            "skill:stardew.proto.say@1.0.0",
            "skill:stardew.basic.observe_current_view@1.0.0",
        }

        readonly = await session.request({
            "jsonrpc": "2.0", "id": "request-read", "method": "tools/call",
            "params": {"name": names[1], "arguments": {},
                       "_meta": {"toolCallId": "tool-read"}},
        })
        assert readonly["result"]["isError"] is False

        action = await session.request({
            "jsonrpc": "2.0", "id": "request-action", "method": "tools/call",
            "params": {"name": "skill:stardew.proto.say@1.0.0",
                       "arguments": {"message": "hosted chain"},
                       "_meta": {"toolCallId": "tool-action"}},
        })
        assert action["result"]["isError"] is False

        composite = await session.request({
            "jsonrpc": "2.0", "id": "request-composite", "method": "tools/call",
            "params": {"name": "skill:stardew.basic.observe_current_view@1.0.0",
                       "arguments": {}, "_meta": {"toolCallId": "tool-composite"}},
        })
        assert composite["result"]["isError"] is False, composite
        correlated = [event for event in audit.events
                      if getattr(event, "tool_call_id", None) == "tool-composite"]
        assert {event.event for event in correlated} >= {
            "skill.execution.started", "skill.capability.started",
            "skill.capability.result", "skill.execution.finished",
        }
        skill_correlated = [event for event in correlated if hasattr(event, "request_id")]
        assert {event.request_id for event in skill_correlated} == {"request-composite"}
        command_correlated = [event for event in correlated
                              if getattr(event, "command_id", None)]
        assert command_correlated
        assert router.dispatch_count == 6  # read + action + four valid composite steps

        denied = await session.request({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "skill:stardew.proto.emote@1.0.0",
                       "arguments": {"emote_id": 1},
                       "_meta": {"toolCallId": "forged"}},
        })
        assert denied["error"]["data"]["code"] == "capability_denied"
        assert router.dispatch_count == 6
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_hosted_composite_failure_is_not_reported_as_success():
    session, router, _audit = await _stack(fail_capability="query_ui_snapshot")
    try:
        await session.request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        await session.request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        failed = await session.request({
            "jsonrpc": "2.0", "id": "request-failed", "method": "tools/call",
            "params": {"name": "skill:stardew.basic.observe_current_view@1.0.0",
                       "arguments": {}, "_meta": {"toolCallId": "tool-failed"}},
        })
        assert "error" in failed or failed["result"]["isError"] is True
        assert router.dispatch_count == 3
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_hosted_risk_skill_requires_write_scope_and_exact_tool_grant():
    navigate = "skill:stardew.basic.navigate_and_interact@1.0.0"
    arguments = {
        "destination": {
            "map_id": "Farm", "x": 1, "y": 1,
            "arrival_mode": "adjacent", "stand_side": "down",
        },
        "interaction": {"x": 1, "y": 1},
    }

    read_session, read_router, _ = await _stack(
        scopes=("game:read",), grants=(navigate,),
    )
    try:
        await read_session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        listed = await read_session.request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert navigate not in {tool["name"] for tool in listed["result"]["tools"]}
        denied = await read_session.request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": navigate, "arguments": arguments},
        })
        assert denied["error"]["data"]["code"] == "capability_denied"
        assert read_router.dispatch_count == 0
    finally:
        await read_session.close()

    write_session, write_router, _ = await _stack(
        scopes=("game:read", "game:write"), grants=(navigate,),
    )
    try:
        await write_session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        listed = await write_session.request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert {tool["name"] for tool in listed["result"]["tools"]} == {navigate}
        completed = await write_session.request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": navigate, "arguments": arguments},
        })
        assert completed["result"]["isError"] is False, completed
        assert write_router.dispatch_count == 3
    finally:
        await write_session.close()

    ungranted_session, ungranted_router, _ = await _stack(
        scopes=("game:read", "game:write"),
        grants=("skill:stardew.proto.query_runtime_snapshot@1.0.0",),
    )
    try:
        await ungranted_session.request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        listed = await ungranted_session.request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert navigate not in {tool["name"] for tool in listed["result"]["tools"]}
        denied = await ungranted_session.request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": navigate, "arguments": arguments},
        })
        assert denied["error"]["data"]["code"] == "capability_denied"
        assert ungranted_router.dispatch_count == 0
    finally:
        await ungranted_session.close()


def test_hosted_risk_policy_is_scope_derived_and_unknown_labels_default_deny():
    from modules.mcp.contracts.models import ExecutionContext

    def context(scopes):
        return ExecutionContext(
            "hosted", "principal", "runtime", 1, "binding", 1,
            "game", "nonce", 1, frozenset(scopes), frozenset(),
            "digest", 1.0,
        )

    assert hosted_risk_policy(context({"game:read"})) == {}
    policy = hosted_risk_policy(context({"game:read", "game:write"}))
    assert policy == {
        "advances_time": RiskDecision.ALLOW,
        "changes_save": RiskDecision.ALLOW,
        "consumes_energy": RiskDecision.ALLOW,
        "consumes_item": RiskDecision.ALLOW,
        "spends_money": RiskDecision.ALLOW,
    }
    assert policy.get("future_unknown_risk", RiskDecision.DENY) is RiskDecision.DENY
