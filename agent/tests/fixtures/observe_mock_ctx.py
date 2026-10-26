"""
observe Skill 测试用 mock 执行上下文。

提供 MockObserveCtx 类，允许 Wave 3 单元测试注入预设的 query 返回值，
无需启动实际的 Bridge/Mod 通信。

使用示例：
    from tests.fixtures.observe_mock_ctx import MockObserveCtx
    from tests.fixtures.observe_query_mocks import (
        GAME_STATE_TYPICAL,
        FIND_TARGETS_CROPS_TYPICAL,
        CONTAINERS_TYPICAL,
        INVENTORY_TYPICAL,
        FIND_TARGETS_ANIMALS_TYPICAL,
        FIND_TARGETS_NPCS_EMPTY,
        FIND_TARGETS_OBJECTS_EMPTY,
        ALIAS_MAP_TYPICAL,
    )

    ctx = MockObserveCtx(
        game_state=GAME_STATE_TYPICAL,
        alias_map=ALIAS_MAP_TYPICAL,
        mocks={
            "query_inventory": INVENTORY_TYPICAL,
            "find_targets:crop": FIND_TARGETS_CROPS_TYPICAL,
            "find_targets:animal": FIND_TARGETS_ANIMALS_TYPICAL,
            "find_targets:npc": FIND_TARGETS_NPCS_EMPTY,
            "find_targets:object": FIND_TARGETS_OBJECTS_EMPTY,
            "query_containers": CONTAINERS_TYPICAL,
        }
    )
    result = await some_observe_function(ctx)
"""

from __future__ import annotations

from typing import Any, Callable, Union


class MockObserveCtx:
    """
    模拟 observe Skill 的执行上下文，预设 query 返回值。

    主要用途：
    1. 单元测试每个 ObserveModule.render 函数（纯函数测试）
    2. 集成测试层 0/1/2 的编排逻辑（无真实 IPC）

    Mock key 命名规则：
    - 无额外参数：capability 名，如 "query_inventory", "query_containers"
    - 有 type 参数（find_targets）："{capability}:{type_value}"，如 "find_targets:crop"
    - game_state：特殊，通过 get_game_state() 而非 query() 获取

    支持 callable mock（动态返回）：
        mocks={"query_inventory": lambda params: INVENTORY_EMPTY if params.get("empty") else INVENTORY_TYPICAL}
    """

    def __init__(
        self,
        game_state: dict | None = None,
        alias_map: dict | None = None,
        mocks: dict[str, Union[dict, Callable[[dict], dict]]] | None = None,
    ):
        """
        参数:
            game_state: Blackboard game_state 内容，None 时返回空字典
            alias_map: {NameOrUniqueName → locshort} 映射，None 时返回空字典
            mocks: {mock_key → result_dict_or_callable}
        """
        self._game_state = game_state or {}
        self._alias_map = alias_map or {}
        self._mocks = mocks or {}

    def get_game_state(self) -> dict:
        """
        获取 Blackboard 中的 game_state（同步，无 IPC）。
        对应 observe Skill 中从 Blackboard 读取的操作。
        """
        return self._game_state

    def get_alias_map(self) -> dict:
        """
        获取地图别名映射表（同步）。
        对应 observe Skill 中从 query_buildings 构建的 alias_map。
        """
        return self._alias_map

    async def query(self, capability: str, params: dict | None = None) -> dict:
        """
        模拟异步 query_* 调用。

        查找逻辑（按优先级）：
        1. "{capability}:{type_value}" —— find_targets 的精确匹配
        2. "{capability}" —— 无 type 参数的精确匹配

        参数:
            capability: 如 "query_inventory", "find_targets"
            params: 额外参数，如 {"target_type": "crop"}

        返回:
            预设的 result dict

        异常:
            KeyError: 没有对应的 mock
        """
        params = params or {}

        # 构建带 type 参数的复合 key
        type_val = params.get("target_type") or params.get("type") or ""
        composite_key = f"{capability}:{type_val}" if type_val else capability

        # 按优先级查找 mock
        mock = self._mocks.get(composite_key) or self._mocks.get(capability)

        if mock is None:
            raise KeyError(
                f"No mock for capability='{capability}' params={params!r}. "
                f"Available keys: {list(self._mocks.keys())}"
            )

        # 支持 callable mock
        if callable(mock):
            return mock(params)
        return mock

    async def query_failing(self, capability: str, _params: dict | None = None) -> dict:
        """
        模拟 query 失败的辅助方法（抛出异常）。
        用于测试降级逻辑（error → "（无法获取）"）。
        """
        raise RuntimeError(f"Mock query failure: {capability}")

    def with_mock(self, key: str, value: Union[dict, Callable[[dict], dict]]) -> "MockObserveCtx":
        """
        返回添加了额外 mock 的新 ctx（不修改当前实例）。
        方便链式构造测试用例。

        示例:
            ctx2 = ctx.with_mock("find_targets:crop", CROPS_EMPTY)
        """
        new_mocks = dict(self._mocks)
        new_mocks[key] = value
        return MockObserveCtx(
            game_state=self._game_state,
            alias_map=self._alias_map,
            mocks=new_mocks,
        )

    def with_game_state(self, game_state: dict) -> "MockObserveCtx":
        """返回替换了 game_state 的新 ctx。"""
        return MockObserveCtx(
            game_state=game_state,
            alias_map=self._alias_map,
            mocks=self._mocks,
        )


