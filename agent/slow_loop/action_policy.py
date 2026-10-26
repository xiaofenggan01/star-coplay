from __future__ import annotations

INSTANT_ACTIONS = frozenset({
    "say",
    "emote",
    "face",
    "equip",
    "interact",
    "open_menu",
    "menu_click",
    "menu_close",
})

QUERY_ACTIONS = frozenset({
    # observe 已移出 QUERY_ACTIONS，由 deliberative_loop.L3_BACKED_CORE_TOOLS
    # 路由到 v2_skills/builtin/observe 实现
    "query_entities", "query_inventory", "query_map_exits", "query_map_objects",
    "query_farm_summary", "query_can_interact", "query_location_info", "query_location",
    "query_menu", "query_runtime_snapshot", "query_world_region", "query_inventory_snapshot",
    "query_ui_snapshot", "query_inspect_refs",
    "query_time", "query_walkable_area", "query_containers", "query_container_items", "query_shop",
})

LONG_ACTIONS = frozenset({
    "move_to", "do", "use_tool", "tp", "water_crops", "harvest_crops",
    "plant_crops", "chop_trees", "mine_rocks", "go_bed", "ship_items",
    "buy_items", "execute_on_targets", "go_to", "go_to_bed", "menu_interact",
})

DEFAULT_LONG_PRIORITY = 100
HIGH_LONG_PRIORITY = 10
TASK_COMPLETED_PRIORITY = 6
MAX_ASYNC_ACTIONS_PER_ROUND = 3
QUERY_TIMEOUT_S = 10.0
BRIDGE_POLL_INTERVAL_S = 0.3
DEFAULT_LONG_ACTION_TIMEOUT_S = 60.0

ACTION_TIMEOUTS: dict[str, float] = {
    "move_to": 60.0,
    "go_to": 120.0,
    "do": 30.0,
    "use_tool": 30.0,
    "tp": 30.0,
    "water_crops": 120.0,
    "harvest_crops": 120.0,
    "plant_crops": 120.0,
    "chop_trees": 120.0,
    "mine_rocks": 120.0,
    "go_bed": 180.0,
    "go_to_bed": 180.0,
    "buy_items": 60.0,
    "ship_items": 30.0,
    "execute_on_targets": 300.0,
    "menu_interact": 120.0,
}


def is_instant_action(name: str) -> bool:
    return name in INSTANT_ACTIONS


def is_long_action(name: str) -> bool:
    return name in LONG_ACTIONS


def timeout_for_action(name: str) -> float:
    return ACTION_TIMEOUTS.get(name, DEFAULT_LONG_ACTION_TIMEOUT_S)
