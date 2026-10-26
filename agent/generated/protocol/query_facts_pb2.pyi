from . import common_pb2 as _common_pb2
from . import ref_pb2 as _ref_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class QuestStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    QUEST_STATUS_UNSPECIFIED: _ClassVar[QuestStatus]
    QUEST_IN_PROGRESS: _ClassVar[QuestStatus]
    QUEST_AWAITING_REWARD: _ClassVar[QuestStatus]
    QUEST_COMPLETED: _ClassVar[QuestStatus]
    QUEST_FAILED: _ClassVar[QuestStatus]

class MachineState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MACHINE_STATE_UNSPECIFIED: _ClassVar[MachineState]
    MACHINE_IDLE: _ClassVar[MachineState]
    MACHINE_PROCESSING: _ClassVar[MachineState]
    MACHINE_READY: _ClassVar[MachineState]
QUEST_STATUS_UNSPECIFIED: QuestStatus
QUEST_IN_PROGRESS: QuestStatus
QUEST_AWAITING_REWARD: QuestStatus
QUEST_COMPLETED: QuestStatus
QUEST_FAILED: QuestStatus
MACHINE_STATE_UNSPECIFIED: MachineState
MACHINE_IDLE: MachineState
MACHINE_PROCESSING: MachineState
MACHINE_READY: MachineState

class QuestObjectiveFact(_message.Message):
    __slots__ = ("description", "current_count", "max_count", "is_complete")
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    CURRENT_COUNT_FIELD_NUMBER: _ClassVar[int]
    MAX_COUNT_FIELD_NUMBER: _ClassVar[int]
    IS_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    description: str
    current_count: int
    max_count: int
    is_complete: bool
    def __init__(self, description: _Optional[str] = ..., current_count: _Optional[int] = ..., max_count: _Optional[int] = ..., is_complete: bool = ...) -> None: ...

class RuntimeSnapshotFact(_message.Message):
    __slots__ = ("game_date", "time_of_day", "season", "day_of_month", "year", "player_location", "money", "stamina", "max_stamina", "health", "max_health", "is_currently_raining", "is_festival_day", "is_menu_open", "is_lightning", "is_snowing", "is_green_rain", "is_outdoors", "debug")
    GAME_DATE_FIELD_NUMBER: _ClassVar[int]
    TIME_OF_DAY_FIELD_NUMBER: _ClassVar[int]
    SEASON_FIELD_NUMBER: _ClassVar[int]
    DAY_OF_MONTH_FIELD_NUMBER: _ClassVar[int]
    YEAR_FIELD_NUMBER: _ClassVar[int]
    PLAYER_LOCATION_FIELD_NUMBER: _ClassVar[int]
    MONEY_FIELD_NUMBER: _ClassVar[int]
    STAMINA_FIELD_NUMBER: _ClassVar[int]
    MAX_STAMINA_FIELD_NUMBER: _ClassVar[int]
    HEALTH_FIELD_NUMBER: _ClassVar[int]
    MAX_HEALTH_FIELD_NUMBER: _ClassVar[int]
    IS_CURRENTLY_RAINING_FIELD_NUMBER: _ClassVar[int]
    IS_FESTIVAL_DAY_FIELD_NUMBER: _ClassVar[int]
    IS_MENU_OPEN_FIELD_NUMBER: _ClassVar[int]
    IS_LIGHTNING_FIELD_NUMBER: _ClassVar[int]
    IS_SNOWING_FIELD_NUMBER: _ClassVar[int]
    IS_GREEN_RAIN_FIELD_NUMBER: _ClassVar[int]
    IS_OUTDOORS_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    game_date: str
    time_of_day: int
    season: str
    day_of_month: int
    year: int
    player_location: _common_pb2.LocationSnapshot
    money: int
    stamina: int
    max_stamina: int
    health: int
    max_health: int
    is_currently_raining: bool
    is_festival_day: bool
    is_menu_open: bool
    is_lightning: bool
    is_snowing: bool
    is_green_rain: bool
    is_outdoors: bool
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, game_date: _Optional[str] = ..., time_of_day: _Optional[int] = ..., season: _Optional[str] = ..., day_of_month: _Optional[int] = ..., year: _Optional[int] = ..., player_location: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ..., money: _Optional[int] = ..., stamina: _Optional[int] = ..., max_stamina: _Optional[int] = ..., health: _Optional[int] = ..., max_health: _Optional[int] = ..., is_currently_raining: bool = ..., is_festival_day: bool = ..., is_menu_open: bool = ..., is_lightning: bool = ..., is_snowing: bool = ..., is_green_rain: bool = ..., is_outdoors: bool = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...

