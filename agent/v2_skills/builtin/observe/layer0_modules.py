"""
observe Skill — 层 0 子模块（场景速览，8 个）

实现规范：observe-l3-skill-spec.md §4
字段名以 QUERY_INTERFACE.md（实际 L1 接口）为准，spec 字段名差异已在注释中标注。
"""

from __future__ import annotations

from .module_base import (
    ObserveModule,
    QuerySpec,
    LOCATION_DISPLAY_NAMES,
    WEATHER_DISPLAY_NAMES,
    SEASON_DISPLAY_NAMES,
    DAY_OF_WEEK_DISPLAY_NAMES,
    QUALITY_DISPLAY_NAMES,
    format_time,
    get_location_display,
)


# ── 1. player_status ──────────────────────────────────────────────────
# 依赖：game_state（Blackboard 同步读取，不走 IPC）
# spec §4 player_status

def _render_player_status(data: dict) -> str:
    """
    渲染玩家状态首段。

    spec 字段：player.stamina / maxStamina / currentTool
    实际接口（QUERY_INTERFACE §1）：player.energy.current / energy.max / equipped_item.name
    注意差异：接口无直接的 stamina 字段，能量信息在 energy 子字典中。
    """
    gs = data.get("game_state") or {}
    player = gs.get("player") or {}
    alias_map = data.get("alias_map") or {}

    location_raw = player.get("location", "")
    location_display = get_location_display(location_raw, alias_map)
    locshort = alias_map.get(location_raw, location_raw.lower() if location_raw else "未知")

    # 实际接口：energy 子字典
    energy = player.get("energy") or {}
    stamina = energy.get("current", "?")
    max_stamina = energy.get("max", "?")

    # 实际接口：equipped_item.name（spec 用 currentTool）
    equipped = player.get("equipped_item") or {}
    tool_name = equipped.get("name") if isinstance(equipped, dict) else None
    tool_display = tool_name if tool_name else "（空手）"

    if isinstance(stamina, float):
        stamina = f"{stamina:.0f}"
    if isinstance(max_stamina, float):
        max_stamina = f"{max_stamina:.0f}"

    return f"你现在在{location_display}（{locshort}），体力 {stamina}/{max_stamina}，手持：{tool_display}。"


PLAYER_STATUS = ObserveModule(
    name="player_status",
    layer=0,
    section_title="",
    queries=[QuerySpec("query_game_state", result_key="game_state")],
    render=_render_player_status,
    visibility=lambda data: True,
)


# ── 2. world_time ─────────────────────────────────────────────────────
# 依赖：game_state（与 player_status 共享同一次调用）
# spec §4 world_time

def _render_world_time(data: dict) -> str:
    """
    渲染世界时间段。

    spec 字段：world.day / dayOfWeek / time / weather
    实际接口（QUERY_INTERFACE §1）：world.day / day_of_week / time / weather.current
    注意差异：weather 是子字典，天气字段在 weather.current；day_of_week 下划线命名。
    """
    gs = data.get("game_state") or {}
    world = gs.get("world") or {}

    season = world.get("season", "spring")
    day = world.get("day", 1)
    # 实际接口用 day_of_week（下划线），spec 用 dayOfWeek
    day_of_week = world.get("day_of_week") or world.get("dayOfWeek", "")
    game_time = world.get("time", 600)

    # 实际接口：weather 是子字典，spec 直接 weather 字符串
    weather_raw = world.get("weather") or {}
    if isinstance(weather_raw, dict):
        weather_str = weather_raw.get("current", "sunny")
    else:
        weather_str = str(weather_raw)

    season_cn = SEASON_DISPLAY_NAMES.get(season, season)
    dow_cn = DAY_OF_WEEK_DISPLAY_NAMES.get(day_of_week, day_of_week)
    time_display = format_time(game_time) if isinstance(game_time, int) else str(game_time)
    weather_cn = WEATHER_DISPLAY_NAMES.get(weather_str, weather_str)

    dow_part = f"，{dow_cn}" if dow_cn else ""
    return f"{season_cn} {day} 日{dow_part}，{time_display}，天气：{weather_cn}"


WORLD_TIME = ObserveModule(
    name="world_time",
    layer=0,
    section_title="",
    queries=[QuerySpec("query_game_state", result_key="game_state")],
    render=_render_world_time,
    visibility=lambda data: True,
)


