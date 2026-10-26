"""L3 fact query schema declarations."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import JsonDict


FILTER_OPERATORS = frozenset({"eq", "in", "min", "max", "exists"})


@dataclass(frozen=True, slots=True)
class QueryField:
    name: str
    operators: frozenset[str] = FILTER_OPERATORS
    sortable: bool = False
    aggregatable: bool = False


@dataclass(frozen=True, slots=True)
class FactQuerySchema:
    fact_type: str
    label: str
    fields: dict[str, QueryField] = field(default_factory=dict)
    count_where_specs: dict[str, JsonDict] = field(default_factory=dict)
    bucket_fields: dict[str, str] = field(default_factory=dict)
    default_sort: str = "distance"

    def filter_fields(self) -> list[str]:
        return sorted(self.fields)

    def sort_fields(self) -> list[str]:
        return sorted(name for name, spec in self.fields.items() if spec.sortable)

    def aggregate_fields(self) -> list[str]:
        return sorted(name for name, spec in self.fields.items() if spec.aggregatable)


def _field(
    name: str,
    *,
    operators: set[str] | frozenset[str] = FILTER_OPERATORS,
    sortable: bool = False,
    aggregatable: bool = False,
) -> QueryField:
    return QueryField(
        name=name,
        operators=frozenset(operators),
        sortable=sortable,
        aggregatable=aggregatable,
    )


COMMON_FIELDS = {
    "distance": _field("distance", operators={"min", "max", "exists"}, sortable=True),
    "type": _field("type", operators={"eq", "in", "exists"}, aggregatable=True),
    "summary": _field("summary", operators={"eq", "in", "exists"}),
}


SCHEMAS: dict[str, FactQuerySchema] = {
    "crop": FactQuerySchema(
        fact_type="crop",
        label="庄稼",
        fields={
            **COMMON_FIELDS,
            "crop_id": _field("crop_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "harvest_item_id": _field("harvest_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "watered": _field("watered", operators={"eq", "exists"}, aggregatable=True),
            "ready_for_harvest": _field("ready_for_harvest", operators={"eq", "exists"}, aggregatable=True),
            "dead": _field("dead", operators={"eq", "exists"}, aggregatable=True),
            "growth_phase": _field("growth_phase", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "phases_total": _field("phases_total", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "day_of_current_phase": _field("day_of_current_phase", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "fertilizer": _field("fertilizer", operators={"eq", "in", "exists"}, aggregatable=True),
            "harvest_method": _field("harvest_method", operators={"eq", "in", "exists"}, aggregatable=True),
            "regrows_after_harvest": _field("regrows_after_harvest", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={
            "needs_watering": {"watered": False, "ready_for_harvest": False, "dead": False},
            "ready_to_harvest": {"ready_for_harvest": True, "dead": False},
            "dead": {"dead": True},
        },
        bucket_fields={"by_crop": "display_name"},
    ),
    "tree": FactQuerySchema(
        fact_type="tree",
        label="树木",
        fields={
            **COMMON_FIELDS,
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "tree_type": _field("tree_type", operators={"eq", "in", "exists"}, aggregatable=True),
            "growth_stage": _field("growth_stage", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "health": _field("health", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "is_stump": _field("is_stump", operators={"eq", "exists"}, aggregatable=True),
            "is_tapped": _field("is_tapped", operators={"eq", "exists"}, aggregatable=True),
            "fertilized": _field("fertilized", operators={"eq", "exists"}, aggregatable=True),
            "has_moss": _field("has_moss", operators={"eq", "exists"}, aggregatable=True),
            "has_seed": _field("has_seed", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={"has_moss": {"has_moss": True}, "is_stump": {"is_stump": True}, "fertilized": {"fertilized": True}},
        bucket_fields={"by_tree_type": "display_name"},
    ),
    "fruit_tree": FactQuerySchema(
        fact_type="fruit_tree",
        label="果树",
        fields={
            **COMMON_FIELDS,
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "growth_stage": _field("growth_stage", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "days_until_mature": _field("days_until_mature", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "fruit_count": _field("fruit_count", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "fruit_quality": _field("fruit_quality", operators={"eq", "in", "min", "max", "exists"}, sortable=True, aggregatable=True),
            "is_in_season": _field("is_in_season", operators={"eq", "exists"}, aggregatable=True),
            "is_stump": _field("is_stump", operators={"eq", "exists"}, aggregatable=True),
            "struck_by_lightning": _field("struck_by_lightning", operators={"eq", "exists"}, aggregatable=True),
            "green_house_tile_tree": _field("green_house_tile_tree", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={
            "has_fruit": {"fruit_count": {"min": 1}},
            "is_stump": {"is_stump": True},
            "struck_by_lightning": {"struck_by_lightning": True},
        },
        bucket_fields={"by_fruit_tree": "display_name", "by_quality": "fruit_quality"},
    ),
    "machine": FactQuerySchema(
        fact_type="machine",
        label="机器",
        fields={
            **COMMON_FIELDS,
            "machine_data_id": _field("machine_data_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "internal_name": _field("internal_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "ready_for_harvest": _field("ready_for_harvest", operators={"eq", "exists"}, aggregatable=True),
            "minutes_until_ready": _field("minutes_until_ready", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "machine_state": _field("machine_state", operators={"eq", "in", "exists"}, aggregatable=True),
            "last_input_item_id": _field("last_input_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "held_object.qualified_item_id": _field("held_object.qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "held_object.display_name": _field("held_object.display_name", operators={"eq", "in", "exists"}, aggregatable=True),
        },
        count_where_specs={"ready": {"ready_for_harvest": True}},
        bucket_fields={"by_state": "machine_state", "by_machine": "display_name"},
    ),
    "resource_node": FactQuerySchema(
        fact_type="resource_node",
        label="资源点",
        fields={
            **COMMON_FIELDS,
            "node_kind": _field("node_kind", operators={"eq", "in", "exists"}, aggregatable=True),
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "required_tool": _field("required_tool", operators={"eq", "in", "exists"}, aggregatable=True),
            "hits_to_destroy": _field("hits_to_destroy", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "is_spawned_object": _field("is_spawned_object", operators={"eq", "exists"}, aggregatable=True),
        },
        bucket_fields={"by_node_kind": "node_kind", "by_required_tool": "required_tool"},
    ),
    "loose_item": FactQuerySchema(
        fact_type="loose_item",
        label="地面物品",
        fields={
            **COMMON_FIELDS,
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "internal_name": _field("internal_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "item_id": _field("item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "stack": _field("stack", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "quality": _field("quality", operators={"eq", "in", "min", "max", "exists"}, sortable=True, aggregatable=True),
            "can_be_grabbed": _field("can_be_grabbed", operators={"eq", "exists"}, aggregatable=True),
            "is_spawned_object": _field("is_spawned_object", operators={"eq", "exists"}, aggregatable=True),
            "category": _field("category", operators={"eq", "in", "exists"}, aggregatable=True),
            "category_name": _field("category_name", operators={"eq", "in", "exists"}, aggregatable=True),
        },
        count_where_specs={"can_grab": {"can_be_grabbed": True}},
        bucket_fields={"by_item": "display_name", "by_category": "category"},
    ),
    "container": FactQuerySchema(
        fact_type="container",
        label="容器",
        fields={
            **COMMON_FIELDS,
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "container_kind": _field("container_kind", operators={"eq", "in", "exists"}, aggregatable=True),
            "items_count": _field("items_count", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "capacity": _field("capacity", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "fridge": _field("fridge", operators={"eq", "exists"}, aggregatable=True),
            "player_chest": _field("player_chest", operators={"eq", "exists"}, aggregatable=True),
            "special_chest_type": _field("special_chest_type", operators={"eq", "in", "exists"}, aggregatable=True),
            "color": _field("color", operators={"eq", "in", "exists"}, aggregatable=True),
        },
        bucket_fields={"by_container_kind": "container_kind", "by_color": "color"},
    ),
    "bed": FactQuerySchema(
        fact_type="bed",
        label="床",
        fields={
            **COMMON_FIELDS,
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "bed_type": _field("bed_type", operators={"eq", "in", "exists"}, aggregatable=True),
            "can_sleep": _field("can_sleep", operators={"eq", "exists"}, aggregatable=True),
            "rotation": _field("rotation", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "is_being_slept_in": _field("is_being_slept_in", operators={"eq", "exists"}, aggregatable=True),
            "bed_spot.x": _field("bed_spot.x", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "bed_spot.y": _field("bed_spot.y", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
        },
        count_where_specs={"can_sleep": {"can_sleep": True}, "being_slept_in": {"is_being_slept_in": True}},
        bucket_fields={"by_bed_type": "bed_type"},
    ),
    "furniture": FactQuerySchema(
        fact_type="furniture",
        label="家具",
        fields={
            **COMMON_FIELDS,
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "internal_name": _field("internal_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "furniture_type": _field("furniture_type", operators={"eq", "in", "exists"}, aggregatable=True),
            "rotation": _field("rotation", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "can_sit": _field("can_sit", operators={"eq", "exists"}, aggregatable=True),
            "is_on": _field("is_on", operators={"eq", "exists"}, aggregatable=True),
            "max_rotations": _field("max_rotations", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
        },
        count_where_specs={"sittable": {"can_sit": True}, "on": {"is_on": True}},
        bucket_fields={"by_furniture_type": "furniture_type"},
    ),
    "warp": FactQuerySchema(
        fact_type="warp",
        label="出口",
        fields={
            **COMMON_FIELDS,
            "warp_key": _field("warp_key", operators={"eq", "in", "exists"}, aggregatable=True),
            "target_map": _field("target_map", operators={"eq", "in", "exists"}, aggregatable=True),
            "target_x": _field("target_x", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "target_y": _field("target_y", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "npc_only": _field("npc_only", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={"npc_only": {"npc_only": True}},
        bucket_fields={"by_target_map": "target_map"},
    ),
    "door": FactQuerySchema(
        fact_type="door",
        label="门",
        fields={
            **COMMON_FIELDS,
            "door_key": _field("door_key", operators={"eq", "in", "exists"}, aggregatable=True),
            "target_map": _field("target_map", operators={"eq", "in", "exists"}, aggregatable=True),
            "target_tile.x": _field("target_tile.x", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "target_tile.y": _field("target_tile.y", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "locked": _field("locked", operators={"eq", "exists"}, aggregatable=True),
            "door_kind": _field("door_kind", operators={"eq", "in", "exists"}, aggregatable=True),
        },
        count_where_specs={"locked": {"locked": True}},
        bucket_fields={"by_target_map": "target_map", "by_door_kind": "door_kind"},
    ),
    "exit": FactQuerySchema(
        fact_type="exit",
        label="出口",
        fields={
            **COMMON_FIELDS,
            "target_map": _field("target_map", operators={"eq", "in", "exists"}, aggregatable=True),
            "target_x": _field("target_x", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "target_y": _field("target_y", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "target_tile.x": _field("target_tile.x", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "target_tile.y": _field("target_tile.y", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "door_kind": _field("door_kind", operators={"eq", "in", "exists"}, aggregatable=True),
            "locked": _field("locked", operators={"eq", "exists"}, aggregatable=True),
            "npc_only": _field("npc_only", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={"locked": {"locked": True}, "npc_only": {"npc_only": True}},
        bucket_fields={"by_target_map": "target_map"},
    ),
    "resource_clump": FactQuerySchema(
        fact_type="resource_clump",
        label="大型资源堆",
        fields={
            **COMMON_FIELDS,
            "clump_kind": _field("clump_kind", operators={"eq", "in", "exists"}, aggregatable=True),
            "health": _field("health", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "width": _field("width", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "height": _field("height", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "required_tool": _field("required_tool", operators={"eq", "in", "exists"}, aggregatable=True),
            "required_tool_level": _field("required_tool_level", operators={"eq", "in", "min", "max", "exists"}, sortable=True, aggregatable=True),
        },
        bucket_fields={"by_clump_kind": "clump_kind", "by_required_tool": "required_tool"},
    ),
    "npc": FactQuerySchema(
        fact_type="npc",
        label="NPC",
        fields={
            **COMMON_FIELDS,
            "name": _field("name", operators={"eq", "in", "exists"}, aggregatable=True),
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "npc_key": _field("npc_key", operators={"eq", "in", "exists"}, aggregatable=True),
            "current_map_token": _field("current_map_token", operators={"eq", "in", "exists"}, aggregatable=True),
            "can_socialize": _field("can_socialize", operators={"eq", "exists"}, aggregatable=True),
            "friendship_points": _field("friendship_points", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "schedule_key": _field("schedule_key", operators={"eq", "in", "exists"}, aggregatable=True),
            "birthday_season": _field("birthday_season", operators={"eq", "in", "exists"}, aggregatable=True),
            "birthday_day": _field("birthday_day", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "has_dialogue": _field("has_dialogue", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={"can_socialize": {"can_socialize": True}, "has_dialogue": {"has_dialogue": True}},
    ),
    "farm_animal": FactQuerySchema(
        fact_type="farm_animal",
        label="动物",
        fields={
            **COMMON_FIELDS,
            "name": _field("name", operators={"eq", "in", "exists"}, aggregatable=True),
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "animal_type": _field("animal_type", operators={"eq", "in", "exists"}, aggregatable=True),
            "home_building_type": _field("home_building_type", operators={"eq", "in", "exists"}, aggregatable=True),
            "home_building_id": _field("home_building_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "produce_ready": _field("produce_ready", operators={"eq", "exists"}, aggregatable=True),
            "was_petted": _field("was_petted", operators={"eq", "exists"}, aggregatable=True),
            "was_auto_petted": _field("was_auto_petted", operators={"eq", "exists"}, aggregatable=True),
            "friendship": _field("friendship", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "happiness": _field("happiness", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
        },
        count_where_specs={"produce_ready": {"produce_ready": True}, "needs_pet": {"was_petted": False}},
        bucket_fields={"by_animal_type": "animal_type", "by_home_building_type": "home_building_type"},
    ),
    "pet": FactQuerySchema(
        fact_type="pet",
        label="宠物",
        fields={
            **COMMON_FIELDS,
            "name": _field("name", operators={"eq", "in", "exists"}, aggregatable=True),
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "pet_guid": _field("pet_guid", operators={"eq", "in", "exists"}, aggregatable=True),
            "pet_type": _field("pet_type", operators={"eq", "in", "exists"}, aggregatable=True),
            "friendship": _field("friendship", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "petted_today": _field("petted_today", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={"needs_pet": {"petted_today": False}},
        bucket_fields={"by_pet_type": "pet_type"},
    ),
    "horse": FactQuerySchema(
        fact_type="horse",
        label="马",
        fields={
            **COMMON_FIELDS,
            "name": _field("name", operators={"eq", "in", "exists"}, aggregatable=True),
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "horse_guid": _field("horse_guid", operators={"eq", "in", "exists"}, aggregatable=True),
            "owner_id": _field("owner_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "has_rider": _field("has_rider", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={"has_rider": {"has_rider": True}},
    ),
    "monster": FactQuerySchema(
        fact_type="monster",
        label="怪物",
        fields={
            **COMMON_FIELDS,
            "name": _field("name", operators={"eq", "in", "exists"}, aggregatable=True),
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "monster_key": _field("monster_key", operators={"eq", "in", "exists"}, aggregatable=True),
            "character_guid": _field("character_guid", operators={"eq", "in", "exists"}, aggregatable=True),
            "current_map_token": _field("current_map_token", operators={"eq", "in", "exists"}, aggregatable=True),
            "health": _field("health", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "max_health": _field("max_health", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "damage_to_farmer": _field("damage_to_farmer", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "is_moving": _field("is_moving", operators={"eq", "exists"}, aggregatable=True),
        },
        count_where_specs={"moving": {"is_moving": True}},
        bucket_fields={"by_monster": "monster_key"},
    ),
    "inventory_item": FactQuerySchema(
        fact_type="inventory_item",
        label="背包物品",
        fields={
            "display_name": _field("display_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "internal_name": _field("internal_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "item_id": _field("item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "category": _field("category", operators={"eq", "in", "exists"}, aggregatable=True),
            "category_name": _field("category_name", operators={"eq", "in", "exists"}, aggregatable=True),
            "stack": _field("stack", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "quality": _field("quality", operators={"eq", "in", "min", "max", "exists"}, sortable=True, aggregatable=True),
            "slot": _field("slot", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "can_stack": _field("can_stack", operators={"eq", "exists"}, aggregatable=True),
            "upgrade_level": _field("upgrade_level", operators={"eq", "in", "min", "max", "exists"}, sortable=True, aggregatable=True),
            "is_active": _field("is_active", operators={"eq", "exists"}, aggregatable=True),
            "is_recipe": _field("is_recipe", operators={"eq", "exists"}, aggregatable=True),
            "base_sell_price": _field("base_sell_price", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
        },
        default_sort="slot",
    ),
    "ui_element": FactQuerySchema(
        fact_type="ui_element",
        label="UI 元素",
        fields={
            "type": _field("type", operators={"eq", "in", "exists"}, aggregatable=True),
            "element_token": _field("element_token", operators={"eq", "in", "exists"}, aggregatable=True),
            "identity_guard": _field("identity_guard", operators={"eq", "in", "exists"}, aggregatable=True),
            "label": _field("label", operators={"eq", "in", "exists"}, aggregatable=True),
            "visible": _field("visible", operators={"eq", "exists"}, aggregatable=True),
            "enabled": _field("enabled", operators={"eq", "exists"}, aggregatable=True),
            "index": _field("index", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "price": _field("price", operators={"eq", "in", "min", "max", "exists"}, sortable=True),
            "stock": _field("stock", operators={"eq", "in", "min", "max", "exists"}, sortable=True, aggregatable=True),
            "qualified_item_id": _field("qualified_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "trade_item_id": _field("trade_item_id", operators={"eq", "in", "exists"}, aggregatable=True),
            "response_key": _field("response_key", operators={"eq", "in", "exists"}, aggregatable=True),
        },
        count_where_specs={"visible": {"visible": True}, "enabled": {"enabled": True}},
        bucket_fields={"by_type": "type"},
        default_sort="index",
    ),
}

FACT_TYPE_ALIASES: dict[str, str] = {
    "crops": "crop",
    "trees": "tree",
    "fruit_trees": "fruit_tree",
    "machines": "machine",
    "containers": "container",
    "beds": "bed",
    "furniture_items": "furniture",
    "warps": "warp",
    "doors": "door",
    "exits": "exit",
    "exit": "exit",
    "objects": "resource_node",
    "resources": "resource_node",
    "npcs": "npc",
    "animals": "farm_animal",
    "inventory": "inventory_item",
    "items": "inventory_item",
    "ui": "ui_element",
}


def get_schema(fact_type: str) -> FactQuerySchema | None:
    normalized = normalize_fact_type(fact_type)
    return SCHEMAS.get(normalized)


def normalize_fact_type(fact_type: str) -> str:
    raw = (fact_type or "").strip()
    return FACT_TYPE_ALIASES.get(raw, raw)


def supported_fact_types() -> list[str]:
    return sorted(SCHEMAS)


def schema_operator_map(schema: FactQuerySchema) -> JsonDict:
    return {name: sorted(field.operators) for name, field in sorted(schema.fields.items())}


def validate_filters(schema: FactQuerySchema, filters: JsonDict | None) -> list[str]:
    if not filters:
        return []
    errors: list[str] = []
    for field_name, predicate in filters.items():
        field = schema.fields.get(field_name)
        if field is None:
            errors.append(f"字段 {field_name!r} 不支持 filter")
            continue
        operators = _predicate_operators(predicate)
        invalid = operators - field.operators
        if invalid:
            errors.append(f"字段 {field_name!r} 不支持操作符：{', '.join(sorted(invalid))}")
        if isinstance(predicate, dict):
            if "in" in predicate and not isinstance(predicate["in"], list):
                errors.append(f"字段 {field_name!r} 的 in 操作符必须使用数组")
            for op in ("min", "max"):
                if op in predicate and not isinstance(predicate[op], (int, float)):
                    errors.append(f"字段 {field_name!r} 的 {op} 操作符必须使用数字")
    return errors


def validate_sort(schema: FactQuerySchema, sort: str | None) -> list[str]:
    if not sort:
        return []
    field_name = sort.removeprefix("-")
    field = schema.fields.get(field_name)
    if field is None:
        return [f"字段 {field_name!r} 不支持 sort"]
    if not field.sortable:
        return [f"字段 {field_name!r} 不能用于 sort"]
    return []


def validate_schema_aggregates(schema: FactQuerySchema) -> list[str]:
    errors: list[str] = []
    errors.extend(validate_sort(schema, schema.default_sort))
    for name, filters in schema.count_where_specs.items():
        for error in validate_filters(schema, filters):
            errors.append(f"count_where {name!r}: {error}")
    for name, field_name in schema.bucket_fields.items():
        field = schema.fields.get(field_name)
        if field is None:
            errors.append(f"bucket {name!r}: 字段 {field_name!r} 不存在")
        elif not field.aggregatable:
            errors.append(f"bucket {name!r}: 字段 {field_name!r} 不能 aggregate")
    return errors


def _predicate_operators(predicate: object) -> frozenset[str]:
    if isinstance(predicate, dict):
        return frozenset(str(key) for key in predicate)
    return frozenset({"eq"})