class WorldRegionFact(_message.Message):
    __slots__ = ("map_token", "map_id", "region", "is_outdoors", "tiles", "entities", "characters", "debug")
    MAP_TOKEN_FIELD_NUMBER: _ClassVar[int]
    MAP_ID_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    IS_OUTDOORS_FIELD_NUMBER: _ClassVar[int]
    TILES_FIELD_NUMBER: _ClassVar[int]
    ENTITIES_FIELD_NUMBER: _ClassVar[int]
    CHARACTERS_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    map_token: str
    map_id: str
    region: _common_pb2.TileRect
    is_outdoors: bool
    tiles: _containers.RepeatedCompositeFieldContainer[TileFact]
    entities: _containers.RepeatedCompositeFieldContainer[WorldEntityFact]
    characters: _containers.RepeatedCompositeFieldContainer[CharacterFact]
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, map_token: _Optional[str] = ..., map_id: _Optional[str] = ..., region: _Optional[_Union[_common_pb2.TileRect, _Mapping]] = ..., is_outdoors: bool = ..., tiles: _Optional[_Iterable[_Union[TileFact, _Mapping]]] = ..., entities: _Optional[_Iterable[_Union[WorldEntityFact, _Mapping]]] = ..., characters: _Optional[_Iterable[_Union[CharacterFact, _Mapping]]] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...

class TileFact(_message.Message):
    __slots__ = ("position", "passable", "occupied", "diggable", "water", "terrain_kind", "refs", "floor_type", "terrain_feature_type", "open_water", "debug")
    POSITION_FIELD_NUMBER: _ClassVar[int]
    PASSABLE_FIELD_NUMBER: _ClassVar[int]
    OCCUPIED_FIELD_NUMBER: _ClassVar[int]
    DIGGABLE_FIELD_NUMBER: _ClassVar[int]
    WATER_FIELD_NUMBER: _ClassVar[int]
    TERRAIN_KIND_FIELD_NUMBER: _ClassVar[int]
    REFS_FIELD_NUMBER: _ClassVar[int]
    FLOOR_TYPE_FIELD_NUMBER: _ClassVar[int]
    TERRAIN_FEATURE_TYPE_FIELD_NUMBER: _ClassVar[int]
    OPEN_WATER_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    position: _common_pb2.TilePosition
    passable: bool
    occupied: bool
    diggable: bool
    water: bool
    terrain_kind: str
    refs: _containers.RepeatedCompositeFieldContainer[_ref_pb2.RefId]
    floor_type: str
    terrain_feature_type: str
    open_water: bool
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, position: _Optional[_Union[_common_pb2.TilePosition, _Mapping]] = ..., passable: bool = ..., occupied: bool = ..., diggable: bool = ..., water: bool = ..., terrain_kind: _Optional[str] = ..., refs: _Optional[_Iterable[_Union[_ref_pb2.RefId, _Mapping]]] = ..., floor_type: _Optional[str] = ..., terrain_feature_type: _Optional[str] = ..., open_water: bool = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...

class WorldEntityFact(_message.Message):
    __slots__ = ("ref", "type", "fact_kind", "position", "summary", "tree", "fruit_tree", "crop", "resource_node", "loose_item", "machine", "container", "bed", "furniture", "resource_clump", "door", "warp", "debug")
    REF_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    FACT_KIND_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    TREE_FIELD_NUMBER: _ClassVar[int]
    FRUIT_TREE_FIELD_NUMBER: _ClassVar[int]
    CROP_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_NODE_FIELD_NUMBER: _ClassVar[int]
    LOOSE_ITEM_FIELD_NUMBER: _ClassVar[int]
    MACHINE_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_FIELD_NUMBER: _ClassVar[int]
    BED_FIELD_NUMBER: _ClassVar[int]
    FURNITURE_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_CLUMP_FIELD_NUMBER: _ClassVar[int]
    DOOR_FIELD_NUMBER: _ClassVar[int]
    WARP_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    ref: _ref_pb2.RefId
    type: str
    fact_kind: str
    position: _common_pb2.TilePosition
    summary: str
    tree: TreeFact
    fruit_tree: FruitTreeFact
    crop: CropFact
    resource_node: ResourceNodeFact
    loose_item: LooseItemFact
    machine: MachineFact
    container: ContainerFact
    bed: BedFact
    furniture: FurnitureFact
    resource_clump: ResourceClumpFact
    door: DoorFact
    warp: WarpFact
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, ref: _Optional[_Union[_ref_pb2.RefId, _Mapping]] = ..., type: _Optional[str] = ..., fact_kind: _Optional[str] = ..., position: _Optional[_Union[_common_pb2.TilePosition, _Mapping]] = ..., summary: _Optional[str] = ..., tree: _Optional[_Union[TreeFact, _Mapping]] = ..., fruit_tree: _Optional[_Union[FruitTreeFact, _Mapping]] = ..., crop: _Optional[_Union[CropFact, _Mapping]] = ..., resource_node: _Optional[_Union[ResourceNodeFact, _Mapping]] = ..., loose_item: _Optional[_Union[LooseItemFact, _Mapping]] = ..., machine: _Optional[_Union[MachineFact, _Mapping]] = ..., container: _Optional[_Union[ContainerFact, _Mapping]] = ..., bed: _Optional[_Union[BedFact, _Mapping]] = ..., furniture: _Optional[_Union[FurnitureFact, _Mapping]] = ..., resource_clump: _Optional[_Union[ResourceClumpFact, _Mapping]] = ..., door: _Optional[_Union[DoorFact, _Mapping]] = ..., warp: _Optional[_Union[WarpFact, _Mapping]] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...

