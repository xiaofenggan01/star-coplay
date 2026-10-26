"""
observe Skill — 层 1 子模块（类别详细列表，7 个）

实现规范：observe-l3-skill-spec.md §5
字段名以 QUERY_INTERFACE.md（实际 L1 接口）为准，spec 字段名差异已在注释中标注。

层 1 特点：
- 包含 [ObserveId]（供 LLM 在后续调用中直接引用）
- 超过 max_n 时截断，附"还有 K 个未列出"
- section_title 用【】包裹
"""

from __future__ import annotations

from collections import defaultdict

from .module_base import (
    ObserveModule,
    QuerySpec,
    LOCATION_DISPLAY_NAMES,
    QUALITY_DISPLAY_NAMES,
    get_location_display,
    _items_truncate,
)

MAX_CROPS_PER_GROUP = 5      # spec §5 crops_detail：每组最多 5 条
MAX_OBJECTS_PER_GROUP = 10   # spec §5 ground_objects_detail：每组最多 10 条


# ── 1. crops_detail ────────────────────────────────────────────────────
# spec §5 crops_detail

_CROP_STATE_ORDER = ["ready_to_harvest", "needs_water", "seedling", "withered"]
_CROP_STATE_DISPLAY = {
    "ready_to_harvest": "可收割",
    "needs_water": "需浇水",
    "seedling": "幼苗",
    "withered": "枯萎",
}


def _classify_crop_state(target: dict) -> str:
    """
    从实际接口字段推断作物状态。

    实际接口字段：is_harvestable / is_dead / is_watered / phase
    spec 字段：state 枚举（"ready_to_harvest"/"needs_water"/"seedling"/"withered"）
    """
    if target.get("is_dead"):
        return "withered"
    if target.get("is_harvestable"):
        return "ready_to_harvest"
    if not target.get("is_watered"):
        return "needs_water"
    # 幼苗判断：phase <= 1（spec 用 days_grown <= 2，接口无此字段，用 phase 代替）
    if (target.get("phase") or 0) <= 1:
        return "seedling"
    return "needs_water"  # 已浇水未成熟正常归需浇水段（第二天会变）


def _format_crop_state_display(target: dict, state: str) -> str:
    """
    生成作物状态描述文案。

    spec 模板：
    - ready_to_harvest → "成熟，可收割"
    - needs_water → "第 {days_grown} 天/共 {total_days} 天，需浇水"
    - seedling → "幼苗，第 {days_grown} 天"
    - withered → "枯萎（需清理）"

    实际接口：无 days_grown/total_days，用 phase/max_phase 代替。
    """
    if state == "ready_to_harvest":
        return "成熟，可收割"
    if state == "withered":
        return "枯萎（需清理）"

    phase = target.get("phase", 0)
    max_phase = target.get("max_phase", 0)

    if state == "seedling":
        return f"幼苗，第 {phase + 1} 阶段"
    # needs_water
    if not target.get("is_watered"):
        return f"第 {phase + 1} 阶段/共 {max_phase + 1} 阶段，需浇水"
    return f"第 {phase + 1} 阶段/共 {max_phase + 1} 阶段，生长中"


def _render_crops_detail(data: dict) -> str:
    """
    渲染农场作物详细列表（分组 + 截断）。

    spec 字段：targets[].name / state / days_grown / days_to_harvest / locshort / x / y / target_ref
    实际接口（QUERY_INTERFACE §11a）：targets[].crop_name / is_harvestable / is_dead / is_watered / phase / max_phase / location_name / x / y / target_ref
    注意差异：name → crop_name；无 locshort（需从 alias_map 计算）；无 state（从接口字段推断）。
    """
    crops_wrapper = data.get("crops_data") or {}
    targets = crops_wrapper.get("targets") or []
    alias_map = data.get("alias_map") or {}

    if not targets:
        return "当前地图没有作物。"

    # 分组
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(targets, key=lambda x: (x.get("location_name", ""), x.get("x", 0), x.get("y", 0))):
        state = _classify_crop_state(t)
        groups[state].append(t)

    total = len(targets)
    ready = len(groups.get("ready_to_harvest", []))
    needs_water = len(groups.get("needs_water", []))
    seedling = len(groups.get("seedling", []))
    withered = len(groups.get("withered", []))

    lines = [f"可收割 {ready} / 需浇水 {needs_water} / 幼苗 {seedling}（共 {total} 株）"]

    for state_key in _CROP_STATE_ORDER:
        group = groups.get(state_key, [])
        if not group:
            continue

        state_cn = _CROP_STATE_DISPLAY[state_key]
        lines.append(f"\n{state_cn}（{len(group)} 株）：")

        shown, hidden = _items_truncate(group, MAX_CROPS_PER_GROUP)
        for t in shown:
            crop_name = t.get("crop_name") or t.get("name", "未知作物")
            state_display = _format_crop_state_display(t, state_key)
            location_name = t.get("location_name", "")
            locshort = alias_map.get(location_name, location_name.lower())
            x = t.get("x", 0)
            y = t.get("y", 0)
            lines.append(f"- {crop_name}，{state_display} [crop:{locshort}_{x}_{y}]")

        if hidden > 0:
            lines.append(f"（…共 {len(group)} 株，仅显示前 {MAX_CROPS_PER_GROUP} 条）")

    return "\n".join(lines)


