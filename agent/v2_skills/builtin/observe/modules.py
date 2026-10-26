"""
observe Skill 子模块注册表。

按层组织 21 个 ObserveModule，供 script.py 编排时使用。
新增模块只需在对应 layerN_modules.py 添加并在此注册。

层次结构：
- 层 0（8 个）：场景速览，observe() 无参数时组合渲染
- 层 1（7 个）：类别列表，observe(detail=枚举值) 时渲染单个类别
- 层 2（6 个）：单实例详情，observe(detail="type:id") 时渲染单个实体

使用示例：
    from observe.modules import get_layer1_module, get_layer2_module, LAYER_0_MODULES

    # 层 0：场景速览（8 个模块顺序渲染）
    for mod in LAYER_0_MODULES:
        if mod.applicable(data):
            text = mod.safe_render(data)

    # 层 1：按枚举值查找模块
    mod = get_layer1_module("crops")     # 返回 CROPS_DETAIL
    mod = get_layer1_module("animals")   # 返回 ANIMALS_DETAIL

    # 层 2：按 id 类型前缀查找模块
    mod = get_layer2_module("chest")     # 返回 CHEST_CONTENT
    mod = get_layer2_module("npc")       # 返回 NPC_INSTANCE
"""

from __future__ import annotations

from typing import Optional

from .module_base import ObserveModule
from .layer0_modules import LAYER_0_MODULES
from .layer1_modules import LAYER_1_MODULES
from .layer2_modules import LAYER_2_MODULES


# ── 总注册表 ──────────────────────────────────────────────────────────

ALL_MODULES: dict[str, object] = {
    "layer_0": LAYER_0_MODULES,
    "layer_1": LAYER_1_MODULES,
    "layer_2": LAYER_2_MODULES,
}


# ── 便捷查询函数 ──────────────────────────────────────────────────────

def get_layer1_module(category: str) -> Optional[ObserveModule]:
    """
    根据 detail 类别名（如 "crops"）获取层 1 模块。

    参数：
        category: detail 枚举值，如 "crops", "animals", "npcs",
                  "containers", "inventory", "objects", "exits"

    返回：
        ObserveModule 实例，或 None（不存在时）
    """
    return LAYER_1_MODULES.get(category)


def get_layer2_module(prefix: str) -> Optional[ObserveModule]:
    """
    根据 id 类型前缀（如 "chest"）获取层 2 模块。

    参数：
        prefix: ObserveId 中冒号前的类型前缀，如 "chest", "fridge",
                "shipping_bin", "crop", "animal", "npc"

    返回：
        ObserveModule 实例，或 None（不存在时）
    """
    return LAYER_2_MODULES.get(prefix)


def parse_detail_to_module(detail: str) -> tuple[str, Optional[ObserveModule], Optional[str]]:
    """
    解析 detail 参数，返回（层级标识, 模块, id_rest）三元组。

    解析规则：
    1. detail 为空 → 层 0，无模块
    2. detail 在 LAYER_1_MODULES 键中 → 层 1，对应模块，id_rest=None
    3. detail 含冒号 → 层 2，前缀查模块，id_rest 为冒号后内容
    4. detail == "shipping_bin"（无冒号的层 2 特例） → 层 2，SHIPPING_BIN_CONTENT

    参数：
        detail: observe() 的 detail 参数字符串

    返回：
        (layer_key, module_or_None, id_rest_or_None)
        layer_key: "layer0" | "layer1" | "layer2"
    """
    if not detail:
        return "layer0", None, None

    # 层 1：枚举值
    if detail in LAYER_1_MODULES:
        return "layer1", LAYER_1_MODULES[detail], None

    # 层 2 特殊值：shipping_bin（无冒号）
    if detail == "shipping_bin":
        return "layer2", LAYER_2_MODULES.get("shipping_bin"), None

    # 层 2：含冒号
    if ":" in detail:
        prefix, _, rest = detail.partition(":")
        mod = LAYER_2_MODULES.get(prefix)
        return "layer2", mod, rest

    # 未识别
    return "unknown", None, None


__all__ = [
    "LAYER_0_MODULES",
    "LAYER_1_MODULES",
    "LAYER_2_MODULES",
    "ALL_MODULES",
    "get_layer1_module",
    "get_layer2_module",
    "parse_detail_to_module",
]