class TreeFact(_message.Message):
    __slots__ = ("tree_type", "tree_type_guard", "growth_stage", "is_stump", "is_tapped", "has_moss", "health", "fertilized", "has_seed", "display_name")
    TREE_TYPE_FIELD_NUMBER: _ClassVar[int]
    TREE_TYPE_GUARD_FIELD_NUMBER: _ClassVar[int]
    GROWTH_STAGE_FIELD_NUMBER: _ClassVar[int]
    IS_STUMP_FIELD_NUMBER: _ClassVar[int]
    IS_TAPPED_FIELD_NUMBER: _ClassVar[int]
    HAS_MOSS_FIELD_NUMBER: _ClassVar[int]
    HEALTH_FIELD_NUMBER: _ClassVar[int]
    FERTILIZED_FIELD_NUMBER: _ClassVar[int]
    HAS_SEED_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    tree_type: str
    tree_type_guard: str
    growth_stage: int
    is_stump: bool
    is_tapped: bool
    has_moss: bool
    health: float
    fertilized: bool
    has_seed: bool
    display_name: str
    def __init__(self, tree_type: _Optional[str] = ..., tree_type_guard: _Optional[str] = ..., growth_stage: _Optional[int] = ..., is_stump: bool = ..., is_tapped: bool = ..., has_moss: bool = ..., health: _Optional[float] = ..., fertilized: bool = ..., has_seed: bool = ..., display_name: _Optional[str] = ...) -> None: ...

class FruitTreeFact(_message.Message):
    __slots__ = ("tree_id", "qualified_item_id", "item_id_guard", "growth_stage", "days_until_mature", "fruit_count", "is_stump", "struck_by_lightning", "is_in_season", "fruit_quality", "green_house_tile_tree", "display_name")
    TREE_ID_FIELD_NUMBER: _ClassVar[int]
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_ID_GUARD_FIELD_NUMBER: _ClassVar[int]
    GROWTH_STAGE_FIELD_NUMBER: _ClassVar[int]
    DAYS_UNTIL_MATURE_FIELD_NUMBER: _ClassVar[int]
    FRUIT_COUNT_FIELD_NUMBER: _ClassVar[int]
    IS_STUMP_FIELD_NUMBER: _ClassVar[int]
    STRUCK_BY_LIGHTNING_FIELD_NUMBER: _ClassVar[int]
    IS_IN_SEASON_FIELD_NUMBER: _ClassVar[int]
    FRUIT_QUALITY_FIELD_NUMBER: _ClassVar[int]
    GREEN_HOUSE_TILE_TREE_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    tree_id: str
    qualified_item_id: str
    item_id_guard: str
    growth_stage: int
    days_until_mature: int
    fruit_count: int
    is_stump: bool
    struck_by_lightning: bool
    is_in_season: bool
    fruit_quality: int
    green_house_tile_tree: bool
    display_name: str
    def __init__(self, tree_id: _Optional[str] = ..., qualified_item_id: _Optional[str] = ..., item_id_guard: _Optional[str] = ..., growth_stage: _Optional[int] = ..., days_until_mature: _Optional[int] = ..., fruit_count: _Optional[int] = ..., is_stump: bool = ..., struck_by_lightning: bool = ..., is_in_season: bool = ..., fruit_quality: _Optional[int] = ..., green_house_tile_tree: bool = ..., display_name: _Optional[str] = ...) -> None: ...

class CropFact(_message.Message):
    __slots__ = ("crop_id", "harvest_item_id", "crop_guard", "growth_phase", "ready_for_harvest", "watered", "fertilizer", "dead", "harvest_method", "phases_total", "day_of_current_phase", "regrows_after_harvest", "display_name")
    CROP_ID_FIELD_NUMBER: _ClassVar[int]
    HARVEST_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    CROP_GUARD_FIELD_NUMBER: _ClassVar[int]
    GROWTH_PHASE_FIELD_NUMBER: _ClassVar[int]
    READY_FOR_HARVEST_FIELD_NUMBER: _ClassVar[int]
    WATERED_FIELD_NUMBER: _ClassVar[int]
    FERTILIZER_FIELD_NUMBER: _ClassVar[int]
    DEAD_FIELD_NUMBER: _ClassVar[int]
    HARVEST_METHOD_FIELD_NUMBER: _ClassVar[int]
    PHASES_TOTAL_FIELD_NUMBER: _ClassVar[int]
    DAY_OF_CURRENT_PHASE_FIELD_NUMBER: _ClassVar[int]
    REGROWS_AFTER_HARVEST_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    crop_id: str
    harvest_item_id: str
    crop_guard: str
    growth_phase: int
    ready_for_harvest: bool
    watered: bool
    fertilizer: str
    dead: bool
    harvest_method: str
    phases_total: int
    day_of_current_phase: int
    regrows_after_harvest: bool
    display_name: str
    def __init__(self, crop_id: _Optional[str] = ..., harvest_item_id: _Optional[str] = ..., crop_guard: _Optional[str] = ..., growth_phase: _Optional[int] = ..., ready_for_harvest: bool = ..., watered: bool = ..., fertilizer: _Optional[str] = ..., dead: bool = ..., harvest_method: _Optional[str] = ..., phases_total: _Optional[int] = ..., day_of_current_phase: _Optional[int] = ..., regrows_after_harvest: bool = ..., display_name: _Optional[str] = ...) -> None: ...

