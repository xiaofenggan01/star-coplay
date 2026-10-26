"""
observe Skill — 层 2 子模块（单实例详情，6 个）

实现规范：observe-l3-skill-spec.md §6
字段名以 QUERY_INTERFACE.md（实际 L1 接口）为准，spec 字段名差异已在注释中标注。

层 2 特点：
- 每个模块对应一种单实例查询
- render 函数从 data 中读取 instance_id（由 collectors.py 注入）
- queries 列表为空（参数在运行时动态计算）

参数传递方式：
- collectors.py 在采集时将解析好的 id_str 注入 data["instance_id"]
- 以及将查询结果注入对应的 result_key（如 "container_data"）
"""

from __future__ import annotations

from .module_base import (
    ObserveModule,
    QuerySpec,
    QUALITY_DISPLAY_NAMES,
    get_location_display,
    _parse_loc_xy,
    _resolve_loc,
)


# ── 辅助函数 ──────────────────────────────────────────────────────────

def _quality_display(quality: int) -> str:
    """返回品质显示字符串，0（普通）返回空字符串。"""
    return QUALITY_DISPLAY_NAMES.get(quality, "")


def _format_items_list(items: list[dict]) -> list[str]:
    """
    将物品列表渲染为行列表。

    实际接口字段：name / stack / quality
    spec 字段：display_name / count / quality
    注意差异：display_name → name；count → stack
    """
    lines = []
    for item in items:
        name = item.get("name", "未知")
        stack = item.get("stack", item.get("count", 1))
        quality = item.get("quality", 0)
        quality_str = _quality_display(quality)

        if stack > 1:
            lines.append(f"- {name}×{stack}{quality_str}")
        else:
            lines.append(f"- {name}{quality_str}")
    return lines


# ── 1. chest_content ──────────────────────────────────────────────────
# spec §6 chest_content

def _render_chest_content(data: dict) -> str:
    """
    渲染指定箱子内容。

    data["container_data"] 来自 query_container_items 结果。
    data["instance_id"] = "farm_12_15"（由 collectors.py 注入）

    spec 字段：items[].display_name / count / quality；location_name / x / y / capacity
    实际接口（QUERY_INTERFACE §10）：items[].name / stack / quality / sell_price；
        container.location / container.tile_x / container.tile_y；slots_used / slots_total

    注意差异：
    - display_name → name
    - count → stack（items 字段）
    - container_id / x / y / capacity 在 container 子字典中
    - item_count 在 container_data 根目录（不是 container 子字典）：slots_used / slots_total
    """
    container_data = data.get("container_data") or {}

    # 错误处理
    if container_data.get("error"):
        return "（未找到该箱子，请确认位置是否正确）"

    container = container_data.get("container") or {}
    items = container_data.get("items") or []
    slots_used = container_data.get("slots_used", len(items))
    slots_total = container_data.get("slots_total", 36)

    # 获取位置信息
    alias_map = data.get("alias_map") or {}
    location = container.get("location", "")
    locshort = alias_map.get(location, location.lower())
    x = container.get("tile_x", container.get("x", 0))
    y = container.get("tile_y", container.get("y", 0))

    if not items:
        return f"箱子为空。（{slots_total} 格可用）"

    lines = [f"位置：{locshort} ({x},{y}) | 容量：{slots_used}/{slots_total} 格", "", "物品列表："]
    lines.extend(_format_items_list(items))
    return "\n".join(lines)


CHEST_CONTENT = ObserveModule(
    name="chest_content",
    layer=2,
    section_title="【箱子 {observe_id} 内容】",
    queries=[],  # 参数在运行时计算，collectors.py 负责采集
    render=_render_chest_content,
)


# ── 2. fridge_content ─────────────────────────────────────────────────
# spec §6 fridge_content

