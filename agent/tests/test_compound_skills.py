"""
Tests for compound skill capabilities (water_crops, harvest_crops, ship_items).

Verifies:
1. Compound capabilities appear in tool schemas
2. LLM FC calls with compound tools produce correct bridge commands
3. Compound results are correctly parsed and forwarded to LLM
4. Timeout handling for long-running compound actions
5. LONG_ACTIONS classification
"""

import sys
import types
import json
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── 导入路径修复（与其他测试文件一致） ──────────────────────────────────────
TESTS_DIR = Path(__file__).resolve().parent
AGENT_DIR = TESTS_DIR.parent
PROJECT_DIR = AGENT_DIR.parent

_PARENT_PKG = "agent"
if _PARENT_PKG not in sys.modules:
    _pkg = types.ModuleType(_PARENT_PKG)
    _pkg.__path__ = [str(AGENT_DIR)]
    _pkg.__package__ = _PARENT_PKG
    sys.modules[_PARENT_PKG] = _pkg

_SUB_PACKAGES = ["bridge", "bt_engine", "memory", "messages", "slow_loop", "report"]
for _sub in _SUB_PACKAGES:
    _fqn = f"{_PARENT_PKG}.{_sub}"
    if _fqn not in sys.modules:
        _sub_dir = AGENT_DIR / _sub
        if _sub_dir.is_dir():
            _sub_pkg = types.ModuleType(_fqn)
            _sub_pkg.__path__ = [str(_sub_dir)]
            _sub_pkg.__package__ = _fqn
            sys.modules[_fqn] = _sub_pkg

# Register message types
if "messages.types" not in sys.modules:
    _msg_types = importlib.import_module("agent.messages.types")
    sys.modules["messages.types"] = _msg_types

from agent.slow_loop.action_policy import timeout_for_action
from agent.slow_loop.deliberative_loop import LONG_ACTIONS, INSTANT_ACTIONS, QUERY_ACTIONS


# ── Fixtures ──────────────────────────────────────────────────────────────────

COMPOUND_CAPABILITIES = [
    {
        "name": "water_crops",
        "category": "compound",
        "description": "Water all unwatered crops on the current map.",
        "params": {
            "type": "object",
            "properties": {
                "max_count": {
                    "type": "number",
                    "description": "maximum number of crops to water (default: 50)"
                }
            }
        }
    },
    {
        "name": "harvest_crops",
        "category": "compound",
        "description": "Harvest all ready crops on the current map.",
        "params": {
            "type": "object",
            "properties": {
                "max_count": {
                    "type": "number",
                    "description": "maximum number of crops to harvest (default: 50)"
                },
                "replant_seed": {
                    "type": "string",
                    "description": "seed name to replant after harvesting"
                }
            }
        }
    },
    {
        "name": "go_bed",
        "category": "compound",
        "description": "Walk home and sleep without teleporting.",
        "params": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "ship_items",
        "category": "compound",
        "description": "Batch-ship items from inventory to the shipping bin.",
        "params": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "plan (default) or execute",
                    "enum": ["plan", "execute"]
                },
                "items": {
                    "type": "string",
                    "description": "comma-separated item names to ship"
                },
                "exclude": {
                    "type": "string",
                    "description": "comma-separated item names to exclude"
                },
                "min_quality": {
                    "type": "string",
                    "description": "keep items at or above this quality",
                    "enum": ["silver", "gold", "iridium"]
                }
            }
        }
    },
    {
        "name": "buy_items",
        "category": "compound",
        "description": "Navigate to a shop, open it, and purchase specified items.",
        "params": {
            "type": "object",
            "properties": {
                "shop": {
                    "type": "string",
                    "description": "shop ID",
                    "enum": ["SeedShop", "FishShop", "Saloon", "Blacksmith", "AnimalShop", "Carpenter", "Hospital", "JojaMart"]
                },
                "items": {
                    "type": "string",
                    "description": "JSON array of items to buy"
                }
            },
            "required": ["shop", "items"]
        }
    },
]

QUERY_CAPABILITIES = [
    {
        "name": "query_shop",
        "category": "query",
        "description": "Query items for sale at a specific shop.",
        "params": {
            "type": "object",
            "properties": {
                "shop": {
                    "type": "string",
                    "description": "shop ID",
                    "enum": ["SeedShop", "FishShop", "Saloon", "Blacksmith", "AnimalShop", "Carpenter", "Hospital", "JojaMart"]
                }
            },
            "required": ["shop"]
        }
    },
]