class ResourceNodeFact(_message.Message):
    __slots__ = ("node_kind", "node_guard", "qualified_item_id", "hits_to_destroy", "required_tool", "is_spawned_object")
    NODE_KIND_FIELD_NUMBER: _ClassVar[int]
    NODE_GUARD_FIELD_NUMBER: _ClassVar[int]
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    HITS_TO_DESTROY_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_TOOL_FIELD_NUMBER: _ClassVar[int]
    IS_SPAWNED_OBJECT_FIELD_NUMBER: _ClassVar[int]
    node_kind: str
    node_guard: str
    qualified_item_id: str
    hits_to_destroy: int
    required_tool: str
    is_spawned_object: bool
    def __init__(self, node_kind: _Optional[str] = ..., node_guard: _Optional[str] = ..., qualified_item_id: _Optional[str] = ..., hits_to_destroy: _Optional[int] = ..., required_tool: _Optional[str] = ..., is_spawned_object: bool = ...) -> None: ...

class LooseItemFact(_message.Message):
    __slots__ = ("qualified_item_id", "item_id", "internal_name", "display_name", "stack", "quality", "can_be_grabbed", "is_spawned_object", "category")
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    INTERNAL_NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    STACK_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    CAN_BE_GRABBED_FIELD_NUMBER: _ClassVar[int]
    IS_SPAWNED_OBJECT_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    qualified_item_id: str
    item_id: str
    internal_name: str
    display_name: str
    stack: int
    quality: int
    can_be_grabbed: bool
    is_spawned_object: bool
    category: int
    def __init__(self, qualified_item_id: _Optional[str] = ..., item_id: _Optional[str] = ..., internal_name: _Optional[str] = ..., display_name: _Optional[str] = ..., stack: _Optional[int] = ..., quality: _Optional[int] = ..., can_be_grabbed: bool = ..., is_spawned_object: bool = ..., category: _Optional[int] = ...) -> None: ...

class MachineFact(_message.Message):
    __slots__ = ("qualified_item_id", "machine_data_id", "internal_name", "display_name", "ready_for_harvest", "minutes_until_ready", "held_object", "last_input_item_id", "machine_state")
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    MACHINE_DATA_ID_FIELD_NUMBER: _ClassVar[int]
    INTERNAL_NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    READY_FOR_HARVEST_FIELD_NUMBER: _ClassVar[int]
    MINUTES_UNTIL_READY_FIELD_NUMBER: _ClassVar[int]
    HELD_OBJECT_FIELD_NUMBER: _ClassVar[int]
    LAST_INPUT_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    MACHINE_STATE_FIELD_NUMBER: _ClassVar[int]
    qualified_item_id: str
    machine_data_id: str
    internal_name: str
    display_name: str
    ready_for_harvest: bool
    minutes_until_ready: int
    held_object: LooseItemFact
    last_input_item_id: str
    machine_state: MachineState
    def __init__(self, qualified_item_id: _Optional[str] = ..., machine_data_id: _Optional[str] = ..., internal_name: _Optional[str] = ..., display_name: _Optional[str] = ..., ready_for_harvest: bool = ..., minutes_until_ready: _Optional[int] = ..., held_object: _Optional[_Union[LooseItemFact, _Mapping]] = ..., last_input_item_id: _Optional[str] = ..., machine_state: _Optional[_Union[MachineState, str]] = ...) -> None: ...

class ContainerFact(_message.Message):
    __slots__ = ("qualified_item_id", "container_kind", "capacity", "items_count", "player_chest", "fridge", "special_chest_type", "color")
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_KIND_FIELD_NUMBER: _ClassVar[int]
    CAPACITY_FIELD_NUMBER: _ClassVar[int]
    ITEMS_COUNT_FIELD_NUMBER: _ClassVar[int]
    PLAYER_CHEST_FIELD_NUMBER: _ClassVar[int]
    FRIDGE_FIELD_NUMBER: _ClassVar[int]
    SPECIAL_CHEST_TYPE_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    qualified_item_id: str
    container_kind: str
    capacity: int
    items_count: int
    player_chest: bool
    fridge: bool
    special_chest_type: str
    color: str
    def __init__(self, qualified_item_id: _Optional[str] = ..., container_kind: _Optional[str] = ..., capacity: _Optional[int] = ..., items_count: _Optional[int] = ..., player_chest: bool = ..., fridge: bool = ..., special_chest_type: _Optional[str] = ..., color: _Optional[str] = ...) -> None: ...