def _render_fridge_content(data: dict) -> str:
    """
    渲染冰箱内容。

    data["container_data"] 来自 query_container_items（container_id = "FarmHouse:fridge_0_0"）。

    spec 字段：同 chest_content，无 x/y 字段
    实际接口：同 query_container_items（§10）
    """
    container_data = data.get("container_data") or {}

    if container_data.get("error"):
        error_code = (container_data.get("error") or {}).get("code", "")
        if error_code == "CONTAINER_NOT_FOUND":
            return "（未找到冰箱，当前角色可能不在农舍地图）"
        return "（无法获取冰箱内容）"

    items = container_data.get("items") or []
    slots_used = container_data.get("slots_used", len(items))
    slots_total = container_data.get("slots_total", 36)

    if not items:
        return "冰箱是空的。"

    lines = [f"位置：农舍 | 容量：{slots_used}/{slots_total} 格", "", "物品列表："]
    lines.extend(_format_items_list(items))
    lines.append("\n备注: 冰箱内容物可用于烹饪。")
    return "\n".join(lines)


FRIDGE_CONTENT = ObserveModule(
    name="fridge_content",
    layer=2,
    section_title="【冰箱内容】",
    queries=[],
    render=_render_fridge_content,
)


# ── 3. shipping_bin_content ───────────────────────────────────────────
# spec §6 shipping_bin_content

def _render_shipping_bin_content(data: dict) -> str:
    """
    渲染运输箱（今日待运物品）。

    spec 字段：items[].display_name / count / quality / estimated_gold；total_estimated_gold
    实际接口（QUERY_INTERFACE §10）：items[].name / stack / quality / sell_price
    注意差异：
    - estimated_gold → sell_price（单件价格，需乘以 stack 得到总价）
    - total_estimated_gold 需自行计算（spec 期望 Mod 返回，实际需 Python 侧计算）
    """
    container_data = data.get("container_data") or {}

    if container_data.get("error"):
        return "（无法获取运输箱内容）"

    items = container_data.get("items") or []

    if not items:
        return "运输箱是空的。今日暂无待运物品。"

    lines = []
    total_gold = 0
    has_price = False

    for item in items:
        name = item.get("name", "未知")
        stack = item.get("stack", item.get("count", 1))
        quality = item.get("quality", 0)
        quality_str = _quality_display(quality)
        sell_price = item.get("sell_price")

        if sell_price is not None:
            estimated = sell_price * stack
            total_gold += estimated
            has_price = True
            if stack > 1:
                lines.append(f"- {name}×{stack}{quality_str}（预计 {estimated}g）")
            else:
                lines.append(f"- {name}{quality_str}（预计 {sell_price}g）")
        else:
            if stack > 1:
                lines.append(f"- {name}×{stack}{quality_str}")
            else:
                lines.append(f"- {name}{quality_str}")

    if has_price and total_gold > 0:
        lines.append(f"预计今日收入：{total_gold}g")

    return "\n".join(lines)


SHIPPING_BIN_CONTENT = ObserveModule(
    name="shipping_bin_content",
    layer=2,
    section_title="【今日运输箱（待运物品）】",
    queries=[],
    render=_render_shipping_bin_content,
)


# ── 4. crop_instance ─────────────────────────────────────────────────
# spec §6 crop_instance