CROPS_DETAIL = ObserveModule(
    name="crops_detail",
    layer=1,
    section_title="【农场作物列表】",
    queries=[QuerySpec("find_targets", {"type": "crop"}, result_key="crops_data")],
    render=_render_crops_detail,
)


# ── 2. animals_detail ─────────────────────────────────────────────────
# spec §5 animals_detail

# 动物类型英文→中文映射
_ANIMAL_TYPE_DISPLAY = {
    "Chicken": "鸡",
    "Cow": "牛",
    "Pig": "猪",
    "Sheep": "羊",
    "Goat": "山羊",
    "Rabbit": "兔",
    "Duck": "鸭",
    "Dinosaur": "恐龙",
    "Ostrich": "鸵鸟",
    "Horse": "马",
}


def _render_animals_detail(data: dict) -> str:
    """
    渲染动物详细列表（按地图分组）。

    spec 字段：targets[].name / display_type / locshort / needs_pet / needs_feed / mood / hearts / unique_id
    实际接口（QUERY_INTERFACE §11b）：animals[].name / display_name / animal_type / location_name / was_petted / friendship / target_ref
    注意差异：
    - 动物列表在 "animals" 键（不是 "targets"）
    - display_type → animal_type（英文，需查映射表）
    - needs_pet → !was_petted
    - hearts → friendship // 250（1000 满）
    - 实际接口无 needs_feed / mood / unique_id 字段
    """
    animals_wrapper = data.get("animals_data") or {}
    animals = animals_wrapper.get("animals") or animals_wrapper.get("targets") or []
    alias_map = data.get("alias_map") or {}

    if not animals:
        return "当前没有圈养动物。"

    # 检测重名（同名动物需要 #shortuid 消歧）
    name_counts: dict[str, int] = defaultdict(int)
    for a in animals:
        name_counts[a.get("name", "")] += 1

    # 按地图分组
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in animals:
        loc = a.get("location_name", "")
        groups[loc].append(a)

    lines = []
    for loc, group in sorted(groups.items()):
        locshort = alias_map.get(loc, loc.lower())
        loc_display = get_location_display(loc, alias_map)
        lines.append(f"\n{loc_display}（{locshort}）：")

        for a in group:
            name = a.get("name") or a.get("display_name", "未知")
            animal_type_en = a.get("animal_type", "")
            display_type = _ANIMAL_TYPE_DISPLAY.get(animal_type_en, animal_type_en)

            # needs_pet
            was_petted = a.get("was_petted", True)
            needs_pet = not was_petted

            # hearts（实际接口：friendship，250/心，0-1000）
            friendship = a.get("friendship", 0)
            hearts = friendship // 250

            # 状态显示
            status_parts = []
            if needs_pet:
                status_parts.append("需爱抚")
            if not status_parts:
                status_parts.append("今日已完成")
            # 实际接口无 mood 字段，跳过

            status_display = "，".join(status_parts)

            # 重名消歧（取 target_ref 末 6 位作为 shortuid）
            observe_id_name = name
            if name_counts.get(name, 0) > 1:
                target_ref = a.get("target_ref", "")
                shortuid = target_ref[-6:] if len(target_ref) >= 6 else "000000"
                observe_id_name = f"{name}#{shortuid}"

            lines.append(f"- {name}（{display_type}），{status_display}，♥{hearts}/5 [animal:{observe_id_name}]")

    return "\n".join(lines)


