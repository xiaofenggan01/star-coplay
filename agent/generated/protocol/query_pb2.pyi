from . import common_pb2 as _common_pb2
from . import ref_pb2 as _ref_pb2
from . import query_facts_pb2 as _query_facts_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class QueryWarning(_message.Message):
    __slots__ = ("code", "message", "detail", "ref")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    REF_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    detail: str
    ref: _ref_pb2.RefId
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., detail: _Optional[str] = ..., ref: _Optional[_Union[_ref_pb2.RefId, _Mapping]] = ...) -> None: ...

class QueryRuntimeSnapshotParams(_message.Message):
    __slots__ = ("include_player", "include_world_time", "include_weather", "include_menu_state")
    INCLUDE_PLAYER_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_WORLD_TIME_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_WEATHER_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_MENU_STATE_FIELD_NUMBER: _ClassVar[int]
    include_player: bool
    include_world_time: bool
    include_weather: bool
    include_menu_state: bool
    def __init__(self, include_player: bool = ..., include_world_time: bool = ..., include_weather: bool = ..., include_menu_state: bool = ...) -> None: ...

class QueryWorldRegionParams(_message.Message):
    __slots__ = ("map_token", "region", "center", "radius", "world_entity_types", "include_tiles", "include_characters", "include_debug")
    MAP_TOKEN_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    CENTER_FIELD_NUMBER: _ClassVar[int]
    RADIUS_FIELD_NUMBER: _ClassVar[int]
    WORLD_ENTITY_TYPES_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_TILES_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_CHARACTERS_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_DEBUG_FIELD_NUMBER: _ClassVar[int]
    map_token: str
    region: _common_pb2.TileRect
    center: _common_pb2.TilePosition
    radius: int
    world_entity_types: _containers.RepeatedScalarFieldContainer[str]
    include_tiles: bool
    include_characters: bool
    include_debug: bool
    def __init__(self, map_token: _Optional[str] = ..., region: _Optional[_Union[_common_pb2.TileRect, _Mapping]] = ..., center: _Optional[_Union[_common_pb2.TilePosition, _Mapping]] = ..., radius: _Optional[int] = ..., world_entity_types: _Optional[_Iterable[str]] = ..., include_tiles: bool = ..., include_characters: bool = ..., include_debug: bool = ...) -> None: ...

class QueryInventorySnapshotParams(_message.Message):
    __slots__ = ("container_kind", "container_ref", "include_empty_slots", "include_details", "include_debug")
    CONTAINER_KIND_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_REF_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_EMPTY_SLOTS_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_DETAILS_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_DEBUG_FIELD_NUMBER: _ClassVar[int]
    container_kind: str
    container_ref: _ref_pb2.RefId
    include_empty_slots: bool
    include_details: bool
    include_debug: bool
    def __init__(self, container_kind: _Optional[str] = ..., container_ref: _Optional[_Union[_ref_pb2.RefId, _Mapping]] = ..., include_empty_slots: bool = ..., include_details: bool = ..., include_debug: bool = ...) -> None: ...

class QueryUiSnapshotParams(_message.Message):
    __slots__ = ("include_elements", "include_debug")
    INCLUDE_ELEMENTS_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_DEBUG_FIELD_NUMBER: _ClassVar[int]
    include_elements: bool
    include_debug: bool
    def __init__(self, include_elements: bool = ..., include_debug: bool = ...) -> None: ...

class QueryInspectRefsParams(_message.Message):
    __slots__ = ("refs", "include_debug")
    REFS_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_DEBUG_FIELD_NUMBER: _ClassVar[int]
    refs: _containers.RepeatedCompositeFieldContainer[_ref_pb2.RefId]
    include_debug: bool
    def __init__(self, refs: _Optional[_Iterable[_Union[_ref_pb2.RefId, _Mapping]]] = ..., include_debug: bool = ...) -> None: ...

class QueryRuntimeSnapshotResult(_message.Message):
    __slots__ = ("fact", "warnings")
    FACT_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    fact: _query_facts_pb2.RuntimeSnapshotFact
    warnings: _containers.RepeatedCompositeFieldContainer[QueryWarning]
    def __init__(self, fact: _Optional[_Union[_query_facts_pb2.RuntimeSnapshotFact, _Mapping]] = ..., warnings: _Optional[_Iterable[_Union[QueryWarning, _Mapping]]] = ...) -> None: ...

class QueryWorldRegionResult(_message.Message):
    __slots__ = ("fact", "warnings")
    FACT_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    fact: _query_facts_pb2.WorldRegionFact
    warnings: _containers.RepeatedCompositeFieldContainer[QueryWarning]
    def __init__(self, fact: _Optional[_Union[_query_facts_pb2.WorldRegionFact, _Mapping]] = ..., warnings: _Optional[_Iterable[_Union[QueryWarning, _Mapping]]] = ...) -> None: ...