def _render_crop_instance(data: dict) -> str:
    """
    渲染单株作物详情。

    data["crop_data"] 来自 find_targets(type="crop", location=loc, x=x, y=y)。
    data["instance_id"] = "farm_10_15"（由 collectors.py 注入）

    spec 字段：name / display_name / state / days_grown / total_growth_days / days_to_harvest / watered_today / quality_prediction
    实际接口（QUERY_INTERFACE §11a）：crop_name / is_harvestable / is_dead / is_watered / phase / max_phase
    注意差异：大量字段不同，需从接口字段推断 spec 期望的信息。
    """
    crop_data = data.get("crop_data") or {}
    targets = crop_data.get("targets") or []
    instance_id = data.get("instance_id", "")

    if not targets:
        return f"（该位置没有作物）"

    t = targets[0]
    crop_name = t.get("crop_name") or t.get("name", "未知作物")
    is_harvestable = t.get("is_harvestable", False)
    is_dead = t.get("is_dead", False)
    is_watered = t.get("is_watered", False)
    phase = t.get("phase", 0)
    max_phase = t.get("max_phase", 0)
    x = t.get("x", 0)
    y = t.get("y", 0)
    location_name = t.get("location_name", "")
    alias_map = data.get("alias_map") or {}
    locshort = alias_map.get(location_name, location_name.lower())

    # 状态判断
    if is_dead:
        state_display = "枯萎（需清理）"
    elif is_harvestable:
        state_display = "成熟，可收割"
    elif not is_watered:
        state_display = "生长中，需浇水"
    else:
        state_display = "生长中"

    # 生长进度
    growth_display = f"第 {phase + 1} 阶段/共 {max_phase + 1} 阶段"
    if is_harvestable:
        days_hint = ""
    elif phase < max_phase:
        days_hint = f"（还需 {max_phase - phase} 阶段）"
    else:
        days_hint = ""

    watered_display = "是" if is_watered else "否"

    lines = [
        f"{crop_name} | {locshort} ({x},{y})",
        f"状态：{state_display}",
        f"生长：{growth_display}{days_hint}",
        f"今日浇水：{watered_display}",
    ]
    return "\n".join(lines)


CROP_INSTANCE = ObserveModule(
    name="crop_instance",
    layer=2,
    section_title="【作物详情 crop:{rest}】",
    queries=[],
    render=_render_crop_instance,
)


# ── 5. animal_instance ────────────────────────────────────────────────
# spec §6 animal_instance

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

_MOOD_DISPLAY = {
    "happy": "开心",
    "content": "满足",
    "neutral": "普通",
    "sad": "不开心",
}


def _render_animal_instance(data: dict) -> str:
    """
    渲染单只动物详情。

    data["animal_data"] 来自 find_targets(type="animal", name=name)。
    data["instance_id"] = "甜豆"（由 collectors.py 注入）

    spec 字段：name / display_type / location_name / hearts / friendship / mood / needs_pet / needs_feed / days_owned / produce_ready / produce_name
    实际接口（QUERY_INTERFACE §11b）：animals[].name / display_name / animal_type / location_name / was_petted / friendship / target_ref
    注意差异：
    - display_type → animal_type（英文，需查映射表）
    - needs_pet → !was_petted
    - hearts → friendship // 250
    - 实际接口无 needs_feed / mood / days_owned / produce_ready / produce_name
    """
    animal_data = data.get("animal_data") or {}
    animals = animal_data.get("animals") or animal_data.get("targets") or []
    instance_id = data.get("instance_id", "")

    if not animals:
        name = instance_id.split("#")[0] if instance_id else "未知"
        return f'（未找到名为"{name}"的动物）'

    a = animals[0]
    name = a.get("name") or a.get("display_name", "未知")
    animal_type_en = a.get("animal_type", "")
    display_type = _ANIMAL_TYPE_DISPLAY.get(animal_type_en, animal_type_en)
    location_name = a.get("location_name", "")
    alias_map = data.get("alias_map") or {}
    locshort = alias_map.get(location_name, location_name.lower())
    loc_display = get_location_display(location_name, alias_map)

    friendship = a.get("friendship", 0)
    hearts = friendship // 250

    was_petted = a.get("was_petted", True)
    needs_pet = not was_petted

    status_parts = []
    if needs_pet:
        status_parts.append("需爱抚")
    if not status_parts:
        status_parts.append("今日已完成")
    status_display = " | ".join(status_parts)

    lines = [
        f"{name} | {display_type} | {loc_display}（{locshort}）",
        f"友好度：♥{hearts}/5（友好值 {friendship}/1000）",
        f"今日状态：{status_display}",
    ]

    # 实际接口无 days_owned，跳过
    # 实际接口无 produce_ready/produce_name，跳过

    return "\n".join(lines)


ANIMAL_INSTANCE = ObserveModule(
    name="animal_instance",
    layer=2,
    section_title="【动物详情 animal:{name}】",
    queries=[],
    render=_render_animal_instance,
)