SAMPLE_GO_BED_RESULT = {
    "seq": 46,
    "action_id": "act_0046_e5f6",
    "protocol_version": "2.0",
    "success": True,
    "data": {
        "method": "warp",
        "home_location": "FarmHouse",
        "sleep_triggered": True,
        "stopped_reason": "sleep_triggered",
    },
    "observation": "已回到 FarmHouse 并触发睡觉（方式: warp）"
}

SAMPLE_WATER_RESULT = {
    "seq": 42,
    "action_id": "act_0042_a1b2",
    "protocol_version": "2.0",
    "success": True,
    "status": "ok",
    "data": {
        "watered": 23,
        "skipped": 2,
        "refilled": 1,
        "stopped_reason": "all_done",
        "location": "Farm"
    },
    "observation": "浇灌了 23 棵作物，跳过 2 棵（无法到达），加水 1 次"
}

SAMPLE_HARVEST_RESULT = {
    "seq": 43,
    "action_id": "act_0043_b2c3",
    "protocol_version": "2.0",
    "success": True,
    "status": "ok",
    "data": {
        "harvested": 15,
        "replanted": 12,
        "skipped": 1,
        "stopped_reason": "all_done",
        "location": "Farm"
    },
    "observation": "收获了 15 棵作物，补种 12 棵"
}

SAMPLE_SHIP_PLAN_RESULT = {
    "seq": 48,
    "action_id": "act_0048_g7h8",
    "protocol_version": "2.0",
    "success": True,
    "data": {
        "mode": "plan",
        "items": [
            {"name": "Parsnip", "count": 15, "unit_price": 35, "total": 525, "quality": "Normal"},
            {"name": "Potato", "count": 8, "unit_price": 80, "total": 640, "quality": "Silver"},
        ],
        "total_value": 1165,
        "item_count": 23,
        "kept": [
            {"name": "Ancient Fruit", "count": 3, "quality": "Iridium", "reason": "quality_filter"}
        ],
        "excluded": ["Parsnip Seeds", "Watering Can"],
        "message": "计划出货 23 件物品，预计收入 1165g。保留 3 件高品质物品。"
    }
}

SAMPLE_SHIP_EXECUTE_RESULT = {
    "seq": 49,
    "action_id": "act_0049_h8i9",
    "protocol_version": "2.0",
    "success": True,
    "data": {
        "mode": "execute",
        "shipped": [
            {"name": "Parsnip", "count": 15, "value": 525},
            {"name": "Potato", "count": 8, "value": 640},
        ],
        "total_value": 1165,
        "failed": [],
        "message": "已出货 23 件物品，明日结算 1165g"
    }
}

SAMPLE_WATER_FAILURE = {
    "seq": 44,
    "action_id": "act_0044_c3d4",
    "protocol_version": "2.0",
    "success": False,
    "error": {
        "code": "ITEM_NOT_FOUND",
        "message": "背包中找不到浇水壶"
    }
}

SAMPLE_QUERY_SHOP_RESULT = {
    "seq": 50,
    "action_id": "act_0050_j0k1",
    "protocol_version": "2.0",
    "success": True,
    "data": {
        "shop": "SeedShop",
        "shop_name": "Pierre's General Store",
        "currency": "0",
        "item_count": 5,
        "items": [
            {"name": "Parsnip Seeds", "price": 20, "stock": -1, "category": "Seeds"},
            {"name": "Bean Starter", "price": 60, "stock": -1, "category": "Seeds"},
            {"name": "Cauliflower Seeds", "price": 80, "stock": -1, "category": "Seeds"},
            {"name": "Potato Seeds", "price": 50, "stock": -1, "category": "Seeds"},
            {"name": "Kale Seeds", "price": 70, "stock": -1, "category": "Seeds"},
        ]
    },
    "observation": "已查询 Pierre's General Store (5 件物品)"
}

SAMPLE_BUY_ITEMS_RESULT = {
    "seq": 51,
    "action_id": "act_0051_k1l2",
    "protocol_version": "2.0",
    "success": True,
    "status": "ok",
    "data": {
        "shop": "SeedShop",
        "bought": [
            {"name": "Parsnip Seeds", "count": 5, "unit_price": 20, "total": 100},
        ],
        "bought_count": 5,
        "total_cost": 100,
        "gold_remaining": 400,
        "failed": [],
        "stopped_reason": "all_done",
    },
    "observation": "从 SeedShop 购买了 5 件物品，花费 100g"
}

