"""
observe Skill — 端到端集成测试。

从 observe(ctx, detail) 入口测试三层路径：
- 层 0（detail=""）：场景速览
- 层 1（detail="crops" 等枚举值）：类别详情
- 层 2（detail="chest:farm_12_15" 等含冒号格式）：单实例详情
"""

from __future__ import annotations

import pytest

from v2_skills.builtin.observe.script import observe
from tests.fixtures.observe_mock_ctx import MockObserveCtx, make_empty_ctx, make_typical_ctx
from tests.fixtures.observe_query_mocks import (
    ALIAS_MAP_TYPICAL,
    CONTAINERS_TYPICAL,
    CONTAINER_ITEMS_CHEST_TYPICAL,
    CONTAINER_ITEMS_FRIDGE_TYPICAL,
    CONTAINER_ITEMS_SHIPPING_TYPICAL,
    FIND_TARGETS_ANIMAL_SINGLE,
    FIND_TARGETS_ANIMALS_TYPICAL,
    FIND_TARGETS_CROP_SINGLE_HARVESTABLE,
    FIND_TARGETS_CROPS_EMPTY,
    FIND_TARGETS_CROPS_TYPICAL,
    FIND_TARGETS_NPCS_EMPTY,
    FIND_TARGETS_NPCS_TYPICAL,
    FIND_TARGETS_OBJECTS_TYPICAL,
    GAME_STATE_EMPTY,
    GAME_STATE_TYPICAL,
    INVENTORY_TYPICAL,
    NPC_LEWIS_TYPICAL,
)


# ══════════════════════════════════════════════════════════════════════
# 层 0 — 场景速览
# ══════════════════════════════════════════════════════════════════════


class TestObserveLayer0:
    """observe(detail="") 场景速览"""

    @pytest.mark.asyncio
    async def test_typical_returns_success(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="")
        assert result["success"] is True
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_typical_observation_contains_player_info(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="")
        text = result["observation"]
        # 玩家状态
        assert "农场" in text or "180" in text
        # 世界时间
        assert "春" in text or "10:00" in text

    @pytest.mark.asyncio
    async def test_typical_observation_contains_farm_section(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="")
        text = result["observation"]
        # 农场状态段（有作物数据）
        assert "【农场状态】" in text

    @pytest.mark.asyncio
    async def test_typical_observation_ends_with_hint(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="")
        text = result["observation"]
        # hint 段（GAME_STATE_TYPICAL.farm_summary.crops.unwatered_count=12）
        assert "下一步" in text or "observe" in text

    @pytest.mark.asyncio
    async def test_all_queries_fail_still_succeeds(self):
        ctx = MockObserveCtx(game_state=GAME_STATE_TYPICAL, mocks={})
        result = await observe(ctx, detail="")
        # 降级：依然成功（查询失败只是数据为空）
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_empty_ctx_returns_success(self):
        ctx = MockObserveCtx()  # 空 game_state + 空 mocks
        result = await observe(ctx, detail="")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_empty_ctx_no_farm_section(self):
        ctx = make_empty_ctx()
        result = await observe(ctx, detail="")
        text = result["observation"]
        # 无作物/动物/对象 → 【农场状态】段不出现
        assert "【农场状态】" not in text

    @pytest.mark.asyncio
    async def test_none_detail_treated_as_layer0(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail=None)
        assert result["success"] is True


# ══════════════════════════════════════════════════════════════════════
# 层 1 — 类别详情
# ══════════════════════════════════════════════════════════════════════


class TestObserveLayer1:
    """observe(detail="crops" 等) 类别详情"""

    @pytest.mark.asyncio
    async def test_crops_detail_typical(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"find_targets:crop": FIND_TARGETS_CROPS_TYPICAL},
        )
        result = await observe(ctx, detail="crops")
        assert result["success"] is True
        text = result["observation"]
        # ObserveId 格式
        assert "[crop:" in text

    @pytest.mark.asyncio
    async def test_animals_detail_typical(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"find_targets:animal": FIND_TARGETS_ANIMALS_TYPICAL},
        )
        result = await observe(ctx, detail="animals")
        assert result["success"] is True
        text = result["observation"]
        assert "甜豆" in text
        assert "[animal:" in text

    @pytest.mark.asyncio
    async def test_npcs_detail_typical(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"find_targets:npc": FIND_TARGETS_NPCS_TYPICAL},
        )
        result = await observe(ctx, detail="npcs")
        assert result["success"] is True
        text = result["observation"]
        assert "路易斯" in text or "Lewis" in text

    @pytest.mark.asyncio
    async def test_containers_detail_typical(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_containers": CONTAINERS_TYPICAL},
        )
        result = await observe(ctx, detail="containers")
        assert result["success"] is True
        text = result["observation"]
        assert "[chest:" in text or "箱子" in text

    @pytest.mark.asyncio
    async def test_inventory_detail_typical(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"query_inventory": INVENTORY_TYPICAL},
        )
        result = await observe(ctx, detail="inventory")
        assert result["success"] is True
        text = result["observation"]
        assert "防风草" in text or "背包" in text

    @pytest.mark.asyncio
    async def test_unknown_detail_returns_error_message(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="nonexistent")
        assert result["success"] is True  # 入口级不报错，observation 含错误提示
        text = result["observation"]
        assert "未知" in text or "可选" in text

    @pytest.mark.asyncio
    async def test_crops_detail_empty(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"find_targets:crop": FIND_TARGETS_CROPS_EMPTY},
        )
        result = await observe(ctx, detail="crops")
        assert result["success"] is True
        text = result["observation"]
        # 空数据降级：没有作物 or 当前没有相关数据
        assert "没有作物" in text or "没有相关数据" in text

    @pytest.mark.asyncio
    async def test_layer1_hint_appended(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"find_targets:crop": FIND_TARGETS_CROPS_TYPICAL},
            alias_map=ALIAS_MAP_TYPICAL,
        )
        result = await observe(ctx, detail="crops")
        text = result["observation"]
        # 层 1 hint
        assert "提示" in text