# ── 6. npc_instance ───────────────────────────────────────────────────
# spec §6 npc_instance

def _render_npc_instance(data: dict) -> str:
    """
    渲染单个 NPC 详情。

    data["npc_data"] 来自 query_npc(name=name)。
    data["instance_id"] = "Lewis"（由 collectors.py 注入）

    spec 字段：name / display_name / description / location_name / x / y / hearts / friendship / gift_given_today / birthday / liked_gifts / loved_gifts / schedule_today
    实际接口（QUERY_INTERFACE §12）：name / location.name / location.tile_x / location.tile_y / friendship_hearts / friendship_points / loved_gifts / can_talk_today / has_given_gift_today / birthday_season / birthday_day / schedule_hint
    注意差异：
    - 接口无 description（职业简介）
    - 接口无 liked_gifts（仅 loved_gifts）
    - 接口无 schedule_today 列表（仅 schedule_hint 字符串）
    - hearts → friendship_hearts
    - friendship → friendship_points
    - gift_given_today → has_given_gift_today
    - birthday 拆为 birthday_season + birthday_day
    """
    npc_data = data.get("npc_data") or {}
    instance_id = data.get("instance_id", "")

    # 错误处理
    if npc_data.get("error"):
        name = instance_id or "未知"
        return f'（未找到 NPC "{name}"，请通过 observe(detail="npcs") 确认正确名称）'

    if not npc_data.get("name"):
        name = instance_id or "未知"
        return f'（未找到 NPC "{name}"，请通过 observe(detail="npcs") 确认正确名称）'

    name = npc_data.get("name", "")
    # 实际接口无 display_name，用 name 代替（Mod 返回的是英文名）
    display_name = npc_data.get("display_name") or name

    # 位置信息（在 location 子字典中）
    location = npc_data.get("location") or {}
    loc_name = location.get("name", "")
    alias_map = data.get("alias_map") or {}
    loc_display = get_location_display(loc_name, alias_map)
    x = location.get("tile_x", location.get("x", 0))
    y = location.get("tile_y", location.get("y", 0))

    # 友好度
    hearts = npc_data.get("friendship_hearts", 0)
    friendship = npc_data.get("friendship_points", 0)

    # 生日
    birthday_season = npc_data.get("birthday_season", "")
    birthday_day = npc_data.get("birthday_day", 0)
    season_cn = {"spring": "春", "summer": "夏", "fall": "秋", "winter": "冬"}.get(birthday_season, birthday_season)
    birthday_display = f"{season_cn} {birthday_day} 日" if birthday_season and birthday_day else "未知"

    # 礼物喜好
    loved_gifts = npc_data.get("loved_gifts") or []

    # 送礼记录
    has_given_gift = npc_data.get("has_given_gift_today", False)

    lines = [
        f"{display_name} | 当前位置：{loc_display} [{x},{y}] | 心形：♥{hearts}",
        "",
        f"生日：{birthday_display}",
    ]

    if loved_gifts:
        lines.append(f"喜爱礼物：{'，'.join(loved_gifts)}")

    if has_given_gift:
        lines.append("今日状态：已送礼")

    # 实际接口无 schedule_today 列表，仅有 schedule_hint 字符串
    schedule_hint = npc_data.get("schedule_hint", "")
    if schedule_hint:
        lines.append(f"\n今日日程提示：{schedule_hint}")

    return "\n".join(lines)


NPC_INSTANCE = ObserveModule(
    name="npc_instance",
    layer=2,
    section_title="【NPC 详情 npc:{name}】",
    queries=[],
    render=_render_npc_instance,
)


# ── 层 2 注册表 ───────────────────────────────────────────────────────

LAYER_2_MODULES: dict[str, ObserveModule] = {
    "chest":        CHEST_CONTENT,
    "fridge":       FRIDGE_CONTENT,
    "shipping_bin": SHIPPING_BIN_CONTENT,
    "crop":         CROP_INSTANCE,
    "animal":       ANIMAL_INSTANCE,
    "npc":          NPC_INSTANCE,
}