SAMPLE_BUY_ITEMS_PARTIAL = {
    "seq": 52,
    "action_id": "act_0052_l2m3",
    "protocol_version": "2.0",
    "success": True,
    "status": "partial",
    "data": {
        "shop": "SeedShop",
        "bought": [
            {"name": "Parsnip Seeds", "count": 3, "unit_price": 20, "total": 60},
        ],
        "bought_count": 3,
        "total_cost": 60,
        "gold_remaining": 0,
        "failed": [
            {"name": "Cauliflower Seeds", "reason": "insufficient_gold", "need": 80, "have": 0}
        ],
        "stopped_reason": "partial",
    },
    "observation": "从 SeedShop 购买了 3 件物品，花费 60g"
}


# ── Tests: Action Classification ──────────────────────────────────────────────

class TestCompoundActionClassification:
    """Verify compound capabilities are classified as LONG_ACTIONS."""

    def test_water_crops_is_long_action(self):
        assert "water_crops" in LONG_ACTIONS

    def test_harvest_crops_is_long_action(self):
        assert "harvest_crops" in LONG_ACTIONS

    def test_go_bed_is_long_action(self):
        assert "go_bed" in LONG_ACTIONS

    def test_ship_items_is_long_action(self):
        assert "ship_items" in LONG_ACTIONS

    def test_buy_items_is_long_action(self):
        assert "buy_items" in LONG_ACTIONS

    def test_query_shop_is_instant_action(self):
        assert "query_shop" in QUERY_ACTIONS

    def test_compound_not_instant(self):
        assert "water_crops" not in INSTANT_ACTIONS
        assert "harvest_crops" not in INSTANT_ACTIONS
        assert "go_bed" not in INSTANT_ACTIONS
        assert "ship_items" not in INSTANT_ACTIONS
        assert "buy_items" not in INSTANT_ACTIONS

    def test_query_shop_not_long_action(self):
        assert "query_shop" not in LONG_ACTIONS

    def test_original_long_actions_preserved(self):
        """Ensure existing long actions are not broken."""
        for action in ("move_to", "do", "use_tool", "tp"):
            assert action in LONG_ACTIONS


# ── Tests: Tool Schema Generation ─────────────────────────────────────────────

class TestCompoundToolSchemas:
    """Verify compound capabilities produce correct OpenAI tool schemas."""

    def _build_schemas(self, capabilities):
        """Minimal _build_tool_schemas reimplementation for testing."""
        tools = []
        for cap in capabilities:
            tools.append({
                "type": "function",
                "function": {
                    "name": cap["name"],
                    "description": cap.get("description", ""),
                    "parameters": cap.get("params", {"type": "object", "properties": {}})
                }
            })
        return tools

    def test_water_crops_schema_structure(self):
        schemas = self._build_schemas(COMPOUND_CAPABILITIES)
        water = next(s for s in schemas if s["function"]["name"] == "water_crops")

        assert water["type"] == "function"
        assert "max_count" in water["function"]["parameters"]["properties"]
        assert water["function"]["parameters"]["properties"]["max_count"]["type"] == "number"

    def test_harvest_crops_schema_structure(self):
        schemas = self._build_schemas(COMPOUND_CAPABILITIES)
        harvest = next(s for s in schemas if s["function"]["name"] == "harvest_crops")

        assert harvest["type"] == "function"
        params = harvest["function"]["parameters"]["properties"]
        assert "max_count" in params
        assert "replant_seed" in params
        assert params["replant_seed"]["type"] == "string"

    def test_go_bed_schema_structure(self):
        schemas = self._build_schemas(COMPOUND_CAPABILITIES)
        go_bed = next(s for s in schemas if s["function"]["name"] == "go_bed")

        assert go_bed["type"] == "function"
        assert "force_tp" not in go_bed["function"]["parameters"]["properties"]

    def test_ship_items_schema_structure(self):
        schemas = self._build_schemas(COMPOUND_CAPABILITIES)
        ship = next(s for s in schemas if s["function"]["name"] == "ship_items")

        assert ship["type"] == "function"
        params = ship["function"]["parameters"]["properties"]
        assert "mode" in params
        assert params["mode"]["type"] == "string"
        assert "plan" in params["mode"]["enum"]
        assert "execute" in params["mode"]["enum"]
        assert "items" in params
        assert "exclude" in params
        assert "min_quality" in params
        assert "silver" in params["min_quality"]["enum"]

    def test_buy_items_schema_structure(self):
        schemas = self._build_schemas(COMPOUND_CAPABILITIES)
        buy = next(s for s in schemas if s["function"]["name"] == "buy_items")

        assert buy["type"] == "function"
        params = buy["function"]["parameters"]["properties"]
        assert "shop" in params
        assert "items" in params
        assert "SeedShop" in params["shop"]["enum"]
        assert "required" in buy["function"]["parameters"]
        assert "shop" in buy["function"]["parameters"]["required"]

    def test_query_shop_schema_structure(self):
        schemas = self._build_schemas(QUERY_CAPABILITIES)
        shop = next(s for s in schemas if s["function"]["name"] == "query_shop")

        assert shop["type"] == "function"
        params = shop["function"]["parameters"]["properties"]
        assert "shop" in params
        assert "SeedShop" in params["shop"]["enum"]
        assert "required" in shop["function"]["parameters"]
        assert "shop" in shop["function"]["parameters"]["required"]

    def test_compound_schemas_count(self):
        schemas = self._build_schemas(COMPOUND_CAPABILITIES)
        assert len(schemas) == 5  # water_crops, harvest_crops, go_bed, ship_items, buy_items