ANIMALS_DETAIL = ObserveModule(
    name="animals_detail",
    layer=1,
    section_title="【动物状态列表】",
    queries=[QuerySpec("find_targets", {"type": "animal"}, result_key="animals_data")],
    render=_render_animals_detail,
)


# ── 3. npcs_detail ────────────────────────────────────────────────────
# spec §5 npcs_detail

def _render_npcs_detail(data: dict) -> str:
    """
    渲染当前地图 NPC 详细列表。

    spec 字段：targets[].display_name / description / x / y / friendship_hearts / gift_given_today
    实际接口（QUERY_INTERFACE §11c）：targets[].display_name / hearts / x / y / talked_today
    注意差异：
    - 实际接口无 description（职业简介）
    - 实际接口无 gift_given_today（但有 talked_today）
    - friendship_hearts → hearts（字段名不同）
    """
    npcs_wrapper = data.get("npcs_data") or {}
    targets = npcs_wrapper.get("targets") or []

    if not targets:
        return "当前地图没有 NPC。"

    lines = []
    for t in targets:
        display_name = t.get("display_name") or t.get("name", "未知")
        name = t.get("name", display_name)  # 英文名用于 ObserveId
        x = t.get("x", 0)
        y = t.get("y", 0)
        hearts = t.get("hearts", 0)

        # 实际接口无 description，省略
        # 实际接口无 gift_given_today，用 talked_today 替代（语义不完全相同）
        # talked_today=True 表示今日已对话（非送礼）
        talked_today = t.get("talked_today", False)

        line = f"- {display_name}，在 [{x},{y}]，♥{hearts} [npc:{name}]"
        if talked_today:
            line += "（今日已对话）"

        lines.append(line)

    return "\n".join(lines)


NPCS_DETAIL = ObserveModule(
    name="npcs_detail",
    layer=1,
    section_title="【当前地图 NPC】",
    queries=[QuerySpec("find_targets", {"type": "npc"}, result_key="npcs_data")],
    render=_render_npcs_detail,
)


# ── 4. containers_list ────────────────────────────────────────────────
# spec §5 containers_list

_CONTAINER_TYPE_DISPLAY = {
    "chest": "木箱",
    "fridge": "冰箱",
    "shipping_bin": "运输箱",
}


def _render_containers_list(data: dict) -> str:
    """
    渲染容器列表（含 ObserveId）。

    spec 字段：containers[].container_id / type / locshort / x / y / item_count / capacity
    实际接口（QUERY_INTERFACE §9）：containers[].id / type / location / tile_x / tile_y / item_count / capacity
    注意差异：
    - container_id → id（字段名不同）
    - locshort → 需从 alias_map + location 计算
    - x / y → tile_x / tile_y（字段名不同）
    """
    containers_wrapper = data.get("containers_data") or {}
    containers = containers_wrapper.get("containers") or []
    alias_map = data.get("alias_map") or {}

    if not containers:
        return "当前地图没有容器。"

    lines = []
    for c in containers:
        ctype = c.get("type", "chest")
        type_display = _CONTAINER_TYPE_DISPLAY.get(ctype, ctype)
        item_count = c.get("item_count")
        capacity = c.get("capacity", 36)
        x = c.get("tile_x", c.get("x", 0))
        y = c.get("tile_y", c.get("y", 0))
        location = c.get("location", "")
        locshort = alias_map.get(location, location.lower())

        # 生成 ObserveId
        if ctype == "chest":
            observe_id = f"[chest:{locshort}_{x}_{y}]"
            count_display = f"{item_count}/{capacity} 格已用" if item_count is not None else "? 格已用"
            line = f"- {type_display}，{count_display}，位于 ({x},{y}) {observe_id}"
        elif ctype == "fridge":
            observe_id = f"[fridge:{locshort}]"
            count_display = f"{item_count}/{capacity} 格已用" if item_count is not None else "? 格已用"
            line = f"- {type_display}，{count_display}，位于农舍 {observe_id}"
        elif ctype == "shipping_bin":
            observe_id = "[shipping_bin]"
            item_display = f"{item_count} 种物品待运" if item_count is not None else "? 种物品"
            line = f"- {type_display}，{item_display} {observe_id}"
        else:
            count_display = f"{item_count}/{capacity} 格已用" if item_count is not None else "? 格已用"
            line = f"- {type_display}，{count_display}"

        lines.append(line)

    return "\n".join(lines)


