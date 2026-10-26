"""
observe Skill 数据采集编排。

负责调用 L1 query_* 命令，处理失败降级。
按层不同采用不同采集策略：
- 层 0：通过 asyncio.gather 并发提交（Python 协程并发，Mod 侧仍串行）
- 层 1：单个 query 即足够
- 层 2：单个 query + extra_params 注入

ctx 接口约定（duck typing，兼容生产 SkillContext 和 MockObserveCtx）：
  ctx.get_game_state() -> dict   # 同步，从 Blackboard 读取 game_state raw dict
  ctx.get_alias_map()  -> dict   # 同步，获取 {NameOrUniqueName → locshort} 映射
  ctx.query(cap, params) -> dict # async，调用 Mod 侧 query_* 能力

注：生产环境 SkillContext 通过 get_game_state() 适配层提供（见 context.py）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .module_base import ObserveModule, QuerySpec

log = logging.getLogger("v2_skills.observe.collectors")


# ── 内部辅助：单次 query 采集 ─────────────────────────────────────────

async def _fetch_one(ctx: Any, capability: str, params: dict) -> tuple[str, Any]:
    """
    执行单次 query_* 调用，返回 (capability, result) 元组。
    失败时返回 (capability, None)，不抛出异常（调用者按 None 判断降级）。

    Args:
        ctx: Skill 执行上下文
        capability: 能力名（如 "find_targets", "query_containers"）
        params: 能力参数 dict

    Returns:
        (capability_key, result_or_None) —— capability_key 用于构建去重 map
    """
    try:
        result = await ctx.query(capability, params)
        return capability, result
    except Exception as e:
        log.warning("query %s params=%s 失败: %s", capability, params, e)
        return capability, None


# ── 核心编排：按模块列表采集 ─────────────────────────────────────────

async def collect_for_modules(
    ctx: Any,
    modules: list[ObserveModule],
    extra_params: dict | None = None,
) -> dict:
    """
    采集一组模块所需的所有 query 结果。

    采集步骤：
    1. 遍历所有模块的 QuerySpec，按 (capability, sorted_params) 去重
    2. 特殊处理 game_state：直接调用 ctx.get_game_state()（同步，无 IPC）
    3. 其余 query 通过 asyncio.gather 并发提交（Mod 侧串行，Python 侧协程切换）
    4. 失败的 query 不写入 data，render 函数通过 data.get(cap) is None 判断降级
    5. 合并 extra_params 到 data（如层 2 的 instance_id/instance_category）

    Args:
        ctx: Skill 执行上下文（提供 query / get_game_state / get_alias_map）
        modules: 要采集的 ObserveModule 列表
        extra_params: 额外注入 data 的参数（如层 2 的 instance_id）

    Returns:
        data dict，键为 capability 名或 extra_params 的键。
        失败的 query 不出现在返回值中（render 函数检查 data.get(cap) is None 判断降级）。
    """
    # ── 步骤 1：收集所有需要的 QuerySpec 并去重 ──
    # 去重 key = (capability, frozen_params_repr)
    # value = {"capability": str, "params": dict, "result_key": str}
    # result_key 决定结果写入 data 的哪个键（render 函数按此 key 读取）
    #
    # 特殊处理：
    #   - capability == "query_game_state"：从 Blackboard 同步读取，不走 IPC，result_key = "game_state"
    #   - 其他：通过 ctx.query() 发给 Mod，result_key 可为空（则用 capability 名）
    needed: dict[str, dict] = {}  # dedup_key → {capability, params, result_key}

    has_game_state_dep = False  # 是否有模块依赖 game_state

    for module in modules:
        for q in module.queries:
            params = q.params or {}
            # query_game_state → Blackboard 同步读取（不走 IPC）
            if q.capability == "query_game_state":
                has_game_state_dep = True
                continue
            dedup_key = f"{q.capability}:{sorted(params.items())}"
            if dedup_key not in needed:
                needed[dedup_key] = {
                    "capability": q.capability,
                    "params": params,
                    "result_key": q.result_key or q.capability,
                }

    # ── 步骤 2：game_state 从 Blackboard 同步读取 ──
    data: dict[str, Any] = {}

    # 始终尝试读取 game_state（层 0 hint 需要，且无代价）
    try:
        game_state = ctx.get_game_state()
        if game_state is not None:
            data["game_state"] = game_state
    except Exception as e:
        log.warning("get_game_state 失败: %s", e)
        # game_state 失败时 data 中不存在 "game_state"，render 函数降级

    # 同样尝试获取 alias_map（layer 0 组装和地图名解析需要）
    try:
        alias_map = ctx.get_alias_map()
        data["alias_map"] = alias_map or {}
    except Exception as e:
        log.debug("get_alias_map 不可用（%s），使用空 map", e)
        data["alias_map"] = {}

    # ── 步骤 3：并发（Python 层 gather）提交所有 query ──
    # 注意：Mod 单线程，Python 层 gather 的"并发"是协程切换，实际仍串行发给 Mod。
    # 保持 gather 形式，为未来 Mod 侧支持 batch_query 预留升级路径。
    if needed:
        tasks = [
            _fetch_one(ctx, item["capability"], item["params"])
            for item in needed.values()
        ]
        results_raw = await asyncio.gather(*tasks)

        # results_raw 顺序与 needed.values() 一致，按 result_key 写入 data
        for (_capability, result), item in zip(results_raw, needed.values()):
            if result is not None:
                # 用 result_key 写入 data，确保 render 函数能按预期 key 读到数据
                rkey = item["result_key"]
                data[rkey] = result

    # ── 步骤 4：合并 extra_params ──
    if extra_params:
        data.update(extra_params)

    return data


# ── 分层入口 ─────────────────────────────────────────────────────────

async def collect_layer0(ctx: Any) -> dict:
    """
    采集层 0 场景速览所需的全部数据。

    采集内容（对应 8 个子模块）：
    - game_state（Blackboard 同步）
    - alias_map（Blackboard 同步）
    - query_inventory
    - find_targets（crop / animal / object / npc）
    - query_containers

    Returns:
        data dict，含 game_state / alias_map / query_inventory /
        find_targets / query_containers 等键（失败的键不存在）。
    """
    from .modules import LAYER_0_MODULES
    return await collect_for_modules(ctx, LAYER_0_MODULES)


async def collect_layer1(ctx: Any, category: str) -> dict | None:
    """
    采集层 1 类别详情数据。

    Args:
        ctx: Skill 执行上下文
        category: detail 枚举值（如 "crops"/"animals"/"containers"）

    Returns:
        data dict，或 None（category 不在注册表中）
    """
    from .modules import get_layer1_module
    module = get_layer1_module(category)
    if module is None:
        return None
    return await collect_for_modules(ctx, [module])


async def collect_layer2(ctx: Any, category: str, id_str: str) -> dict | None:
    """
    采集层 2 单实例数据。

    层 2 模块的 QuerySpec 为空列表（spec §7.3：参数在运行时计算）。
    本函数根据 category 动态构建查询参数，按 spec §7.3 的映射关系发起 IPC 调用，
    将结果以 render 函数期望的 result_key 写入 data。

    spec §7.3 采集映射：
      chest        → query_container_items(container_id=<loc>:chest_<x>_<y>)  → data["container_data"]
      fridge       → query_container_items(container_id=<loc>:fridge_0_0)      → data["container_data"]
      shipping_bin → query_container_items(container_id="shipping_bin")         → data["container_data"]
      crop         → find_targets(target_type=crop, location=loc, x=x, y=y)   → data["crop_data"]
      animal       → find_targets(target_type=animal, name=id_str)             → data["animal_data"]
      npc          → query(type=npc, name=id_str)                              → data["npc_data"]

    Args:
        ctx: Skill 执行上下文
        category: 实例类别前缀（"chest"/"fridge"/"shipping_bin"/"crop"/"animal"/"npc"）
        id_str: 实例 ID（如 "farm_12_15"/"甜豆"/"Lewis"，shipping_bin 时为空字符串）

    Returns:
        data dict（含 game_state / alias_map / instance_id / instance_category
        以及对应 result_key 的 query 结果），或 None（category 不在注册表中）
    """
    from .modules import get_layer2_module
    from .module_base import _parse_loc_xy, _resolve_loc

    module = get_layer2_module(category)
    if module is None:
        return None

    # ── 基础数据（game_state + alias_map）──
    data: dict[str, Any] = {
        "instance_id": id_str,
        "instance_category": category,
    }

    try:
        gs = ctx.get_game_state()
        if gs:
            data["game_state"] = gs
    except Exception as e:
        log.debug("collect_layer2 get_game_state 失败: %s", e)

    try:
        alias_map = ctx.get_alias_map()
        data["alias_map"] = alias_map or {}
    except Exception as e:
        log.debug("collect_layer2 get_alias_map 不可用: %s", e)
        data["alias_map"] = {}

    alias_map = data.get("alias_map", {})

    # ── 按 category 动态查询 ──
    try:
        if category == "chest":
            # id_str = "farm_12_15" → container_id = "Farm:chest_12_15"
            locshort, x_str, y_str = _parse_loc_xy(id_str)
            full_loc = _resolve_loc(locshort, alias_map)
            container_id = f"{full_loc}:chest_{x_str}_{y_str}"
            result = await ctx.query("query_container_items", {"container_id": container_id})
            data["container_data"] = result

        elif category == "fridge":
            # id_str = "farmhouse" 或 "" → container_id = "FarmHouse:fridge_0_0"
            locshort = id_str or "farmhouse"
            full_loc = _resolve_loc(locshort, alias_map)
            container_id = f"{full_loc}:fridge_0_0"
            result = await ctx.query("query_container_items", {"container_id": container_id})
            data["container_data"] = result

        elif category == "shipping_bin":
            result = await ctx.query("query_container_items", {"container_id": "shipping_bin"})
            data["container_data"] = result

        elif category == "crop":
            # id_str = "farm_12_15" → location=Farm, x=12, y=15
            locshort, x_str, y_str = _parse_loc_xy(id_str)
            full_loc = _resolve_loc(locshort, alias_map)
            result = await ctx.query("find_targets", {
                "target_type": "crop",
                "location": full_loc,
            })
            data["crop_data"] = result

        elif category == "animal":
            # id_str = "甜豆" or "甜豆#1"（含 #N 后缀）
            name = id_str.split("#")[0]
            result = await ctx.query("find_targets", {
                "target_type": "animal",
                "name": name,
            })
            data["animal_data"] = result

        elif category == "npc":
            # id_str = "Lewis"
            result = await ctx.query("query", {"type": "npc", "name": id_str})
            data["npc_data"] = result

    except Exception as e:
        log.warning("collect_layer2 category=%s id=%r 查询失败: %s", category, id_str, e)
        # 查询失败时 data 中缺少对应 result_key，render 函数降级

    return data