# ── 3. inventory_summary ──────────────────────────────────────────────
# 依赖：query_inventory
# spec §4 inventory_summary

def _render_inventory_summary(data: dict) -> str:
    """
    渲染背包摘要（最多前 4 种物品）。

    spec 字段：items[].name / count / quality；used_slots / capacity
    实际接口（QUERY_INTERFACE §2）：items[].name / stack / quality；slots_used / slots_total
    注意差异：count → stack；used_slots → slots_used；capacity → slots_total
    """
    inv_wrapper = data.get("inventory") or {}
    # 实际接口 inventory 是嵌套的：{"type": "inventory", "inventory": {...}}
    # 但注册表里 result_key="inventory"，所以 data["inventory"] 就是查询结果根对象
    # 兼容两种情况：直接是 slots_used 字典，或嵌套在 inventory 键下
    if "inventory" in inv_wrapper and isinstance(inv_wrapper["inventory"], dict):
        inv = inv_wrapper["inventory"]
    else:
        inv = inv_wrapper

    # 实际接口字段
    used_slots = inv.get("slots_used", 0)
    capacity = inv.get("slots_total", 36)
    items = inv.get("items") or []

    if not items:
        return f"背包（{used_slots}/{capacity} 格）：（空）"

    # 按 stack（数量）降序排列，取前 4
    sorted_items = sorted(items, key=lambda x: x.get("stack", 0), reverse=True)
    top4 = sorted_items[:4]
    total = len(items)

    parts = []
    for item in top4:
        name = item.get("name", "未知")
        stack = item.get("stack", 1)
        if stack > 1:
            parts.append(f"{name}×{stack}")
        else:
            parts.append(name)

    items_display = "，".join(parts)
    if total > 4:
        items_display += f"…共 {total} 种"

    return f"背包（{used_slots}/{capacity} 格）：{items_display}"


INVENTORY_SUMMARY = ObserveModule(
    name="inventory_summary",
    layer=0,
    section_title="",
    queries=[QuerySpec("query_inventory", result_key="inventory")],
    render=_render_inventory_summary,
    visibility=lambda data: True,
)


# ── 4. farm_summary_crops ─────────────────────────────────────────────
# 依赖：find_targets (type="crop")
# spec §4 farm_summary_crops

def _render_farm_summary_crops(data: dict) -> str:
    """
    渲染作物聚合摘要。

    spec 字段：targets[].state（"ready_to_harvest"/"needs_water"/"seedling"/"withered"）
    实际接口（QUERY_INTERFACE §11a）：targets[].is_harvestable / is_dead / is_watered / phase
    注意差异：没有 state 枚举，需从 is_harvestable/is_dead/is_watered 推断状态。
    seedling 判断：phase <= 1（无 days_grown 字段，用 phase 代替）
    """
    crops_wrapper = data.get("crops_data") or {}
    targets = crops_wrapper.get("targets") or []

    if not targets:
        return ""

    ready = 0
    needs_water = 0
    seedling = 0
    withered = 0

    for t in targets:
        if t.get("is_dead"):
            withered += 1
        elif t.get("is_harvestable"):
            ready += 1
        elif not t.get("is_watered"):
            needs_water += 1
        elif (t.get("phase") or 0) <= 1:
            seedling += 1
        else:
            # 已浇水且未成熟的正常生长作物，归入"需浇水"之外统计
            # 按 spec 逻辑：非 ready/withered/seedling 的默认归 needs_water（今日已浇水则不算）
            pass

    total = len(targets)
    result = f"作物 {total} 株：可收割 {ready} / 需浇水 {needs_water} / 幼苗 {seedling}"
    if withered > 0:
        result += f" / 枯萎 {withered}（需清理）"
    return result


FARM_SUMMARY_CROPS = ObserveModule(
    name="farm_summary_crops",
    layer=0,
    section_title="【农场状态】",
    queries=[QuerySpec("find_targets", {"type": "crop"}, result_key="crops_data")],
    render=_render_farm_summary_crops,
    visibility=lambda data: len((data.get("crops_data") or {}).get("targets") or []) > 0,
)


# ── 5. farm_summary_animals ───────────────────────────────────────────
# 依赖：find_targets (type="animal")
# spec §4 farm_summary_animals