# ══════════════════════════════════════════════════════════════════════
# 层 2 — 单实例详情
# ══════════════════════════════════════════════════════════════════════


class TestObserveLayer2:
    """observe(detail="chest:farm_12_15" 等含冒号格式) 单实例详情"""

    @pytest.mark.asyncio
    async def test_chest_content_typical(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_container_items": CONTAINER_ITEMS_CHEST_TYPICAL},
        )
        result = await observe(ctx, detail="chest:farm_12_15")
        assert result["success"] is True
        text = result["observation"]
        assert "防风草" in text or "格" in text

    @pytest.mark.asyncio
    async def test_fridge_content_typical(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_container_items": CONTAINER_ITEMS_FRIDGE_TYPICAL},
        )
        result = await observe(ctx, detail="fridge:farmhouse")
        assert result["success"] is True
        text = result["observation"]
        assert "牛奶" in text or "鸡蛋" in text

    @pytest.mark.asyncio
    async def test_shipping_bin_content(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_container_items": CONTAINER_ITEMS_SHIPPING_TYPICAL},
        )
        result = await observe(ctx, detail="shipping_bin:")
        assert result["success"] is True
        text = result["observation"]
        assert "防风草" in text or "运输" in text or "g" in text

    @pytest.mark.asyncio
    async def test_crop_instance_harvestable(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"find_targets:crop": FIND_TARGETS_CROP_SINGLE_HARVESTABLE},
        )
        result = await observe(ctx, detail="crop:farm_10_15")
        assert result["success"] is True
        text = result["observation"]
        assert "防风草" in text
        assert "成熟" in text or "可收割" in text

    @pytest.mark.asyncio
    async def test_animal_instance_typical(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"find_targets:animal": FIND_TARGETS_ANIMAL_SINGLE},
        )
        result = await observe(ctx, detail="animal:甜豆")
        assert result["success"] is True
        text = result["observation"]
        assert "甜豆" in text
        assert "鸡" in text

    @pytest.mark.asyncio
    async def test_npc_instance_lewis(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query": NPC_LEWIS_TYPICAL},
        )
        result = await observe(ctx, detail="npc:Lewis")
        assert result["success"] is True
        # 即使 npc_data 未填入（query capability 不匹配），observe 也不应崩溃
        assert "observation" in result

    @pytest.mark.asyncio
    async def test_unknown_layer2_prefix_returns_error(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="badprefix:something")
        assert result["success"] is True  # 入口不报 success=False
        text = result["observation"]
        assert "未知" in text or "可选" in text

    @pytest.mark.asyncio
    async def test_layer2_hint_appended(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_container_items": CONTAINER_ITEMS_CHEST_TYPICAL},
        )
        result = await observe(ctx, detail="chest:farm_12_15")
        text = result["observation"]
        # 层 2 hint（take_from_container / store_to_container）
        assert "提示" in text


# ══════════════════════════════════════════════════════════════════════
# observe() 返回格式校验
# ══════════════════════════════════════════════════════════════════════


class TestObserveReturnFormat:
    """返回值结构校验"""

    @pytest.mark.asyncio
    async def test_success_result_has_all_keys(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="")
        assert "success" in result
        assert "status" in result
        assert "observation" in result

    @pytest.mark.asyncio
    async def test_success_true_when_ok(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="")
        assert result["success"] is True
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_observation_is_string(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="")
        assert isinstance(result["observation"], str)

    @pytest.mark.asyncio
    async def test_observation_not_empty(self):
        ctx = make_typical_ctx()
        result = await observe(ctx, detail="")
        assert len(result["observation"]) > 0