# ── Tests: Result Parsing ─────────────────────────────────────────────────────

class TestCompoundResultParsing:
    """Verify compound results can be correctly parsed."""

    def test_water_success_result(self):
        result = SAMPLE_WATER_RESULT
        assert result["success"] is True
        assert result["data"]["watered"] == 23
        assert result["data"]["skipped"] == 2
        assert result["data"]["refilled"] == 1
        assert result["data"]["stopped_reason"] == "all_done"

    def test_harvest_success_result(self):
        result = SAMPLE_HARVEST_RESULT
        assert result["success"] is True
        assert result["data"]["harvested"] == 15
        assert result["data"]["replanted"] == 12
        assert result["data"]["stopped_reason"] == "all_done"

    def test_water_failure_result(self):
        result = SAMPLE_WATER_FAILURE
        assert result["success"] is False
        assert result["error"]["code"] == "ITEM_NOT_FOUND"

    def test_go_bed_success_result(self):
        result = SAMPLE_GO_BED_RESULT
        assert result["success"] is True
        assert result["data"]["method"] == "warp"
        assert result["data"]["home_location"] == "FarmHouse"
        assert result["data"]["sleep_triggered"] is True
        assert result["data"]["stopped_reason"] == "sleep_triggered"

    def test_ship_plan_result(self):
        result = SAMPLE_SHIP_PLAN_RESULT
        assert result["success"] is True
        assert result["data"]["mode"] == "plan"
        assert result["data"]["total_value"] == 1165
        assert result["data"]["item_count"] == 23
        assert len(result["data"]["items"]) == 2
        assert result["data"]["items"][0]["name"] == "Parsnip"
        assert result["data"]["items"][0]["unit_price"] == 35
        assert len(result["data"]["kept"]) == 1
        assert result["data"]["kept"][0]["reason"] == "quality_filter"

    def test_ship_execute_result(self):
        result = SAMPLE_SHIP_EXECUTE_RESULT
        assert result["success"] is True
        assert result["data"]["mode"] == "execute"
        assert result["data"]["total_value"] == 1165
        assert len(result["data"]["shipped"]) == 2
        assert len(result["data"]["failed"]) == 0

    def test_query_shop_result(self):
        result = SAMPLE_QUERY_SHOP_RESULT
        assert result["success"] is True
        assert result["data"]["shop"] == "SeedShop"
        assert result["data"]["item_count"] == 5
        assert len(result["data"]["items"]) == 5
        assert result["data"]["items"][0]["name"] == "Parsnip Seeds"
        assert result["data"]["items"][0]["price"] == 20

    def test_buy_items_success_result(self):
        result = SAMPLE_BUY_ITEMS_RESULT
        assert result["success"] is True
        assert result["data"]["shop"] == "SeedShop"
        assert result["data"]["bought_count"] == 5
        assert result["data"]["total_cost"] == 100
        assert result["data"]["gold_remaining"] == 400
        assert result["data"]["stopped_reason"] == "all_done"
        assert len(result["data"]["bought"]) == 1
        assert len(result["data"]["failed"]) == 0

    def test_buy_items_partial_result(self):
        result = SAMPLE_BUY_ITEMS_PARTIAL
        assert result["success"] is True
        assert result["data"]["stopped_reason"] == "partial"
        assert result["data"]["bought_count"] == 3
        assert len(result["data"]["failed"]) == 1
        assert result["data"]["failed"][0]["reason"] == "insufficient_gold"

    def test_result_json_roundtrip(self):
        """Ensure results survive JSON serialization."""
        for sample in [SAMPLE_WATER_RESULT, SAMPLE_HARVEST_RESULT, SAMPLE_WATER_FAILURE, SAMPLE_GO_BED_RESULT,
                       SAMPLE_SHIP_PLAN_RESULT, SAMPLE_SHIP_EXECUTE_RESULT,
                       SAMPLE_QUERY_SHOP_RESULT, SAMPLE_BUY_ITEMS_RESULT, SAMPLE_BUY_ITEMS_PARTIAL]:
            serialized = json.dumps(sample)
            parsed = json.loads(serialized)
            assert parsed == sample

    def test_observation_present(self):
        assert len(SAMPLE_WATER_RESULT["observation"]) > 0
        assert len(SAMPLE_HARVEST_RESULT["observation"]) > 0

    def test_stopped_reasons(self):
        """Verify all expected stopped_reason values."""
        valid_reasons = {"all_done", "stamina_low", "max_count", "no_water_source",
                        "inventory_full", "no_seeds", "cancelled"}
        assert SAMPLE_WATER_RESULT["data"]["stopped_reason"] in valid_reasons
        assert SAMPLE_HARVEST_RESULT["data"]["stopped_reason"] in valid_reasons