CONTAINERS_LIST = ObserveModule(
    name="containers_list",
    layer=1,
    section_title="【容器列表】",
    queries=[QuerySpec("query_containers", result_key="containers_data")],
    render=_render_containers_list,
)


# ── 5. inventory_detail ───────────────────────────────────────────────
# spec §5 inventory_detail

# 物品分类映射（基于 item_id 前缀和 name 推断）
_CATEGORY_DISPLAY = {
    "Seeds": "种子",
    "Tool": "工具",
    "Weapon": "武器",
    "Mineral": "矿物",
    "Crop": "作物",
}


def _classify_item_category(item: dict) -> str:
    """
    从实际接口字段推断物品分类。

    实际接口字段：item_id（"(T)xxx"=工具, "(W)xxx"=武器）/ name
    spec 字段：category（"Seeds"/"Tool"/"Weapon"/"Mineral"/"Crop"/"其他"）
    """
    item_id = item.get("item_id", "")
    name = item.get("name", "")

    if item_id.startswith("(T)"):
        return "工具"
    if item_id.startswith("(W)"):
        return "武器"
    if item_id.startswith("(B)") or item_id.startswith("(BC)"):
        return "制作品"

    name_lower = name.lower()
    if "seed" in name_lower or "种子" in name:
        return "种子"
    if "ore" in name_lower or "coal" in name_lower or "bar" in name_lower or "矿石" in name or "锭" in name:
        return "矿物"

    return "其他"


def _render_inventory_detail(data: dict) -> str:
    """
    渲染背包完整物品列表（按分类分组）。

    spec 字段：items[].display_name / count / quality / category；used_slots / capacity
    实际接口（QUERY_INTERFACE §2）：items[].name / stack / quality / item_id；slots_used / slots_total
    注意差异：display_name → name；count → stack；slots_used/slots_total（有差异）
    """
    inv_wrapper = data.get("inventory") or {}
    if "inventory" in inv_wrapper and isinstance(inv_wrapper["inventory"], dict):
        inv = inv_wrapper["inventory"]
    else:
        inv = inv_wrapper

    used_slots = inv.get("slots_used", 0)
    capacity = inv.get("slots_total", 36)
    items = inv.get("items") or []

    if not items:
        return f"背包是空的。（{capacity} 格可用）"

    # 按分类分组
    groups: dict[str, list[dict]] = defaultdict(list)
    category_order = ["工具", "武器", "种子", "矿物", "制作品", "作物", "其他"]

    for item in items:
        cat = _classify_item_category(item)
        groups[cat].append(item)

    lines = [f"（{used_slots}/{capacity} 格已用）"]
    for cat in category_order:
        group = groups.get(cat, [])
        if not group:
            continue
        lines.append(f"\n{cat}：")
        for item in group:
            name = item.get("name", "未知")
            stack = item.get("stack", 1)
            quality = item.get("quality", 0)
            quality_display = QUALITY_DISPLAY_NAMES.get(quality, "")

            if stack > 1:
                lines.append(f"- {name}×{stack}{quality_display}")
            else:
                lines.append(f"- {name}{quality_display}")

    return "\n".join(lines)


INVENTORY_DETAIL = ObserveModule(
    name="inventory_detail",
    layer=1,
    section_title="【背包物品】",
    queries=[QuerySpec("query_inventory", result_key="inventory")],
    render=_render_inventory_detail,
)


# ── 6. ground_objects_detail ──────────────────────────────────────────
# spec §5 ground_objects_detail