class BedFact(_message.Message):
    __slots__ = ("qualified_item_id", "bed_type", "can_sleep", "rotation", "bounding_tiles", "is_being_slept_in", "display_name", "bed_spot")
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    BED_TYPE_FIELD_NUMBER: _ClassVar[int]
    CAN_SLEEP_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    BOUNDING_TILES_FIELD_NUMBER: _ClassVar[int]
    IS_BEING_SLEPT_IN_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    BED_SPOT_FIELD_NUMBER: _ClassVar[int]
    qualified_item_id: str
    bed_type: str
    can_sleep: bool
    rotation: int
    bounding_tiles: _containers.RepeatedCompositeFieldContainer[_common_pb2.TilePosition]
    is_being_slept_in: bool
    display_name: str
    bed_spot: _common_pb2.TilePoint
    def __init__(self, qualified_item_id: _Optional[str] = ..., bed_type: _Optional[str] = ..., can_sleep: bool = ..., rotation: _Optional[int] = ..., bounding_tiles: _Optional[_Iterable[_Union[_common_pb2.TilePosition, _Mapping]]] = ..., is_being_slept_in: bool = ..., display_name: _Optional[str] = ..., bed_spot: _Optional[_Union[_common_pb2.TilePoint, _Mapping]] = ...) -> None: ...

class FurnitureFact(_message.Message):
    __slots__ = ("qualified_item_id", "furniture_type", "rotation", "can_sit", "bounding_tiles", "display_name", "internal_name", "is_on", "max_rotations")
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    FURNITURE_TYPE_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    CAN_SIT_FIELD_NUMBER: _ClassVar[int]
    BOUNDING_TILES_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    INTERNAL_NAME_FIELD_NUMBER: _ClassVar[int]
    IS_ON_FIELD_NUMBER: _ClassVar[int]
    MAX_ROTATIONS_FIELD_NUMBER: _ClassVar[int]
    qualified_item_id: str
    furniture_type: str
    rotation: int
    can_sit: bool
    bounding_tiles: _containers.RepeatedCompositeFieldContainer[_common_pb2.TilePosition]
    display_name: str
    internal_name: str
    is_on: bool
    max_rotations: int
    def __init__(self, qualified_item_id: _Optional[str] = ..., furniture_type: _Optional[str] = ..., rotation: _Optional[int] = ..., can_sit: bool = ..., bounding_tiles: _Optional[_Iterable[_Union[_common_pb2.TilePosition, _Mapping]]] = ..., display_name: _Optional[str] = ..., internal_name: _Optional[str] = ..., is_on: bool = ..., max_rotations: _Optional[int] = ...) -> None: ...

class ResourceClumpFact(_message.Message):
    __slots__ = ("clump_kind", "clump_guard", "health", "width", "height", "required_tool", "required_tool_level")
    CLUMP_KIND_FIELD_NUMBER: _ClassVar[int]
    CLUMP_GUARD_FIELD_NUMBER: _ClassVar[int]
    HEALTH_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_TOOL_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_TOOL_LEVEL_FIELD_NUMBER: _ClassVar[int]
    clump_kind: str
    clump_guard: str
    health: int
    width: int
    height: int
    required_tool: str
    required_tool_level: int
    def __init__(self, clump_kind: _Optional[str] = ..., clump_guard: _Optional[str] = ..., health: _Optional[int] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., required_tool: _Optional[str] = ..., required_tool_level: _Optional[int] = ...) -> None: ...

class DoorFact(_message.Message):
    __slots__ = ("door_key", "target_map", "target_tile", "locked", "door_kind")
    DOOR_KEY_FIELD_NUMBER: _ClassVar[int]
    TARGET_MAP_FIELD_NUMBER: _ClassVar[int]
    TARGET_TILE_FIELD_NUMBER: _ClassVar[int]
    LOCKED_FIELD_NUMBER: _ClassVar[int]
    DOOR_KIND_FIELD_NUMBER: _ClassVar[int]
    door_key: str
    target_map: str
    target_tile: _common_pb2.TilePoint
    locked: bool
    door_kind: str
    def __init__(self, door_key: _Optional[str] = ..., target_map: _Optional[str] = ..., target_tile: _Optional[_Union[_common_pb2.TilePoint, _Mapping]] = ..., locked: bool = ..., door_kind: _Optional[str] = ...) -> None: ...

class WarpFact(_message.Message):
    __slots__ = ("warp_key", "target_map", "target_x", "target_y", "npc_only")
    WARP_KEY_FIELD_NUMBER: _ClassVar[int]
    TARGET_MAP_FIELD_NUMBER: _ClassVar[int]
    TARGET_X_FIELD_NUMBER: _ClassVar[int]
    TARGET_Y_FIELD_NUMBER: _ClassVar[int]
    NPC_ONLY_FIELD_NUMBER: _ClassVar[int]
    warp_key: str
    target_map: str
    target_x: int
    target_y: int
    npc_only: bool
    def __init__(self, warp_key: _Optional[str] = ..., target_map: _Optional[str] = ..., target_x: _Optional[int] = ..., target_y: _Optional[int] = ..., npc_only: bool = ...) -> None: ...

