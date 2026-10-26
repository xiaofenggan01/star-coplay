from __future__ import annotations

import pytest

from l3_query.inspect import inspect_refs
from slow_loop.core_tools import get_local_tool_names, get_local_tool_schemas
from slow_loop.deliberative_loop import L3_BACKED_CORE_TOOLS
from v2_skills.builtin.inspect.script import inspect
from v2_skills.registry import SKILLS_EXCLUDED_FROM_FC


class InspectCtx:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def query(self, capability: str, params: dict | None = None) -> dict:
        params = params or {}
        self.calls.append((capability, params))
        assert capability == "query_inspect_refs"
        return {
            "protocol_data": {
                "inspected": [
                    {
                        "ref": params["refs"][0],
                        "type": "tree",
                        "fact_kind": "tree",
                        "summary": "Oak Tree",
                        "resolution": {"status": "REF_RESOLVED"},
                        "world_entity": {
                            "type": "tree",
                            "tree": {"has_moss": True},
                        },
                    }
                ],
                "warnings": [],
            }
        }


class EmptyInspectCtx(InspectCtx):
    async def query(self, capability: str, params: dict | None = None) -> dict:
        params = params or {}
        self.calls.append((capability, params))
        return {"protocol_data": {"inspected": [], "warnings": []}}


class FailedInspectCtx(InspectCtx):
    async def query(self, capability: str, params: dict | None = None) -> dict:
        params = params or {}
        self.calls.append((capability, params))
        return {
            "raw_protocol_result": {
                "success": False,
                "status": "failed",
                "error": {"code": "REF_STALE", "message": "ref is stale"},
            }
        }


class IndoorInspectCtx(InspectCtx):
    async def query(self, capability: str, params: dict | None = None) -> dict:
        params = params or {}
        self.calls.append((capability, params))
        return {
            "protocol_data": {
                "inspected": [
                    {
                        "ref": params["refs"][0],
                        "type": "bed",
                        "fact_kind": "bed",
                        "resolution": {"status": "REF_RESOLVED"},
                        "world_entity": {
                            "type": "bed",
                            "fact_kind": "bed",
                            "position": {"x": 9, "y": 8},
                            "summary": "Bed(Single) at (9,8)",
                            "bed": {
                                "display_name": "床",
                                "can_sleep": True,
                                "bed_spot": {"x": 10, "y": 9},
                            },
                        },
                    },
                    {
                        "ref": params["refs"][1],
                        "type": "warp",
                        "fact_kind": "warp",
                        "resolution": {"status": "REF_RESOLVED"},
                        "world_entity": {
                            "type": "warp",
                            "fact_kind": "warp",
                            "position": {"x": 3, "y": 12},
                            "summary": "Warp → Farm (56,16)",
                            "warp": {
                                "target_map": "Farm",
                                "target_x": 56,
                                "target_y": 16,
                            },
                        },
                    },
                ],
                "warnings": [],
            }
        }


@pytest.mark.asyncio
async def test_inspect_refs_delegates_to_query_inspect_refs_without_parsing_ref():
    ctx = InspectCtx()
    ref = "world_entity:tree:farm:41:12#wt_1"

    result = await inspect_refs(ctx, [ref])

    assert ctx.calls == [("query_inspect_refs", {"refs": [ref], "include_debug": False})]
    assert result["data"]["inspected"][0]["ref"] == ref
    assert "1 个成功解析" in result["description"]
    assert result["success"] is True


@pytest.mark.asyncio
async def test_inspect_refs_preserves_world_entity_payload_for_llm_details():
    bed_ref = "world_entity:bed:farmhouse:9:8#f_2048"
    warp_ref = "world_entity:warp:farmhouse:3:12#farm"

    result = await inspect_refs(IndoorInspectCtx(), [bed_ref, warp_ref])

    assert result["success"] is True
    bed, warp = result["data"]["items"]
    assert bed["label"] == "床"
    assert bed["summary"] == "Bed(Single) at (9,8)"
    assert bed["fact"]["bed_spot"] == {"x": 10, "y": 9}
    assert warp["label"] == "出口到 Farm"
    assert warp["summary"] == "Warp → Farm (56,16)"
    assert warp["fact"]["target_map"] == "Farm"


@pytest.mark.asyncio
async def test_inspect_skill_accepts_single_ref_id():
    ctx = InspectCtx()
    ref = "world_entity:tree:farm:41:12#wt_1"

    result = await inspect(ctx, ref_id=ref)

    assert result["success"] is True
    assert result["data"]["inspected"][0]["ref"] == ref


@pytest.mark.asyncio
async def test_inspect_skill_reports_empty_inspect_as_failed():
    ref = "world_entity:tree:farm:41:12#wt_1"

    result = await inspect(EmptyInspectCtx(), ref_id=ref)

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"]["code"] == "INSPECT_EMPTY"
    assert result["data"]["requested_refs"] == [ref]


@pytest.mark.asyncio
async def test_inspect_skill_propagates_protocol_failure():
    ref = "world_entity:tree:farm:41:12#wt_1"

    result = await inspect(FailedInspectCtx(), ref_id=ref)

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"]["code"] == "REF_STALE"


@pytest.mark.asyncio
async def test_inspect_skill_rejects_missing_ref():
    result = await inspect(InspectCtx())

    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_PARAMS"


def test_inspect_is_llm_visible_local_tool_and_l3_backed():
    assert "inspect" in get_local_tool_names()
    schemas = get_local_tool_schemas()
    inspect_schema = next(schema for schema in schemas if schema["function"]["name"] == "inspect")
    assert "refs" in inspect_schema["function"]["parameters"]["properties"]
    assert L3_BACKED_CORE_TOOLS["inspect"] == "inspect"
    assert "inspect" in SKILLS_EXCLUDED_FROM_FC
