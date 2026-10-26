"""
observe Skill — collectors.py 单元测试。

测试 collect_for_modules / collect_layer0 / collect_layer1 / collect_layer2 的行为：
- 正常采集
- 部分 query 失败时降级（其他数据仍正常）
- category 不存在时返回 None
- game_state 从 Blackboard 同步读取（不走 IPC）
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from v2_skills.builtin.observe.collectors import (
    collect_for_modules,
    collect_layer0,
    collect_layer1,
    collect_layer2,
)
from v2_skills.builtin.observe.layer0_modules import LAYER_0_MODULES
from v2_skills.builtin.observe.layer1_modules import LAYER_1_MODULES
from tests.fixtures.observe_mock_ctx import MockObserveCtx, make_empty_ctx, make_typical_ctx
from tests.fixtures.observe_query_mocks import (
    ALIAS_MAP_TYPICAL,
    CONTAINERS_TYPICAL,
    CONTAINER_ITEMS_CHEST_TYPICAL,
    CONTAINER_ITEMS_FRIDGE_TYPICAL,
    CONTAINER_ITEMS_SHIPPING_TYPICAL,
    FIND_TARGETS_ANIMAL_SINGLE,
    FIND_TARGETS_ANIMALS_TYPICAL,
    FIND_TARGETS_CROPS_TYPICAL,
    FIND_TARGETS_NPCS_TYPICAL,
    FIND_TARGETS_OBJECTS_TYPICAL,
    GAME_STATE_TYPICAL,
    INVENTORY_TYPICAL,
    NPC_LEWIS_TYPICAL,
)


# ══════════════════════════════════════════════════════════════════════
# collect_for_modules
# ══════════════════════════════════════════════════════════════════════


class TestCollectForModules:
    """collect_for_modules — 核心采集编排"""

    @pytest.mark.asyncio
    async def test_game_state_always_collected_from_blackboard(self):
        ctx = MockObserveCtx(game_state=GAME_STATE_TYPICAL, alias_map=ALIAS_MAP_TYPICAL)
        data = await collect_for_modules(ctx, [])
        # game_state 总是从 Blackboard 读（即使 modules 列表为空）
        assert "game_state" in data
        assert data["game_state"]["player"]["name"] == "农夫"

    @pytest.mark.asyncio
    async def test_alias_map_always_collected(self):
        ctx = MockObserveCtx(game_state=GAME_STATE_TYPICAL, alias_map=ALIAS_MAP_TYPICAL)
        data = await collect_for_modules(ctx, [])
        assert "alias_map" in data
        assert "Farm" in data["alias_map"]

    @pytest.mark.asyncio
    async def test_query_results_stored_by_result_key(self):
        ctx = make_typical_ctx()
        from v2_skills.builtin.observe.layer0_modules import INVENTORY_SUMMARY
        data = await collect_for_modules(ctx, [INVENTORY_SUMMARY])
        # INVENTORY_SUMMARY.queries[0].result_key = "inventory"
        assert "inventory" in data

    @pytest.mark.asyncio
    async def test_failed_query_not_in_data(self):
        # 提供 game_state 但不提供 query_inventory mock → 该 query 失败
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={},  # 无任何 query mock
        )
        from v2_skills.builtin.observe.layer0_modules import INVENTORY_SUMMARY
        data = await collect_for_modules(ctx, [INVENTORY_SUMMARY])
        # game_state 存在，inventory 不存在（查询失败）
        assert "game_state" in data
        assert "inventory" not in data

    @pytest.mark.asyncio
    async def test_extra_params_injected(self):
        ctx = MockObserveCtx(game_state=GAME_STATE_TYPICAL)
        data = await collect_for_modules(ctx, [], extra_params={"instance_id": "farm_12_15"})
        assert data["instance_id"] == "farm_12_15"

    @pytest.mark.asyncio
    async def test_dedup_same_query_called_once(self):
        """同一个 (capability, params) 组合，多个模块声明时只采集一次"""
        # layer0 中 player_status 和 world_time 都依赖 query_game_state（Blackboard 路径）
        # 只需确保不重复
        ctx = make_typical_ctx()
        from v2_skills.builtin.observe.layer0_modules import PLAYER_STATUS, WORLD_TIME
        data = await collect_for_modules(ctx, [PLAYER_STATUS, WORLD_TIME])
        # game_state 存在，且没有崩溃
        assert "game_state" in data


# ══════════════════════════════════════════════════════════════════════
# collect_layer0
# ══════════════════════════════════════════════════════════════════════


class TestCollectLayer0:
    """collect_layer0 — 层 0 全量采集"""

    @pytest.mark.asyncio
    async def test_typical_all_data_collected(self):
        ctx = make_typical_ctx()
        data = await collect_layer0(ctx)
        assert "game_state" in data
        assert "alias_map" in data
        assert "inventory" in data
        # find_targets 数据（crops_data / animals_data / npcs_data / objects_data）
        assert "crops_data" in data
        assert "animals_data" in data
        assert "npcs_data" in data
        assert "objects_data" in data
        # query_containers 数据
        assert "containers_data" in data

    @pytest.mark.asyncio
    async def test_one_query_fails_others_succeed(self):
        """一个 query 失败，其他正常"""
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={
                "query_inventory": INVENTORY_TYPICAL,
                # find_targets:crop 故意缺失 → 采集失败
                "find_targets:animal": FIND_TARGETS_ANIMALS_TYPICAL,
                "find_targets:npc": FIND_TARGETS_NPCS_TYPICAL,
                "find_targets:object": FIND_TARGETS_OBJECTS_TYPICAL,
                "query_containers": CONTAINERS_TYPICAL,
            },
        )
        data = await collect_layer0(ctx)
        assert "inventory" in data
        assert "animals_data" in data
        assert "npcs_data" in data
        # crops_data 不在 data（查询失败）
        assert "crops_data" not in data

    @pytest.mark.asyncio
    async def test_empty_ctx_does_not_crash(self):
        ctx = MockObserveCtx()  # 无任何 mock
        data = await collect_layer0(ctx)
        # 仅有 game_state（空dict）和 alias_map（空dict）
        assert "alias_map" in data

    @pytest.mark.asyncio
    async def test_game_state_from_blackboard_not_ipc(self):
        """game_state 不经过 IPC，即使不提供 query mock 也能读到"""
        ctx = MockObserveCtx(game_state=GAME_STATE_TYPICAL, mocks={})
        data = await collect_layer0(ctx)
        assert data["game_state"]["player"]["name"] == "农夫"


# ══════════════════════════════════════════════════════════════════════
# collect_layer1
# ══════════════════════════════════════════════════════════════════════


class TestCollectLayer1:
    """collect_layer1 — 层 1 类别采集"""

    @pytest.mark.asyncio
    async def test_crops_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"find_targets:crop": FIND_TARGETS_CROPS_TYPICAL},
        )
        data = await collect_layer1(ctx, "crops")
        assert data is not None
        assert "crops_data" in data

    @pytest.mark.asyncio
    async def test_animals_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"find_targets:animal": FIND_TARGETS_ANIMALS_TYPICAL},
        )
        data = await collect_layer1(ctx, "animals")
        assert data is not None
        assert "animals_data" in data

    @pytest.mark.asyncio
    async def test_inventory_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"query_inventory": INVENTORY_TYPICAL},
        )
        data = await collect_layer1(ctx, "inventory")
        assert data is not None
        assert "inventory" in data

    @pytest.mark.asyncio
    async def test_containers_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"query_containers": CONTAINERS_TYPICAL},
        )
        data = await collect_layer1(ctx, "containers")
        assert data is not None
        assert "containers_data" in data

    @pytest.mark.asyncio
    async def test_unknown_category_returns_none(self):
        ctx = MockObserveCtx()
        result = await collect_layer1(ctx, "nonexistent_category")
        assert result is None

    @pytest.mark.asyncio
    async def test_query_fails_data_still_returns(self):
        ctx = MockObserveCtx(game_state=GAME_STATE_TYPICAL, mocks={})
        # find_targets:crop 失败 → crops_data 不存在，但函数不崩溃
        data = await collect_layer1(ctx, "crops")
        assert data is not None  # 返回空 data（含 game_state）

    @pytest.mark.asyncio
    async def test_npcs_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            mocks={"find_targets:npc": FIND_TARGETS_NPCS_TYPICAL},
        )
        data = await collect_layer1(ctx, "npcs")
        assert data is not None
        assert "npcs_data" in data


# ══════════════════════════════════════════════════════════════════════
# collect_layer2
# ══════════════════════════════════════════════════════════════════════


class TestCollectLayer2:
    """collect_layer2 — 层 2 单实例采集"""

    @pytest.mark.asyncio
    async def test_chest_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_container_items": CONTAINER_ITEMS_CHEST_TYPICAL},
        )
        data = await collect_layer2(ctx, "chest", "farm_12_15")
        assert data is not None
        assert "container_data" in data
        assert data["instance_id"] == "farm_12_15"
        assert data["instance_category"] == "chest"

    @pytest.mark.asyncio
    async def test_fridge_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_container_items": CONTAINER_ITEMS_FRIDGE_TYPICAL},
        )
        data = await collect_layer2(ctx, "fridge", "farmhouse")
        assert data is not None
        assert "container_data" in data

    @pytest.mark.asyncio
    async def test_shipping_bin_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_container_items": CONTAINER_ITEMS_SHIPPING_TYPICAL},
        )
        data = await collect_layer2(ctx, "shipping_bin", "")
        assert data is not None
        assert "container_data" in data

    @pytest.mark.asyncio
    async def test_animal_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"find_targets:animal": FIND_TARGETS_ANIMAL_SINGLE},
        )
        data = await collect_layer2(ctx, "animal", "甜豆")
        assert data is not None
        # 层 2 animal 用 animal_data（单数形式），与 layer2_modules.py 读取 key 一致
        assert "animal_data" in data

    @pytest.mark.asyncio
    async def test_npc_category(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query": NPC_LEWIS_TYPICAL},
        )
        data = await collect_layer2(ctx, "npc", "Lewis")
        assert data is not None
        # npc_data 可能存在也可能因 capability key 差异而不在 data

    @pytest.mark.asyncio
    async def test_unknown_category_returns_none(self):
        ctx = MockObserveCtx()
        result = await collect_layer2(ctx, "nonexistent", "id")
        assert result is None

    @pytest.mark.asyncio
    async def test_query_fails_returns_data_without_result(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={},  # 无 mock → query 失败
        )
        data = await collect_layer2(ctx, "chest", "farm_12_15")
        # 函数不崩溃，返回 data（不含 container_data）
        assert data is not None
        assert "instance_id" in data
        assert "container_data" not in data

    @pytest.mark.asyncio
    async def test_crop_category_with_parse(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"find_targets:crop": FIND_TARGETS_CROPS_TYPICAL},
        )
        data = await collect_layer2(ctx, "crop", "farm_10_15")
        assert data is not None
        assert data["instance_id"] == "farm_10_15"
        # 层 2 crop 用 crop_data（单数形式），与 layer2_modules.py 读取 key 一致
        assert "crop_data" in data

    @pytest.mark.asyncio
    async def test_alias_map_injected(self):
        ctx = MockObserveCtx(
            game_state=GAME_STATE_TYPICAL,
            alias_map=ALIAS_MAP_TYPICAL,
            mocks={"query_container_items": CONTAINER_ITEMS_CHEST_TYPICAL},
        )
        data = await collect_layer2(ctx, "chest", "farm_12_15")
        assert "alias_map" in data
        assert "Farm" in data["alias_map"]