def _render_farm_summary_animals(data: dict) -> str:
    """
    渲染动物聚合摘要。

    spec 字段：targets[].needs_pet / needs_feed / location_name
    实际接口（QUERY_INTERFACE §11b）：animals[].was_petted / location_name（无 needs_feed/mood）
    注意差异：
    - 动物返回键是 "animals"（不是 "targets"）
    - 没有 needs_feed 字段，用 was_petted 的反值表示需爱抚
    - 没有 mood 字段
    """
    animals_wrapper = data.get("animals_data") or {}
    # 实际接口：动物列表在 "animals" 键（不是 "targets"）
    animals = animals_wrapper.get("animals") or animals_wrapper.get("targets") or []

    if not animals:
        return ""

    total = len(animals)
    needs_pet = sum(1 for a in animals if not a.get("was_petted", True))

    # 收集动物所在地图（locshort 或 location_name 的短名）
    alias_map = data.get("alias_map") or {}
    locations = set()
    for a in animals:
        loc = a.get("location_name", "")
        locshort = alias_map.get(loc, loc.split("-")[0].lower() if "-" in loc else loc.lower())
        if locshort:
            locations.add(locshort)
    location_display = "、".join(sorted(locations)) if locations else "未知"

    # 实际接口无 needs_feed，暂不统计（降级处理）
    return f"动物 {total} 只（{location_display}）：需爱抚 {needs_pet} / 需喂食 ?"


FARM_SUMMARY_ANIMALS = ObserveModule(
    name="farm_summary_animals",
    layer=0,
    section_title="",
    queries=[QuerySpec("find_targets", {"type": "animal"}, result_key="animals_data")],
    render=_render_farm_summary_animals,
    visibility=lambda data: len(
        (data.get("animals_data") or {}).get("animals") or
        (data.get("animals_data") or {}).get("targets") or []
    ) > 0,
)


# ── 6. farm_summary_objects ───────────────────────────────────────────
# 依赖：find_targets (type="object")
# spec §4 farm_summary_objects

# sub_type 分类规则（基于实际接口 object_type/category 字段推断）
_OBJECT_TYPE_DISPLAY = {
    "rock": "石头",
    "twig": "树枝",
    "weed": "杂草",
    "tree": "大树",
    "artifact_spot": "挖掘点",
}

def _classify_object_sub_type(target: dict) -> str:
    """
    根据实际接口字段推断 sub_type。

    实际接口字段：name / object_type / category / item_id
    spec 字段：sub_type（由 Mod 侧计算后返回，但实际接口中不包含此字段）

    推断规则（基于 four-layer-lessons.md SDV API 原则）：
    - object_type == "Litter" → 可能是 rock/twig/weed（根据 name 区分）
    - name == "Stone" 类 → rock
    - name == "Twig" 类 → twig
    - name == "Weeds" 类 → weed
    - target_ref 含 :tree_ → tree
    - target_ref 含 :rock_ → rock
    """
    target_ref = target.get("target_ref", "")
    name = target.get("name", "")
    object_type = target.get("object_type", "")

    # 先检查 target_ref 前缀（最可靠）
    if ":rock_" in target_ref:
        return "rock"
    if ":tree_" in target_ref:
        return "tree"
    if ":debris_" in target_ref:
        # debris 类型包含 twig/weed/grass
        name_lower = name.lower()
        if "twig" in name_lower:
            return "twig"
        if "weed" in name_lower:
            return "weed"
        return "weed"  # 默认归 weed

    # 按 name 判断常见对象
    name_lower = name.lower()
    if "stone" in name_lower or "rock" in name_lower or "boulder" in name_lower:
        return "rock"
    if "twig" in name_lower:
        return "twig"
    if "weed" in name_lower:
        return "weed"
    if "tree" in name_lower:
        return "tree"
    if "artifact" in name_lower:
        return "artifact_spot"

    return "object"