def _render_ground_objects_detail(data: dict) -> str:
    """
    渲染地面可互动对象详细列表（按 sub_type 分组，每组最多 10 条）。

    spec 字段：targets[].sub_type / display_name / locshort / x / y / target_ref
    实际接口（QUERY_INTERFACE §11d）：targets[].name / display_name / object_type / target_ref / x / y / location_name
    注意差异：sub_type 需从接口字段推断（见 layer0 的 _classify_object_sub_type）。
    """
    objects_wrapper = data.get("objects_data") or {}
    targets = objects_wrapper.get("targets") or []
    alias_map = data.get("alias_map") or {}

    if not targets:
        return "当前地图没有可互动对象。"

    # sub_type → ObserveId 前缀映射（spec §5）
    sub_type_prefix = {
        "rock": "rock",
        "tree": "tree",
        "twig": "twig",
        "weed": "weed",
        "artifact_spot": "object",
    }
    sub_type_display = {
        "rock": "石头",
        "tree": "大树",
        "twig": "树枝",
        "weed": "杂草",
        "artifact_spot": "挖掘点",
        "object": "对象",
    }
    display_order = ["rock", "twig", "weed", "tree", "artifact_spot", "object"]

    # 分组（从 layer0 模块导入分类函数）
    from .layer0_modules import _classify_object_sub_type

    groups: dict[str, list[dict]] = defaultdict(list)
    for t in targets:
        sub = t.get("sub_type") or _classify_object_sub_type(t)
        groups[sub].append(t)

    lines = []
    for sub in display_order:
        group = groups.get(sub, [])
        if not group:
            continue

        sub_cn = sub_type_display.get(sub, sub)
        lines.append(f"\n{sub_cn}（{len(group)} 个）：")

        shown, hidden = _items_truncate(group, MAX_OBJECTS_PER_GROUP)
        for t in shown:
            display_name = t.get("display_name") or t.get("name", "未知")
            location_name = t.get("location_name", "")
            locshort = alias_map.get(location_name, location_name.lower())
            x = t.get("x", 0)
            y = t.get("y", 0)
            prefix = sub_type_prefix.get(sub, "object")
            observe_id = f"[{prefix}:{locshort}_{x}_{y}]"
            lines.append(f"- {display_name} {observe_id}")

        if hidden > 0:
            lines.append(f"（…共 {len(group)} 个，仅显示前 {MAX_OBJECTS_PER_GROUP} 条）")

    return "\n".join(lines) if lines else "当前地图没有可互动对象。"


GROUND_OBJECTS_DETAIL = ObserveModule(
    name="ground_objects_detail",
    layer=1,
    section_title="【地面可互动对象】",
    queries=[QuerySpec("find_targets", {"type": "object"}, result_key="objects_data")],
    render=_render_ground_objects_detail,
)


# ── 7. exits_detail ───────────────────────────────────────────────────
# spec §5 exits_detail
# 注：实际接口无 query_exits capability（QUERY_INTERFACE §12 末尾注）
# 使用 query_location_info（warps 字段）代替
# Wave 2B（collectors.py）负责处理这个特殊情况
# 这里的 render 函数按 spec 期望的 data 格式实现

_DIRECTION_CN = {
    "north": "北",
    "south": "南",
    "east": "东",
    "west": "西",
    "door": "（门）",
}


def _render_exits_detail(data: dict) -> str:
    """
    渲染地图出口列表。

    spec 字段：exits[].target_location / display_name / direction / exit_x / exit_y
    实际接口（QUERY_INTERFACE 末尾注）：query_exits 暂无专用 capability，
      使用 query_location_info（取 warps 字段）。
    data["exits_data"] 由 collectors.py 组装（从 location_info.warps 转换）。
    """
    exits_wrapper = data.get("exits_data") or {}
    exits = exits_wrapper.get("exits") or []
    alias_map = data.get("alias_map") or {}

    if not exits:
        return "当前地图没有可用出口。（可能是内部地图）"

    lines = []
    for e in exits:
        target_location = e.get("target_location", "")
        display_name = e.get("display_name") or get_location_display(target_location, alias_map)
        direction = e.get("direction", "")
        direction_cn = _DIRECTION_CN.get(direction, f"（{direction}）" if direction else "")

        locshort = alias_map.get(target_location, target_location.lower())
        observe_id = f"[exit:{locshort}]"

        direction_part = f"（{direction_cn}）" if direction_cn else ""
        lines.append(f"- → {display_name}{direction_part} {observe_id}")

    return "\n".join(lines)


EXITS_DETAIL = ObserveModule(
    name="exits_detail",
    layer=1,
    section_title="【地图出口】",
    queries=[QuerySpec("query_exits", result_key="exits_data")],
    render=_render_exits_detail,
)


# ── 层 1 注册表 ───────────────────────────────────────────────────────

LAYER_1_MODULES: dict[str, ObserveModule] = {
    "crops":      CROPS_DETAIL,
    "animals":    ANIMALS_DETAIL,
    "npcs":       NPCS_DETAIL,
    "containers": CONTAINERS_LIST,
    "inventory":  INVENTORY_DETAIL,
    "objects":    GROUND_OBJECTS_DETAIL,
    "exits":      EXITS_DETAIL,
}