class CharacterFact(_message.Message):
    __slots__ = ("ref", "type", "fact_kind", "name", "display_name", "position", "summary", "npc", "monster", "farm_animal", "pet", "horse", "debug")
    REF_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    FACT_KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    NPC_FIELD_NUMBER: _ClassVar[int]
    MONSTER_FIELD_NUMBER: _ClassVar[int]
    FARM_ANIMAL_FIELD_NUMBER: _ClassVar[int]
    PET_FIELD_NUMBER: _ClassVar[int]
    HORSE_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    ref: _ref_pb2.RefId
    type: str
    fact_kind: str
    name: str
    display_name: str
    position: _common_pb2.TilePosition
    summary: str
    npc: NpcFact
    monster: MonsterFact
    farm_animal: FarmAnimalFact
    pet: PetFact
    horse: HorseFact
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, ref: _Optional[_Union[_ref_pb2.RefId, _Mapping]] = ..., type: _Optional[str] = ..., fact_kind: _Optional[str] = ..., name: _Optional[str] = ..., display_name: _Optional[str] = ..., position: _Optional[_Union[_common_pb2.TilePosition, _Mapping]] = ..., summary: _Optional[str] = ..., npc: _Optional[_Union[NpcFact, _Mapping]] = ..., monster: _Optional[_Union[MonsterFact, _Mapping]] = ..., farm_animal: _Optional[_Union[FarmAnimalFact, _Mapping]] = ..., pet: _Optional[_Union[PetFact, _Mapping]] = ..., horse: _Optional[_Union[HorseFact, _Mapping]] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...

class NpcFact(_message.Message):
    __slots__ = ("npc_key", "current_map_token", "can_socialize", "schedule_key", "friendship_points", "facing", "birthday_season", "birthday_day", "has_dialogue")
    NPC_KEY_FIELD_NUMBER: _ClassVar[int]
    CURRENT_MAP_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CAN_SOCIALIZE_FIELD_NUMBER: _ClassVar[int]
    SCHEDULE_KEY_FIELD_NUMBER: _ClassVar[int]
    FRIENDSHIP_POINTS_FIELD_NUMBER: _ClassVar[int]
    FACING_FIELD_NUMBER: _ClassVar[int]
    BIRTHDAY_SEASON_FIELD_NUMBER: _ClassVar[int]
    BIRTHDAY_DAY_FIELD_NUMBER: _ClassVar[int]
    HAS_DIALOGUE_FIELD_NUMBER: _ClassVar[int]
    npc_key: str
    current_map_token: str
    can_socialize: bool
    schedule_key: str
    friendship_points: int
    facing: _common_pb2.Direction
    birthday_season: str
    birthday_day: int
    has_dialogue: bool
    def __init__(self, npc_key: _Optional[str] = ..., current_map_token: _Optional[str] = ..., can_socialize: bool = ..., schedule_key: _Optional[str] = ..., friendship_points: _Optional[int] = ..., facing: _Optional[_Union[_common_pb2.Direction, str]] = ..., birthday_season: _Optional[str] = ..., birthday_day: _Optional[int] = ..., has_dialogue: bool = ...) -> None: ...

class MonsterFact(_message.Message):
    __slots__ = ("monster_key", "character_guid", "current_map_token", "health", "is_moving", "facing", "max_health", "damage_to_farmer")
    MONSTER_KEY_FIELD_NUMBER: _ClassVar[int]
    CHARACTER_GUID_FIELD_NUMBER: _ClassVar[int]
    CURRENT_MAP_TOKEN_FIELD_NUMBER: _ClassVar[int]
    HEALTH_FIELD_NUMBER: _ClassVar[int]
    IS_MOVING_FIELD_NUMBER: _ClassVar[int]
    FACING_FIELD_NUMBER: _ClassVar[int]
    MAX_HEALTH_FIELD_NUMBER: _ClassVar[int]
    DAMAGE_TO_FARMER_FIELD_NUMBER: _ClassVar[int]
    monster_key: str
    character_guid: str
    current_map_token: str
    health: int
    is_moving: bool
    facing: _common_pb2.Direction
    max_health: int
    damage_to_farmer: int
    def __init__(self, monster_key: _Optional[str] = ..., character_guid: _Optional[str] = ..., current_map_token: _Optional[str] = ..., health: _Optional[int] = ..., is_moving: bool = ..., facing: _Optional[_Union[_common_pb2.Direction, str]] = ..., max_health: _Optional[int] = ..., damage_to_farmer: _Optional[int] = ...) -> None: ...