class QueryInventorySnapshotResult(_message.Message):
    __slots__ = ("fact", "warnings")
    FACT_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    fact: _query_facts_pb2.InventorySnapshotFact
    warnings: _containers.RepeatedCompositeFieldContainer[QueryWarning]
    def __init__(self, fact: _Optional[_Union[_query_facts_pb2.InventorySnapshotFact, _Mapping]] = ..., warnings: _Optional[_Iterable[_Union[QueryWarning, _Mapping]]] = ...) -> None: ...

class QueryUiSnapshotResult(_message.Message):
    __slots__ = ("fact", "warnings")
    FACT_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    fact: _query_facts_pb2.UiSnapshotFact
    warnings: _containers.RepeatedCompositeFieldContainer[QueryWarning]
    def __init__(self, fact: _Optional[_Union[_query_facts_pb2.UiSnapshotFact, _Mapping]] = ..., warnings: _Optional[_Iterable[_Union[QueryWarning, _Mapping]]] = ...) -> None: ...

class QueryInspectRefsResult(_message.Message):
    __slots__ = ("inspected", "warnings")
    INSPECTED_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    inspected: _containers.RepeatedCompositeFieldContainer[InspectedRef]
    warnings: _containers.RepeatedCompositeFieldContainer[QueryWarning]
    def __init__(self, inspected: _Optional[_Iterable[_Union[InspectedRef, _Mapping]]] = ..., warnings: _Optional[_Iterable[_Union[QueryWarning, _Mapping]]] = ...) -> None: ...

class InspectedRef(_message.Message):
    __slots__ = ("resolution", "ref", "ref_type", "type", "fact_kind", "summary", "debug_reason", "runtime", "world_entity", "character", "inventory_item", "inventory_snapshot", "ui_element", "ui_snapshot", "quest", "tile", "debug")
    RESOLUTION_FIELD_NUMBER: _ClassVar[int]
    REF_FIELD_NUMBER: _ClassVar[int]
    REF_TYPE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    FACT_KIND_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    DEBUG_REASON_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_FIELD_NUMBER: _ClassVar[int]
    WORLD_ENTITY_FIELD_NUMBER: _ClassVar[int]
    CHARACTER_FIELD_NUMBER: _ClassVar[int]
    INVENTORY_ITEM_FIELD_NUMBER: _ClassVar[int]
    INVENTORY_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    UI_ELEMENT_FIELD_NUMBER: _ClassVar[int]
    UI_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    QUEST_FIELD_NUMBER: _ClassVar[int]
    TILE_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    resolution: _ref_pb2.RefResolution
    ref: str
    ref_type: str
    type: str
    fact_kind: str
    summary: str
    debug_reason: str
    runtime: _query_facts_pb2.RuntimeSnapshotFact
    world_entity: _query_facts_pb2.WorldEntityFact
    character: _query_facts_pb2.CharacterFact
    inventory_item: _query_facts_pb2.InventoryItemFact
    inventory_snapshot: _query_facts_pb2.InventorySnapshotFact
    ui_element: _query_facts_pb2.UiElementFact
    ui_snapshot: _query_facts_pb2.UiSnapshotFact
    quest: _query_facts_pb2.QuestFact
    tile: _query_facts_pb2.TileFact
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, resolution: _Optional[_Union[_ref_pb2.RefResolution, _Mapping]] = ..., ref: _Optional[str] = ..., ref_type: _Optional[str] = ..., type: _Optional[str] = ..., fact_kind: _Optional[str] = ..., summary: _Optional[str] = ..., debug_reason: _Optional[str] = ..., runtime: _Optional[_Union[_query_facts_pb2.RuntimeSnapshotFact, _Mapping]] = ..., world_entity: _Optional[_Union[_query_facts_pb2.WorldEntityFact, _Mapping]] = ..., character: _Optional[_Union[_query_facts_pb2.CharacterFact, _Mapping]] = ..., inventory_item: _Optional[_Union[_query_facts_pb2.InventoryItemFact, _Mapping]] = ..., inventory_snapshot: _Optional[_Union[_query_facts_pb2.InventorySnapshotFact, _Mapping]] = ..., ui_element: _Optional[_Union[_query_facts_pb2.UiElementFact, _Mapping]] = ..., ui_snapshot: _Optional[_Union[_query_facts_pb2.UiSnapshotFact, _Mapping]] = ..., quest: _Optional[_Union[_query_facts_pb2.QuestFact, _Mapping]] = ..., tile: _Optional[_Union[_query_facts_pb2.TileFact, _Mapping]] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...