# ── 工厂函数：常用 ctx 预设 ──────────────────────────────────────────

def make_typical_ctx(alias_map: dict | None = None) -> MockObserveCtx:
    """
    创建典型场景的 ctx（农场有作物/动物/NPC/容器）。
    主要用于层 0 综合测试。
    """
    from tests.fixtures.observe_query_mocks import (
        ALIAS_MAP_TYPICAL,
        CONTAINERS_TYPICAL,
        FIND_TARGETS_ANIMALS_TYPICAL,
        FIND_TARGETS_CROPS_TYPICAL,
        FIND_TARGETS_NPCS_TYPICAL,
        FIND_TARGETS_OBJECTS_TYPICAL,
        GAME_STATE_TYPICAL,
        INVENTORY_TYPICAL,
    )

    return MockObserveCtx(
        game_state=GAME_STATE_TYPICAL,
        alias_map=alias_map or ALIAS_MAP_TYPICAL,
        mocks={
            "query_inventory": INVENTORY_TYPICAL,
            "find_targets:crop": FIND_TARGETS_CROPS_TYPICAL,
            "find_targets:animal": FIND_TARGETS_ANIMALS_TYPICAL,
            "find_targets:npc": FIND_TARGETS_NPCS_TYPICAL,
            "find_targets:object": FIND_TARGETS_OBJECTS_TYPICAL,
            "query_containers": CONTAINERS_TYPICAL,
        },
    )


def make_empty_ctx() -> MockObserveCtx:
    """
    创建空场景的 ctx（无作物/动物/NPC/容器）。
    用于测试空数据时的渲染降级。
    """
    from tests.fixtures.observe_query_mocks import (
        ALIAS_MAP_TYPICAL,
        CONTAINERS_EMPTY,
        FIND_TARGETS_ANIMALS_EMPTY,
        FIND_TARGETS_CROPS_EMPTY,
        FIND_TARGETS_NPCS_EMPTY,
        FIND_TARGETS_OBJECTS_EMPTY,
        GAME_STATE_EMPTY,
        INVENTORY_EMPTY,
    )

    return MockObserveCtx(
        game_state=GAME_STATE_EMPTY,
        alias_map=ALIAS_MAP_TYPICAL,
        mocks={
            "query_inventory": INVENTORY_EMPTY,
            "find_targets:crop": FIND_TARGETS_CROPS_EMPTY,
            "find_targets:animal": FIND_TARGETS_ANIMALS_EMPTY,
            "find_targets:npc": FIND_TARGETS_NPCS_EMPTY,
            "find_targets:object": FIND_TARGETS_OBJECTS_EMPTY,
            "query_containers": CONTAINERS_EMPTY,
        },
    )