class FarmAnimalFact(_message.Message):
    __slots__ = ("animal_id", "animal_type", "home_building_type", "produce_ready", "friendship", "home_building_id", "was_petted", "was_auto_petted", "happiness")
    ANIMAL_ID_FIELD_NUMBER: _ClassVar[int]
    ANIMAL_TYPE_FIELD_NUMBER: _ClassVar[int]
    HOME_BUILDING_TYPE_FIELD_NUMBER: _ClassVar[int]
    PRODUCE_READY_FIELD_NUMBER: _ClassVar[int]
    FRIENDSHIP_FIELD_NUMBER: _ClassVar[int]
    HOME_BUILDING_ID_FIELD_NUMBER: _ClassVar[int]
    WAS_PETTED_FIELD_NUMBER: _ClassVar[int]
    WAS_AUTO_PETTED_FIELD_NUMBER: _ClassVar[int]
    HAPPINESS_FIELD_NUMBER: _ClassVar[int]
    animal_id: str
    animal_type: str
    home_building_type: str
    produce_ready: bool
    friendship: int
    home_building_id: str
    was_petted: bool
    was_auto_petted: bool
    happiness: int
    def __init__(self, animal_id: _Optional[str] = ..., animal_type: _Optional[str] = ..., home_building_type: _Optional[str] = ..., produce_ready: bool = ..., friendship: _Optional[int] = ..., home_building_id: _Optional[str] = ..., was_petted: bool = ..., was_auto_petted: bool = ..., happiness: _Optional[int] = ...) -> None: ...

class PetFact(_message.Message):
    __slots__ = ("pet_guid", "pet_type", "friendship", "petted_today")
    PET_GUID_FIELD_NUMBER: _ClassVar[int]
    PET_TYPE_FIELD_NUMBER: _ClassVar[int]
    FRIENDSHIP_FIELD_NUMBER: _ClassVar[int]
    PETTED_TODAY_FIELD_NUMBER: _ClassVar[int]
    pet_guid: str
    pet_type: str
    friendship: int
    petted_today: bool
    def __init__(self, pet_guid: _Optional[str] = ..., pet_type: _Optional[str] = ..., friendship: _Optional[int] = ..., petted_today: bool = ...) -> None: ...

class HorseFact(_message.Message):
    __slots__ = ("horse_guid", "owner_id", "has_rider", "display_name")
    HORSE_GUID_FIELD_NUMBER: _ClassVar[int]
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    HAS_RIDER_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    horse_guid: str
    owner_id: str
    has_rider: bool
    display_name: str
    def __init__(self, horse_guid: _Optional[str] = ..., owner_id: _Optional[str] = ..., has_rider: bool = ..., display_name: _Optional[str] = ...) -> None: ...

class InventorySnapshotFact(_message.Message):
    __slots__ = ("container_kind", "container_ref", "slots_used", "slots_total", "items", "debug")
    CONTAINER_KIND_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_REF_FIELD_NUMBER: _ClassVar[int]
    SLOTS_USED_FIELD_NUMBER: _ClassVar[int]
    SLOTS_TOTAL_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    container_kind: str
    container_ref: _ref_pb2.RefId
    slots_used: int
    slots_total: int
    items: _containers.RepeatedCompositeFieldContainer[InventoryItemFact]
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, container_kind: _Optional[str] = ..., container_ref: _Optional[_Union[_ref_pb2.RefId, _Mapping]] = ..., slots_used: _Optional[int] = ..., slots_total: _Optional[int] = ..., items: _Optional[_Iterable[_Union[InventoryItemFact, _Mapping]]] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...

class InventoryItemFact(_message.Message):
    __slots__ = ("ref", "container_kind", "container_locator", "slot", "qualified_item_id", "item_id", "internal_name", "display_name", "stack", "quality", "category", "can_stack", "upgrade_level", "is_active", "is_recipe", "base_sell_price", "category_name", "debug")
    REF_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_KIND_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_LOCATOR_FIELD_NUMBER: _ClassVar[int]
    SLOT_FIELD_NUMBER: _ClassVar[int]
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    INTERNAL_NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    STACK_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    CAN_STACK_FIELD_NUMBER: _ClassVar[int]
    UPGRADE_LEVEL_FIELD_NUMBER: _ClassVar[int]
    IS_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    IS_RECIPE_FIELD_NUMBER: _ClassVar[int]
    BASE_SELL_PRICE_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_NAME_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    ref: _ref_pb2.RefId
    container_kind: str
    container_locator: str
    slot: int
    qualified_item_id: str
    item_id: str
    internal_name: str
    display_name: str
    stack: int
    quality: int
    category: int
    can_stack: bool
    upgrade_level: int
    is_active: bool
    is_recipe: bool
    base_sell_price: int
    category_name: str
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, ref: _Optional[_Union[_ref_pb2.RefId, _Mapping]] = ..., container_kind: _Optional[str] = ..., container_locator: _Optional[str] = ..., slot: _Optional[int] = ..., qualified_item_id: _Optional[str] = ..., item_id: _Optional[str] = ..., internal_name: _Optional[str] = ..., display_name: _Optional[str] = ..., stack: _Optional[int] = ..., quality: _Optional[int] = ..., category: _Optional[int] = ..., can_stack: bool = ..., upgrade_level: _Optional[int] = ..., is_active: bool = ..., is_recipe: bool = ..., base_sell_price: _Optional[int] = ..., category_name: _Optional[str] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...