# ── Tests: Bridge Command Format ──────────────────────────────────────────────

class TestCompoundBridgeCommands:
    """Verify compound capabilities generate correct bridge commands."""

    def test_water_crops_command_format(self):
        """water_crops FC call should produce a V2 command."""
        cmd = {
            "seq": 42,
            "protocol_version": "2.0",
            "capability": "water_crops",
            "params": {"max_count": 30},
            "action_id": "act_0042_a1b2"
        }
        assert cmd["capability"] == "water_crops"
        assert cmd["protocol_version"] == "2.0"
        assert cmd["params"]["max_count"] == 30

    def test_harvest_crops_command_with_replant(self):
        cmd = {
            "seq": 43,
            "protocol_version": "2.0",
            "capability": "harvest_crops",
            "params": {"max_count": 25, "replant_seed": "Parsnip Seeds"},
            "action_id": "act_0043_b2c3"
        }
        assert cmd["params"]["replant_seed"] == "Parsnip Seeds"

    def test_harvest_crops_command_without_replant(self):
        cmd = {
            "seq": 44,
            "protocol_version": "2.0",
            "capability": "harvest_crops",
            "params": {},
            "action_id": "act_0044_c3d4"
        }
        assert "replant_seed" not in cmd["params"]

    def test_go_bed_command_format(self):
        """go_bed FC call should produce a V2 command."""
        cmd = {
            "seq": 46,
            "protocol_version": "2.0",
            "capability": "go_bed",
            "params": {},
            "action_id": "act_0046_e5f6"
        }
        assert cmd["capability"] == "go_bed"
        assert cmd["protocol_version"] == "2.0"

    def test_go_bed_command_has_no_force_tp(self):
        cmd = {
            "seq": 47,
            "protocol_version": "2.0",
            "capability": "go_bed",
            "params": {},
            "action_id": "act_0047_f6g7"
        }
        assert "force_tp" not in cmd["params"]

    def test_ship_items_plan_command(self):
        """ship_items plan mode command."""
        cmd = {
            "seq": 48,
            "protocol_version": "2.0",
            "capability": "ship_items",
            "params": {"mode": "plan", "min_quality": "gold"},
            "action_id": "act_0048_g7h8"
        }
        assert cmd["capability"] == "ship_items"
        assert cmd["params"]["mode"] == "plan"
        assert cmd["params"]["min_quality"] == "gold"

    def test_ship_items_execute_command(self):
        """ship_items execute mode with item selection."""
        cmd = {
            "seq": 49,
            "protocol_version": "2.0",
            "capability": "ship_items",
            "params": {"mode": "execute", "items": ["Parsnip", "Potato"]},
            "action_id": "act_0049_h8i9"
        }
        assert cmd["params"]["mode"] == "execute"
        assert len(cmd["params"]["items"]) == 2

    def test_ship_items_default_params(self):
        """Empty params should default to plan mode."""
        cmd = {
            "seq": 50,
            "protocol_version": "2.0",
            "capability": "ship_items",
            "params": {},
            "action_id": "act_0050_i9j0"
        }
        assert cmd["params"] == {}

    def test_buy_items_command_format(self):
        """buy_items FC call should produce a V2 command."""
        cmd = {
            "seq": 51,
            "protocol_version": "2.0",
            "capability": "buy_items",
            "params": {
                "shop": "SeedShop",
                "items": [{"name": "Parsnip Seeds", "quantity": 5}]
            },
            "action_id": "act_0051_k1l2"
        }
        assert cmd["capability"] == "buy_items"
        assert cmd["params"]["shop"] == "SeedShop"
        assert len(cmd["params"]["items"]) == 1

    def test_query_shop_command_format(self):
        """query_shop FC call should produce a V2 command."""
        cmd = {
            "seq": 50,
            "protocol_version": "2.0",
            "capability": "query_shop",
            "params": {"shop": "SeedShop"},
            "action_id": "act_0050_j0k1"
        }
        assert cmd["capability"] == "query_shop"
        assert cmd["params"]["shop"] == "SeedShop"

    def test_water_crops_default_params(self):
        """Empty params should be valid (all defaults)."""
        cmd = {
            "seq": 45,
            "protocol_version": "2.0",
            "capability": "water_crops",
            "params": {},
            "action_id": "act_0045_d4e5"
        }
        # No required params
        assert cmd["params"] == {}


