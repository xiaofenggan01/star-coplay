from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.protocol.canonical_surface import canonical_specs
from modules.mcp.contracts.models import CommandState, GameCommandResult, SideEffect
from modules.mcp.core.catalog import ToolCatalog, ToolDefinition
from modules.mcp.core.service import McpCore
from modules.mcp.core.errors import McpError
from modules.mcp.fakes import FakeContextAuthority, FakeGameSessionRouter
from modules.mcp.fakes.router import RouteFence
from modules.mcp.manifest import build_manifest, build_tool_catalog
from tests.mcp.conftest import make_context


VECTORS = json.loads(
    (Path(__file__).resolve().parents[4]
     / "agent/tests/fixtures/canonical-result-vectors.json").read_text()
)["vectors"]

_SUCCESS_VECTORS = {
    vid: v for vid, v in VECTORS.items()
    if isinstance(v.get("expected"), dict) and v["result"]["state"] == "SUCCEEDED"
}


def test_product_catalog_34_tools_each_have_a_success_oracle_vector():
    """The 34 product tools — not the three legacy canonical aliases — are the M2 gate."""
    repo_root = Path(__file__).resolve().parents[4]
    catalog = build_tool_catalog(build_manifest(repo_root))
    product_names = {
        tool.name for tool in catalog.list_for(frozenset(canonical_specs()))
    }
    covered = {
        vector["canonical_name"] for vector in _SUCCESS_VECTORS.values()
        if vector["result"]["state"] == "SUCCEEDED"
        and vector["expected"].get("success") is True
    }
    assert len(product_names) == 34
    assert product_names <= covered


def _core(name: str, *, tools: frozenset[str] | None = None):
    spec = canonical_specs()[name]
    scope = "game:read" if spec.execution_model == "request_result" and "query" in name else "game:write"
    # read_only capabilities based on side_effect
    read_only = name in {
        "query_runtime_snapshot", "observe_current_view",
        "query_world_region", "query_inventory_snapshot", "query_ui_snapshot",
        "query_inspect_refs", "query_menu", "query_inventory", "query_location",
        "query_map_exits",
    }
    scope = "game:read" if read_only else "game:write"
    context = make_context(tools=tools or {name})
    authority = FakeContextAuthority({"credential": context})
    router = FakeGameSessionRouter(RouteFence("game-a", "nonce-a", 1, 1, 1, "digest-a"))
    catalog = ToolCatalog([ToolDefinition(
        name, name, "canonical.v1", scope,
        SideEffect.READ_ONLY if scope == "game:read" else SideEffect.MUTATING,
        spec.input_schema, spec.output_schema, timeout_seconds=spec.timeout_ms / 1000,
    )])
    return context, authority, router, McpCore(
        authority, router, catalog, adapt_results=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("vector_id", sorted(_SUCCESS_VECTORS))
async def test_every_capability_normalizes_through_core(vector_id):
    """Every canonical capability with adapter=True must produce normalized output."""
    vector = _SUCCESS_VECTORS[vector_id]
    source = vector["result"]
    name = vector["canonical_name"]
    context, authority, router, core = _core(name)
    router.results[(context.game_session_ref, context.game_instance_nonce, source["command_id"])] = (
        GameCommandResult(
            source["command_id"], CommandState(source["state"]),
            source["output"], source.get("error_code"),
        )
    )
    handle = await authority.establish({"credential": "credential"})
    result = await core.call_tool(
        handle, name, vector["arguments"],
        tool_call_id=f"tool-{source['command_id']}", command_id=source["command_id"],
    )
    assert result.output == vector["expected"], (
        f"{vector_id}: core-normalized output does not match frozen expected"
    )


@pytest.mark.asyncio
async def test_core_does_not_fabricate_terminal_output_for_unknown():
    vector = VECTORS["_unknown_is_not_terminal_output"]
    source = vector["result"]
    context, authority, router, core = _core(vector["canonical_name"])
    router.results[(context.game_session_ref, context.game_instance_nonce, source["command_id"])] = (
        GameCommandResult(
            source["command_id"], CommandState.UNKNOWN, {}, source["error_code"],
        )
    )
    handle = await authority.establish({"credential": "credential"})
    result = await core.call_tool(
        handle, vector["canonical_name"], vector["arguments"],
        tool_call_id="tool-unknown", command_id=source["command_id"],
    )
    assert result.state is CommandState.UNKNOWN
    assert result.output == {}
    assert result.error_code == "route_unavailable"


@pytest.mark.asyncio
async def test_core_rejects_source_result_that_fails_canonical_schema():
    vector = VECTORS["v2_observe_current_view_completed"]
    source = vector["result"]
    context, authority, router, core = _core(vector["canonical_name"])
    malformed = {
        **source["output"],
        "data": {**source["output"]["data"], "timestamp": "not-an-integer"},
    }
    router.results[(context.game_session_ref, context.game_instance_nonce, source["command_id"])] = (
        GameCommandResult(source["command_id"], CommandState.SUCCEEDED, malformed)
    )
    handle = await authority.establish({"credential": "credential"})
    with pytest.raises(McpError) as caught:
        await core.call_tool(
            handle, vector["canonical_name"], vector["arguments"],
            tool_call_id="tool-malformed", command_id=source["command_id"],
        )
    assert caught.value.code == "upstream_protocol_error"


@pytest.mark.asyncio
async def test_all_capabilities_are_adapted_when_flag_is_true():
    """When adapt_results=True, every tool result goes through the adapter — no raw passthrough."""
    name = "water_crops"
    context, authority, router, core = _core(name)
    raw = {
        "action_id": "cmd-water", "protocol_version": "2.0",
        "success": True, "status": "ok", "data": {"watered": 1},
    }
    router.results[(context.game_session_ref, context.game_instance_nonce, "cmd-water")] = (
        GameCommandResult("cmd-water", CommandState.SUCCEEDED, raw)
    )
    handle = await authority.establish({"credential": "credential"})
    result = await core.call_tool(
        handle, name, {"max_count": 1},
        tool_call_id="tool-water", command_id="cmd-water",
    )
    # Adapted — no longer raw passthrough.
    assert result.output == {
        "command_id": "cmd-water",
        "capability": "water_crops",
        "success": True,
        "status": "COMPLETED",
        "data": {"watered": 1},
    }


@pytest.mark.asyncio
async def test_core_rejects_proto_command_id_mismatch():
    vector = VECTORS["_proto_command_id_mismatch"]
    source = vector["result"]
    context, authority, router, core = _core(vector["canonical_name"])
    router.results[(context.game_session_ref, context.game_instance_nonce, source["command_id"])] = (
        GameCommandResult(
            source["command_id"], CommandState(source["state"]),
            source["output"], source.get("error_code"),
        )
    )
    handle = await authority.establish({"credential": "credential"})
    with pytest.raises(McpError) as caught:
        await core.call_tool(
            handle, vector["canonical_name"], vector["arguments"],
            tool_call_id="tool-mismatch", command_id=source["command_id"],
        )
    assert caught.value.code == "upstream_protocol_error"