class UiSnapshotFact(_message.Message):
    __slots__ = ("menu_open", "menu", "elements", "debug")
    MENU_OPEN_FIELD_NUMBER: _ClassVar[int]
    MENU_FIELD_NUMBER: _ClassVar[int]
    ELEMENTS_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    menu_open: bool
    menu: UiMenuFact
    elements: _containers.RepeatedCompositeFieldContainer[UiElementFact]
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, menu_open: bool = ..., menu: _Optional[_Union[UiMenuFact, _Mapping]] = ..., elements: _Optional[_Iterable[_Union[UiElementFact, _Mapping]]] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...

class UiMenuFact(_message.Message):
    __slots__ = ("menu_token", "menu_type", "active_tab", "title", "modal", "dialogue_text", "is_question", "shop_currency")
    MENU_TOKEN_FIELD_NUMBER: _ClassVar[int]
    MENU_TYPE_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_TAB_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    MODAL_FIELD_NUMBER: _ClassVar[int]
    DIALOGUE_TEXT_FIELD_NUMBER: _ClassVar[int]
    IS_QUESTION_FIELD_NUMBER: _ClassVar[int]
    SHOP_CURRENCY_FIELD_NUMBER: _ClassVar[int]
    menu_token: str
    menu_type: str
    active_tab: str
    title: str
    modal: bool
    dialogue_text: str
    is_question: bool
    shop_currency: int
    def __init__(self, menu_token: _Optional[str] = ..., menu_type: _Optional[str] = ..., active_tab: _Optional[str] = ..., title: _Optional[str] = ..., modal: bool = ..., dialogue_text: _Optional[str] = ..., is_question: bool = ..., shop_currency: _Optional[int] = ...) -> None: ...

class UiElementFact(_message.Message):
    __slots__ = ("ref", "type", "element_token", "identity_guard", "label", "visible", "enabled", "position", "index", "debug", "price", "stock", "qualified_item_id", "trade_item_id", "response_key")
    REF_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    ELEMENT_TOKEN_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_GUARD_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    VISIBLE_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    STOCK_FIELD_NUMBER: _ClassVar[int]
    QUALIFIED_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    TRADE_ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_KEY_FIELD_NUMBER: _ClassVar[int]
    ref: _ref_pb2.RefId
    type: str
    element_token: str
    identity_guard: str
    label: str
    visible: bool
    enabled: bool
    position: _common_pb2.PixelPosition
    index: int
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    price: int
    stock: int
    qualified_item_id: str
    trade_item_id: str
    response_key: str
    def __init__(self, ref: _Optional[_Union[_ref_pb2.RefId, _Mapping]] = ..., type: _Optional[str] = ..., element_token: _Optional[str] = ..., identity_guard: _Optional[str] = ..., label: _Optional[str] = ..., visible: bool = ..., enabled: bool = ..., position: _Optional[_Union[_common_pb2.PixelPosition, _Mapping]] = ..., index: _Optional[int] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ..., price: _Optional[int] = ..., stock: _Optional[int] = ..., qualified_item_id: _Optional[str] = ..., trade_item_id: _Optional[str] = ..., response_key: _Optional[str] = ...) -> None: ...

class QuestFact(_message.Message):
    __slots__ = ("ref", "type", "quest_key", "title", "status", "days_left", "completed", "reward_claimed", "objectives", "is_daily", "can_be_cancelled", "money_reward", "requester", "order_type", "debug")
    REF_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    QUEST_KEY_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DAYS_LEFT_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_FIELD_NUMBER: _ClassVar[int]
    REWARD_CLAIMED_FIELD_NUMBER: _ClassVar[int]
    OBJECTIVES_FIELD_NUMBER: _ClassVar[int]
    IS_DAILY_FIELD_NUMBER: _ClassVar[int]
    CAN_BE_CANCELLED_FIELD_NUMBER: _ClassVar[int]
    MONEY_REWARD_FIELD_NUMBER: _ClassVar[int]
    REQUESTER_FIELD_NUMBER: _ClassVar[int]
    ORDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    ref: _ref_pb2.RefId
    type: str
    quest_key: str
    title: str
    status: QuestStatus
    days_left: int
    completed: bool
    reward_claimed: bool
    objectives: _containers.RepeatedCompositeFieldContainer[QuestObjectiveFact]
    is_daily: bool
    can_be_cancelled: bool
    money_reward: int
    requester: str
    order_type: str
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, ref: _Optional[_Union[_ref_pb2.RefId, _Mapping]] = ..., type: _Optional[str] = ..., quest_key: _Optional[str] = ..., title: _Optional[str] = ..., status: _Optional[_Union[QuestStatus, str]] = ..., days_left: _Optional[int] = ..., completed: bool = ..., reward_claimed: bool = ..., objectives: _Optional[_Iterable[_Union[QuestObjectiveFact, _Mapping]]] = ..., is_daily: bool = ..., can_be_cancelled: bool = ..., money_reward: _Optional[int] = ..., requester: _Optional[str] = ..., order_type: _Optional[str] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...