def _render_farm_summary_objects(data: dict) -> str:
    """
    渲染地面对象聚合摘要（仅含数量 > 0 的类别）。

    spec 字段：targets[].sub_type（"rock"/"twig"/"weed"/"tree"/"artifact_spot"）
    实际接口（QUERY_INTERFACE §11d）：targets[].name / object_type / category / target_ref
    注意差异：实际接口无 sub_type 字段，需从其他字段推断。
    """
    objects_wrapper = data.get("objects_data") or {}
    targets = objects_wrapper.get("targets") or []

    if not targets:
        return ""

    counts: dict[str, int] = {}
    for t in targets:
        sub = t.get("sub_type") or _classify_object_sub_type(t)
        counts[sub] = counts.get(sub, 0) + 1

    display_order = ["rock", "twig", "weed", "tree", "artifact_spot"]
    display_names = {
        "rock": "石头",
        "twig": "树枝",
        "weed": "杂草",
        "tree": "大树",
        "artifact_spot": "挖掘点",
    }

    parts = []
    for sub in display_order:
        if counts.get(sub, 0) > 0:
            parts.append(f"{display_names[sub]} {counts[sub]}")
    # 其他未知 sub_type
    other_count = sum(v for k, v in counts.items() if k not in display_order)
    if other_count > 0:
        parts.append(f"其他 {other_count}")

    if not parts:
        return ""
    return "地面对象：" + " / ".join(parts)


FARM_SUMMARY_OBJECTS = ObserveModule(
    name="farm_summary_objects",
    layer=0,
    section_title="",
    queries=[QuerySpec("find_targets", {"type": "object"}, result_key="objects_data")],
    render=_render_farm_summary_objects,
    visibility=lambda data: len((data.get("objects_data") or {}).get("targets") or []) > 0,
)


# ── 7. local_containers_summary ───────────────────────────────────────
# 依赖：query_containers
# spec §4 local_containers_summary

def _render_local_containers_summary(data: dict) -> str:
    """
    渲染容器数量摘要。

    spec 字段：containers[].type
    实际接口（QUERY_INTERFACE §9）：containers[].type（相同！）
    注意：实际接口字段名 id/location/tile_x/tile_y/item_count/capacity 与 spec 略有差异。
    """
    containers_wrapper = data.get("containers_data") or {}
    containers = containers_wrapper.get("containers") or []

    if not containers:
        return ""

    chest_count = sum(1 for c in containers if c.get("type") == "chest")
    fridge_count = sum(1 for c in containers if c.get("type") == "fridge")
    shipping_bin_count = sum(1 for c in containers if c.get("type") == "shipping_bin")

    parts = []
    if chest_count > 0:
        parts.append(f"箱子 {chest_count} 个")
    if fridge_count > 0:
        parts.append(f"冰箱 {fridge_count} 个")
    if shipping_bin_count > 0:
        parts.append(f"运输箱 {shipping_bin_count} 个")

    if not parts:
        return ""
    return "容器：" + " / ".join(parts)


LOCAL_CONTAINERS_SUMMARY = ObserveModule(
    name="local_containers_summary",
    layer=0,
    section_title="",
    queries=[QuerySpec("query_containers", result_key="containers_data")],
    render=_render_local_containers_summary,
    visibility=lambda data: len((data.get("containers_data") or {}).get("containers") or []) > 0,
)


# ── 8. local_npcs_summary ─────────────────────────────────────────────
# 依赖：find_targets (type="npc")
# spec §4 local_npcs_summary

def _render_local_npcs_summary(data: dict) -> str:
    """
    渲染当前地图 NPC 名单（最多 5 人）。

    spec 字段：targets[].display_name（本地化名）
    实际接口（QUERY_INTERFACE §11c）：targets[].display_name（相同！）
    """
    npcs_wrapper = data.get("npcs_data") or {}
    targets = npcs_wrapper.get("targets") or []

    if not targets:
        return ""

    total = len(targets)
    shown = targets[:5]
    names = [t.get("display_name") or t.get("name", "未知") for t in shown]
    name_list = "，".join(names)

    if total > 5:
        name_list += f"…共 {total} 人"

    return f"附近 NPC：{name_list}"


LOCAL_NPCS_SUMMARY = ObserveModule(
    name="local_npcs_summary",
    layer=0,
    section_title="",
    queries=[QuerySpec("find_targets", {"type": "npc"}, result_key="npcs_data")],
    render=_render_local_npcs_summary,
    visibility=lambda data: len((data.get("npcs_data") or {}).get("targets") or []) > 0,
)


# ── 层 0 注册表 ───────────────────────────────────────────────────────

LAYER_0_MODULES: list[ObserveModule] = [
    PLAYER_STATUS,
    WORLD_TIME,
    INVENTORY_SUMMARY,
    FARM_SUMMARY_CROPS,
    FARM_SUMMARY_ANIMALS,
    FARM_SUMMARY_OBJECTS,
    LOCAL_CONTAINERS_SUMMARY,
    LOCAL_NPCS_SUMMARY,
]