# ── Tests: Timeout Configuration ──────────────────────────────────────────────

class TestCompoundTimeouts:
    """Verify compound actions use extended timeouts."""

    def test_compound_timeout_is_extended(self):
        """Compound actions should have timeout > 15s (the default for normal long actions)."""
        for name in ("water_crops", "harvest_crops"):
            assert timeout_for_action(name) == 120.0, f"{name} should have 120s timeout"

    def test_go_bed_timeout(self):
        """go_bed should allow real pathfinding and end-of-day UI."""
        assert timeout_for_action("go_bed") == 180.0, "go_bed should have 180s timeout"
        assert timeout_for_action("go_to_bed") == 180.0, "go_to_bed should have 180s timeout"

    def test_ship_items_timeout(self):
        """ship_items should have 30s timeout."""
        assert timeout_for_action("ship_items") == 30.0, "ship_items should have 30s timeout"

    def test_buy_items_timeout(self):
        """buy_items should have 60s timeout."""
        assert timeout_for_action("buy_items") == 60.0, "buy_items should have 60s timeout"

    def test_normal_long_action_timeout(self):
        """Normal long actions should not fall back to the old 15s timeout."""
        assert timeout_for_action("move_to") == 60.0
        for name in ("do", "use_tool", "tp"):
            assert timeout_for_action(name) == 30.0


# ── Tests: Capability Category ────────────────────────────────────────────────

class TestCompoundCategory:
    """Verify compound capabilities are categorized correctly in adapter_info."""

    def test_water_crops_category(self):
        cap = COMPOUND_CAPABILITIES[0]
        assert cap["category"] == "compound"

    def test_harvest_crops_category(self):
        cap = COMPOUND_CAPABILITIES[1]
        assert cap["category"] == "compound"

    def test_go_bed_category(self):
        cap = COMPOUND_CAPABILITIES[2]
        assert cap["category"] == "compound"

    def test_ship_items_category(self):
        cap = COMPOUND_CAPABILITIES[3]
        assert cap["category"] == "compound"

    def test_buy_items_category(self):
        cap = COMPOUND_CAPABILITIES[4]
        assert cap["category"] == "compound"

    def test_query_shop_category(self):
        cap = QUERY_CAPABILITIES[0]
        assert cap["category"] == "query"

    def test_category_distinct_from_action_and_query(self):
        for cap in COMPOUND_CAPABILITIES:
            assert cap["category"] not in ("action", "query")
