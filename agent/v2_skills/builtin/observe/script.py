"""
observe L3 Skill 入口。

三层渐进式披露：
  observe()                          → 概览（层 0）
  observe(detail="crops")            → 类别详情（层 1）
  observe(detail="<opaque ref_id>")  → 单实例详情（层 2，兼容转 inspect）

注册约定（registry.py 扫描）：
  - 本文件中的入口函数名必须与目录名一致，即 observe(ctx, ...)
  - ctx 为第一个参数（SkillContext 或 duck-compatible MockObserveCtx）

层 0 组装规则（spec §9）：
  1. player_status + world_time  合并为一行（" | " 分隔）
  2. inventory_summary           独立一行
  3. farm_summary_* 若至少一个 applicable，前置【农场状态】标题，然后各子行
  4. local_containers_summary    独立一行（applicable 时）
  5. local_npcs_summary          独立一行（applicable 时）
  6. 空字符串段落直接跳过，段落间用 "\\n" 分隔（保持紧凑，spec §9 规则 5）
  7. 末尾追加 hint

hint 嵌入规范（spec §10）：
  - 层 0：有作物→crops / 有动物→animals / 有容器→containers，否则→exits
  - 层 1：按 category 返回对应操作建议
  - 层 2：按 category 前缀返回单实例操作建议
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from l3_query.detail import observe_detail
from l3_query.inspect import inspect_refs
from l3_query.observe import observe_current_view

from .collectors import collect_layer0, collect_layer1, collect_layer2
from .module_base import _truncate, _MAX_TOKENS_LAYER0, _MAX_TOKENS_LAYER1, _MAX_TOKENS_LAYER2
from .modules import (
    LAYER_0_MODULES,
    LAYER_1_MODULES,
    LAYER_2_MODULES,
    get_layer1_module,
    get_layer2_module,
)

log = logging.getLogger("v2_skills.observe")

# 层 0 农场状态标题（spec §9 中的 FARM_GROUP_TITLE）
_FARM_GROUP_TITLE = "【农场状态】"


# ══════════════════════════════════════════════════════════════════════
# Skill 入口（registry.py 通过函数名 "observe" 发现此函数）
# ══════════════════════════════════════════════════════════════════════

async def observe(ctx: Any, detail: str = "") -> dict:
    """
    感知周围环境。

    Args:
        ctx: SkillContext（或 duck-compatible mock），提供 query / get_game_state。
        detail: 查询级别：
            ""                    → 层 0 场景速览
            "crops"/"animals"...  → 层 1 类别详情
            "<opaque ref_id>"      → 层 2 单实例详情

    Returns:
        {"success": bool, "status": str, "observation": str}
        observation 为纯文字，LLM 可直接引用文中 [标识符] 传给后续 Skill。
    """
    detail = (detail or "").strip()

    try:
        if not detail:
            current_view = await observe_current_view(ctx)
            if current_view.get("has_proto_facts"):
                return {
                    "success": bool(current_view.get("success", True)),
                    "status": str(current_view.get("status") or ("ok" if current_view.get("success", True) else "failed")),
                    "observation": current_view["description"],
                    "data": current_view["data"],
                }
            text = await _render_layer0(ctx)
        elif ":" in detail:
            inspected = await _try_render_ref_detail(ctx, detail)
            if inspected is not None:
                return {
                    "success": bool(inspected.get("success", True)),
                    "status": str(inspected.get("status") or ("ok" if inspected.get("success", True) else "failed")),
                    "observation": inspected["description"],
                    "data": inspected["data"],
                    **({"error": inspected["error"]} if inspected.get("error") else {}),
                }
            text = await _render_layer2(ctx, detail)
        else:
            detail_view = await observe_detail(ctx, detail)
            if detail_view.get("has_proto_facts") and detail_view.get("handled"):
                return {
                    "success": True,
                    "status": "ok",
                    "observation": detail_view["description"],
                    "data": detail_view["data"],
                }
            text = await _render_layer1(ctx, detail)

        return {
            "success": True,
            "status": "ok",
            "observation": text,
        }

    except Exception as e:
        log.exception("observe 执行失败 detail=%r", detail)
        return {
            "success": False,
            "status": "failed",
            "observation": f"observe 失败：{e}",
        }


async def _try_render_ref_detail(ctx: Any, detail: str) -> dict | None:
    prefix, _, rest = detail.partition(":")
    if prefix in LAYER_2_MODULES:
        return None
    opaque_ref_prefixes = {"world_entity", "character", "inventory_item", "ui"}
    if prefix not in {"ref", "inspect", *opaque_ref_prefixes}:
        return None
    ref_id = rest if prefix in {"ref", "inspect"} else detail
    if not ref_id.strip():
        return None
    try:
        return await inspect_refs(ctx, [ref_id.strip()])
    except Exception:
        log.debug("observe ref detail fallback to legacy detail=%r", detail, exc_info=True)
        return None


# ══════════════════════════════════════════════════════════════════════
# 层 0 编排
# ══════════════════════════════════════════════════════════════════════

async def _render_layer0(ctx: Any) -> str:
    """
    组装层 0 概览文本。

    组装规则（spec §9）：
    1. player_status + world_time  用 " | " 合并（均始终渲染）
    2. inventory_summary           单独一行
    3. farm_summary_*（crops/animals/objects）：若至少一个 applicable 且有内容，
       先追加【农场状态】标题，再逐行追加各子行
    4. local_containers_summary    applicable 时单独一行
    5. local_npcs_summary          applicable 时单独一行
    6. 空字符串跳过；段落间用 "\\n" 分隔（紧凑，非 \\n\\n）
    7. 全文截断到 _MAX_TOKENS_LAYER0 后追加 hint
    """
    data = await collect_layer0(ctx)
    parts: list[str] = []

    # 找出各模块（按注册表顺序，通过 name 匹配）
    _modules_by_name = {m.name: m for m in LAYER_0_MODULES}
    player_status_mod = _modules_by_name.get("player_status")
    world_time_mod = _modules_by_name.get("world_time")
    inventory_summary_mod = _modules_by_name.get("inventory_summary")
    farm_crops_mod = _modules_by_name.get("farm_summary_crops")
    farm_animals_mod = _modules_by_name.get("farm_summary_animals")
    farm_objects_mod = _modules_by_name.get("farm_summary_objects")
    containers_mod = _modules_by_name.get("local_containers_summary")
    npcs_mod = _modules_by_name.get("local_npcs_summary")

    # ── 段 1：玩家状态 + 世界时间（" | " 合并）──
    status_parts: list[str] = []
    if player_status_mod:
        rendered = player_status_mod.safe_render(data)
        if rendered:
            status_parts.append(rendered)
    if world_time_mod:
        rendered = world_time_mod.safe_render(data)
        if rendered:
            status_parts.append(rendered)
    if status_parts:
        parts.append(" | ".join(status_parts))

    # ── 段 2：背包摘要 ──
    if inventory_summary_mod:
        inv = inventory_summary_mod.safe_render(data)
        if inv:
            parts.append(inv)

    # ── 段 3：农场状态（有内容时才加标题）──
    farm_modules = [m for m in [farm_crops_mod, farm_animals_mod, farm_objects_mod] if m]
    farm_parts: list[str] = []
    for m in farm_modules:
        if m.applicable(data):
            rendered = m.safe_render(data)
            if rendered:
                farm_parts.append(rendered)
    if farm_parts:
        parts.append(_FARM_GROUP_TITLE)
        parts.extend(farm_parts)

    # ── 段 4：容器摘要 ──
    if containers_mod and containers_mod.applicable(data):
        rendered = containers_mod.safe_render(data)
        if rendered:
            parts.append(rendered)

    # ── 段 5：NPC 摘要 ──
    if npcs_mod and npcs_mod.applicable(data):
        rendered = npcs_mod.safe_render(data)
        if rendered:
            parts.append(rendered)

    text = "\n".join(parts)
    text = _truncate(text, _MAX_TOKENS_LAYER0)
    text += _hint_layer0(data)
    return text


# ══════════════════════════════════════════════════════════════════════
# 层 1 编排
# ══════════════════════════════════════════════════════════════════════

async def _render_layer1(ctx: Any, category: str) -> str:
    """
    渲染层 1 类别详情。

    Args:
        ctx: Skill 执行上下文
        category: 枚举值（"crops"/"animals"/"npcs"/"containers"/"inventory"/"objects"/"exits"）

    Returns:
        格式化的类别列表文本（末尾含 hint）
    """
    valid_categories = set(LAYER_1_MODULES.keys())
    if category not in valid_categories:
        return (
            f"未知的 detail 值：{category!r}。\n"
            f"可选类别：{' / '.join(sorted(valid_categories))}\n"
            f"单实例查询格式：chest:<id> / fridge:<locshort> / shipping_bin / "
            f"crop:<locshort>_<x>_<y> / animal:<name> / npc:<Name>"
        )

    data = await collect_layer1(ctx, category)
    if data is None:
        # collect 返回 None 表示 category 不在注册表（理论上已被 valid_categories 过滤）
        return f"（{category} 模块未注册）"

    module = get_layer1_module(category)
    if module is None:
        return f"（{category} 模块未注册）"

    if not module.applicable(data):
        return f"{module.section_title}\n（当前没有相关数据）"

    body = module.safe_render(data)
    if not body:
        return f"{module.section_title}\n（当前没有相关数据）"

    body = _truncate(body, _MAX_TOKENS_LAYER1)
    body += _hint_layer1(category, data)
    return body


# ══════════════════════════════════════════════════════════════════════
# 层 2 编排
# ══════════════════════════════════════════════════════════════════════

async def _render_layer2(ctx: Any, detail: str) -> str:
    """
    渲染层 2 单实例详情。

    detail 格式：
      "chest:farm_12_15"    → category="chest",   id_str="farm_12_15"
      "animal:甜豆"          → category="animal",  id_str="甜豆"
      "npc:Lewis"           → category="npc",     id_str="Lewis"
      "shipping_bin"        → 特殊，无冒号（category="shipping_bin", id_str=""）
      "fridge:farmhouse"    → category="fridge",  id_str="farmhouse"
      "crop:farm_12_15"     → category="crop",    id_str="farm_12_15"

    注意：shipping_bin 是 "detail == 'shipping_bin'"（无冒号）路径，
    但 script.py 入口检测 ":" in detail，所以 shipping_bin 走层 1 路径。
    此函数仅处理含冒号的 detail（层 2）。

    Args:
        ctx: Skill 执行上下文
        detail: 含冒号的 detail 字符串

    Returns:
        单实例详情文本（末尾含 hint）
    """
    category, id_str = detail.split(":", 1)

    valid_categories = set(LAYER_2_MODULES.keys())
    if category not in valid_categories:
        return (
            f"未知的单实例类别：{category!r}。\n"
            f"可选前缀：{' / '.join(sorted(valid_categories))}\n"
            f"格式示例：chest:farm_12_15 / animal:甜豆 / npc:Lewis"
        )

    data = await collect_layer2(ctx, category, id_str)
    if data is None:
        return f"（{category} 模块未注册）"

    module = get_layer2_module(category)
    if module is None:
        return f"（{category} 模块未注册）"

    if not module.applicable(data):
        return f"未找到 {detail!r}（实体不存在或已消失）"

    body = module.safe_render(data)
    if not body:
        return f"未找到 {detail!r}（实体不存在或已消失）"

    body = _truncate(body, _MAX_TOKENS_LAYER2)
    body += _hint_layer2(category, id_str)
    return body


# ══════════════════════════════════════════════════════════════════════
# hint 生成（spec §10）
# ══════════════════════════════════════════════════════════════════════

def _hint_layer0(data: dict) -> str:
    """
    根据当前数据动态生成层 0 末尾 hint（spec §10）。

    优先级：
    1. 有未浇水/可收割作物 → crops
    2. 有未抚摸动物       → animals
    3. 有容器             → containers
    4. 均无则             → exits
    最多 3 条；格式：反引号包裹工具调用，" / " 分隔。

    Returns:
        "\\n\\n> 下一步：..." 格式字符串
    """
    hints: list[str] = []

    # 从 find_targets 结果（层 0 采集后以 capability 名为 key）
    crops_result = data.get("find_targets", {}) if False else None  # 层 0 的 find_targets 按 type 区分
    # 注意：层 0 里 find_targets 被多个模块共享（crop/animal/npc/object），
    # collect_for_modules 按 (capability, params) 去重后存储，key 为 capability 名。
    # 由于 find_targets 有多个不同 params，实际上 data 中只会存最后一次 gather 结果。
    # 因此层 0 hint 判断应从 game_state.farm_summary 读取聚合数据，更可靠。

    game_state = data.get("game_state") or {}
    farm_summary = game_state.get("farm_summary") or {}

    # 作物：有未浇水或可收割
    crops_info = farm_summary.get("crops") or {}
    unwatered = crops_info.get("unwatered_count", 0) or 0
    harvestable = crops_info.get("harvestable_count", 0) or 0
    if unwatered > 0 or harvestable > 0:
        hints.append('`observe(detail="crops")` 查看作物详情')

    # 动物：有未抚摸
    animals_info = farm_summary.get("animals") or {}
    unpetted = animals_info.get("unpetted", 0) or animals_info.get("ungreeted_count", 0) or 0
    if unpetted > 0:
        hints.append('`observe(detail="animals")` 查看动物状态')

    # 容器：有任意容器
    containers_result = data.get("query_containers") or {}
    containers_list = containers_result.get("containers", []) if isinstance(containers_result, dict) else []
    if containers_list:
        hints.append('`observe(detail="containers")` 查看容器列表')

    # 兜底：若无以上任何提示，推荐查看出口
    if not hints:
        hints.append('`observe(detail="exits")` 查看可前往地点')

    return "\n\n> 下一步：" + " / ".join(hints[:3])


def _hint_layer1(category: str, data: dict) -> str:
    """
    层 1 末尾 hint（spec §10）。

    Args:
        category: detail 枚举值
        data: 采集数据（当前未用，保留参数供未来动态 hint 扩展）

    Returns:
        "\\n\\n> 提示：..." 格式字符串，无对应类别时返回空字符串
    """
    _hints: dict[str, str] = {
        "crops":      "若要浇水：`water_crops()`；若要收割：`harvest_crops()`",
        "animals":    "若要喂食/爱抚：`skill:pet_animals()`",
        "npcs":       "若要对话：先用 `find_interactions()` 获取候选；只有 `execution_gate.status=\"ready\"` 时才按 `recommended_steps` 调用移动与交互工具",
        "containers": "若要查看某个容器详情：使用返回的 `ref_id` 调用 `inspect(ref_id=...)`",
        "inventory":  "若要存入箱子：`store_to_container(container_id=..., items=[...])`",
        "objects":    "若要清理：`skill:clear_farm()`；若要采集：`execute_on_targets`",
        "exits":      "若要前往：使用返回的出口/传送点坐标调用 `go_to(map_id=..., x=..., y=..., arrival_mode=...)`",
    }
    hint_text = _hints.get(category, "")
    if not hint_text:
        return ""
    return f"\n\n> 提示：{hint_text}"


def _hint_layer2(category: str, rest: Optional[str] = None) -> str:
    """
    层 2 末尾 hint（spec §10）。

    Args:
        category: 实例类别前缀（"chest"/"fridge"/"shipping_bin"/"crop"/"animal"/"npc"）
        rest: 实例 ID（当前未用，保留参数供未来动态 hint 扩展）

    Returns:
        "\\n\\n> 提示：..." 格式字符串，无对应类别时返回空字符串
    """
    _hints: dict[str, str] = {
        "chest": (
            "取出物品：`take_from_container(container_id=..., item_name=...)`\n"
            "存入：`store_to_container(...)`"
        ),
        "fridge": (
            "冰箱内容物可用于烹饪。"
            "取出：`take_from_container(container_id='FarmHouse:fridge_0_0', ...)`"
        ),
        "shipping_bin": (
            "运输箱中的物品过夜自动出售。"
            "可补充：`store_to_container(container_id='shipping_bin', ...)`"
        ),
        "crop":   "若可收割：`harvest_crops()`；若需浇水：`water_crops()`",
        "animal": "若需爱抚：`skill:pet_animals()`",
        "npc":    "若要对话或送礼：先用 `find_interactions()` 确认可交互位置；先看 `execution_gate`/`execution_warnings`，再决定是否执行 schema-valid 工具步骤",
    }
    hint_text = _hints.get(category, "")
    if not hint_text:
        return ""
    return f"\n\n> 提示：{hint_text}"
