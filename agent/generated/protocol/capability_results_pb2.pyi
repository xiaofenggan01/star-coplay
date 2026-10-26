from . import common_pb2 as _common_pb2
from . import capability_params_pb2 as _capability_params_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SayResult(_message.Message):
    __slots__ = ("content_length",)
    CONTENT_LENGTH_FIELD_NUMBER: _ClassVar[int]
    content_length: int
    def __init__(self, content_length: _Optional[int] = ...) -> None: ...

class EmoteResult(_message.Message):
    __slots__ = ("emote_type",)
    EMOTE_TYPE_FIELD_NUMBER: _ClassVar[int]
    emote_type: _capability_params_pb2.EmoteType
    def __init__(self, emote_type: _Optional[_Union[_capability_params_pb2.EmoteType, str]] = ...) -> None: ...

class FaceResult(_message.Message):
    __slots__ = ("final_direction", "already_facing")
    FINAL_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    ALREADY_FACING_FIELD_NUMBER: _ClassVar[int]
    final_direction: _common_pb2.Direction
    already_facing: bool
    def __init__(self, final_direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., already_facing: bool = ...) -> None: ...

class MoveToResult(_message.Message):
    __slots__ = ("mode", "start", "target_x", "target_y", "resolved_destination_x", "resolved_destination_y", "final", "requested_direction", "requested_face_direction", "stand_side", "reached_destination", "warp_triggered", "timing")
    MODE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    TARGET_X_FIELD_NUMBER: _ClassVar[int]
    TARGET_Y_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_DESTINATION_X_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_DESTINATION_Y_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_FACE_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    STAND_SIDE_FIELD_NUMBER: _ClassVar[int]
    REACHED_DESTINATION_FIELD_NUMBER: _ClassVar[int]
    WARP_TRIGGERED_FIELD_NUMBER: _ClassVar[int]
    TIMING_FIELD_NUMBER: _ClassVar[int]
    mode: _capability_params_pb2.MoveToMode
    start: _common_pb2.LocationSnapshot
    target_x: int
    target_y: int
    resolved_destination_x: int
    resolved_destination_y: int
    final: _common_pb2.LocationSnapshot
    requested_direction: _common_pb2.Direction
    requested_face_direction: _common_pb2.Direction
    stand_side: _common_pb2.Direction
    reached_destination: bool
    warp_triggered: bool
    timing: _common_pb2.ExecutionTiming
    def __init__(self, mode: _Optional[_Union[_capability_params_pb2.MoveToMode, str]] = ..., start: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ..., target_x: _Optional[int] = ..., target_y: _Optional[int] = ..., resolved_destination_x: _Optional[int] = ..., resolved_destination_y: _Optional[int] = ..., final: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ..., requested_direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., requested_face_direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., stand_side: _Optional[_Union[_common_pb2.Direction, str]] = ..., reached_destination: bool = ..., warp_triggered: bool = ..., timing: _Optional[_Union[_common_pb2.ExecutionTiming, _Mapping]] = ...) -> None: ...

class GoToResult(_message.Message):
    __slots__ = ("start", "target_map", "target_x", "target_y", "final", "arrival_mode", "stand_side", "face_direction", "route_maps", "steps_total", "steps_completed", "failed_step_index", "failed_step_result", "final_move_result", "timing")
    START_FIELD_NUMBER: _ClassVar[int]
    TARGET_MAP_FIELD_NUMBER: _ClassVar[int]
    TARGET_X_FIELD_NUMBER: _ClassVar[int]
    TARGET_Y_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    ARRIVAL_MODE_FIELD_NUMBER: _ClassVar[int]
    STAND_SIDE_FIELD_NUMBER: _ClassVar[int]
    FACE_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    ROUTE_MAPS_FIELD_NUMBER: _ClassVar[int]
    STEPS_TOTAL_FIELD_NUMBER: _ClassVar[int]
    STEPS_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    FAILED_STEP_INDEX_FIELD_NUMBER: _ClassVar[int]
    FAILED_STEP_RESULT_FIELD_NUMBER: _ClassVar[int]
    FINAL_MOVE_RESULT_FIELD_NUMBER: _ClassVar[int]
    TIMING_FIELD_NUMBER: _ClassVar[int]
    start: _common_pb2.LocationSnapshot
    target_map: str
    target_x: int
    target_y: int
    final: _common_pb2.LocationSnapshot
    arrival_mode: _capability_params_pb2.ArrivalMode
    stand_side: _common_pb2.Direction
    face_direction: _common_pb2.Direction
    route_maps: _containers.RepeatedScalarFieldContainer[str]
    steps_total: int
    steps_completed: int
    failed_step_index: int
    failed_step_result: MoveToResult
    final_move_result: MoveToResult
    timing: _common_pb2.ExecutionTiming
    def __init__(self, start: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ..., target_map: _Optional[str] = ..., target_x: _Optional[int] = ..., target_y: _Optional[int] = ..., final: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ..., arrival_mode: _Optional[_Union[_capability_params_pb2.ArrivalMode, str]] = ..., stand_side: _Optional[_Union[_common_pb2.Direction, str]] = ..., face_direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., route_maps: _Optional[_Iterable[str]] = ..., steps_total: _Optional[int] = ..., steps_completed: _Optional[int] = ..., failed_step_index: _Optional[int] = ..., failed_step_result: _Optional[_Union[MoveToResult, _Mapping]] = ..., final_move_result: _Optional[_Union[MoveToResult, _Mapping]] = ..., timing: _Optional[_Union[_common_pb2.ExecutionTiming, _Mapping]] = ...) -> None: ...

class GoToBedResult(_message.Message):
    __slots__ = ("date_before", "date_after", "time_before", "time_after", "location_before", "location_after", "stamina", "money_before", "money_after", "bed_position", "used_tp", "move_to_bed_result", "sleep_prompt_seen", "sleep_confirmed", "post_sleep_ui_steps", "decision_required", "day_advanced", "player_can_move_after", "timing", "decision_kind", "blocking_menu_type", "phase")
    DATE_BEFORE_FIELD_NUMBER: _ClassVar[int]
    DATE_AFTER_FIELD_NUMBER: _ClassVar[int]
    TIME_BEFORE_FIELD_NUMBER: _ClassVar[int]
    TIME_AFTER_FIELD_NUMBER: _ClassVar[int]
    LOCATION_BEFORE_FIELD_NUMBER: _ClassVar[int]
    LOCATION_AFTER_FIELD_NUMBER: _ClassVar[int]
    STAMINA_FIELD_NUMBER: _ClassVar[int]
    MONEY_BEFORE_FIELD_NUMBER: _ClassVar[int]
    MONEY_AFTER_FIELD_NUMBER: _ClassVar[int]
    BED_POSITION_FIELD_NUMBER: _ClassVar[int]
    USED_TP_FIELD_NUMBER: _ClassVar[int]
    MOVE_TO_BED_RESULT_FIELD_NUMBER: _ClassVar[int]
    SLEEP_PROMPT_SEEN_FIELD_NUMBER: _ClassVar[int]
    SLEEP_CONFIRMED_FIELD_NUMBER: _ClassVar[int]
    POST_SLEEP_UI_STEPS_FIELD_NUMBER: _ClassVar[int]
    DECISION_REQUIRED_FIELD_NUMBER: _ClassVar[int]
    DAY_ADVANCED_FIELD_NUMBER: _ClassVar[int]
    PLAYER_CAN_MOVE_AFTER_FIELD_NUMBER: _ClassVar[int]
    TIMING_FIELD_NUMBER: _ClassVar[int]
    DECISION_KIND_FIELD_NUMBER: _ClassVar[int]
    BLOCKING_MENU_TYPE_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    date_before: str
    date_after: str
    time_before: int
    time_after: int
    location_before: _common_pb2.LocationSnapshot
    location_after: _common_pb2.LocationSnapshot
    stamina: _common_pb2.ResourceDelta
    money_before: int
    money_after: int
    bed_position: _common_pb2.TilePosition
    used_tp: bool
    move_to_bed_result: MoveToResult
    sleep_prompt_seen: bool
    sleep_confirmed: bool
    post_sleep_ui_steps: int
    decision_required: bool
    day_advanced: bool
    player_can_move_after: bool
    timing: _common_pb2.ExecutionTiming
    decision_kind: str
    blocking_menu_type: str
    phase: str
    def __init__(self, date_before: _Optional[str] = ..., date_after: _Optional[str] = ..., time_before: _Optional[int] = ..., time_after: _Optional[int] = ..., location_before: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ..., location_after: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ..., stamina: _Optional[_Union[_common_pb2.ResourceDelta, _Mapping]] = ..., money_before: _Optional[int] = ..., money_after: _Optional[int] = ..., bed_position: _Optional[_Union[_common_pb2.TilePosition, _Mapping]] = ..., used_tp: bool = ..., move_to_bed_result: _Optional[_Union[MoveToResult, _Mapping]] = ..., sleep_prompt_seen: bool = ..., sleep_confirmed: bool = ..., post_sleep_ui_steps: _Optional[int] = ..., decision_required: bool = ..., day_advanced: bool = ..., player_can_move_after: bool = ..., timing: _Optional[_Union[_common_pb2.ExecutionTiming, _Mapping]] = ..., decision_kind: _Optional[str] = ..., blocking_menu_type: _Optional[str] = ..., phase: _Optional[str] = ...) -> None: ...

class InteractResult(_message.Message):
    __slots__ = ("effect",)
    EFFECT_FIELD_NUMBER: _ClassVar[int]
    effect: _common_pb2.TileEffectResult
    def __init__(self, effect: _Optional[_Union[_common_pb2.TileEffectResult, _Mapping]] = ...) -> None: ...

class UseToolResult(_message.Message):
    __slots__ = ("effect", "tool_name", "charge_level")
    EFFECT_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    CHARGE_LEVEL_FIELD_NUMBER: _ClassVar[int]
    effect: _common_pb2.TileEffectResult
    tool_name: str
    charge_level: int
    def __init__(self, effect: _Optional[_Union[_common_pb2.TileEffectResult, _Mapping]] = ..., tool_name: _Optional[str] = ..., charge_level: _Optional[int] = ...) -> None: ...

class EquipResult(_message.Message):
    __slots__ = ("selected_by", "slot", "item_name", "item_display_name", "item_category", "method", "swapped", "from_slot", "to_slot", "swapped_out")
    SELECTED_BY_FIELD_NUMBER: _ClassVar[int]
    SLOT_FIELD_NUMBER: _ClassVar[int]
    ITEM_NAME_FIELD_NUMBER: _ClassVar[int]
    ITEM_DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    ITEM_CATEGORY_FIELD_NUMBER: _ClassVar[int]
    METHOD_FIELD_NUMBER: _ClassVar[int]
    SWAPPED_FIELD_NUMBER: _ClassVar[int]
    FROM_SLOT_FIELD_NUMBER: _ClassVar[int]
    TO_SLOT_FIELD_NUMBER: _ClassVar[int]
    SWAPPED_OUT_FIELD_NUMBER: _ClassVar[int]
    selected_by: str
    slot: int
    item_name: str
    item_display_name: str
    item_category: _common_pb2.ItemCategory
    method: str
    swapped: bool
    from_slot: int
    to_slot: int
    swapped_out: str
    def __init__(self, selected_by: _Optional[str] = ..., slot: _Optional[int] = ..., item_name: _Optional[str] = ..., item_display_name: _Optional[str] = ..., item_category: _Optional[_Union[_common_pb2.ItemCategory, str]] = ..., method: _Optional[str] = ..., swapped: bool = ..., from_slot: _Optional[int] = ..., to_slot: _Optional[int] = ..., swapped_out: _Optional[str] = ...) -> None: ...

class MenuResult(_message.Message):
    __slots__ = ("action", "transition", "timing", "component_id", "source", "already_closed", "click_count")
    ACTION_FIELD_NUMBER: _ClassVar[int]
    TRANSITION_FIELD_NUMBER: _ClassVar[int]
    TIMING_FIELD_NUMBER: _ClassVar[int]
    COMPONENT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    ALREADY_CLOSED_FIELD_NUMBER: _ClassVar[int]
    CLICK_COUNT_FIELD_NUMBER: _ClassVar[int]
    action: str
    transition: _common_pb2.MenuTransition
    timing: _common_pb2.ExecutionTiming
    component_id: int
    source: _common_pb2.MenuComponentSource
    already_closed: bool
    click_count: int
    def __init__(self, action: _Optional[str] = ..., transition: _Optional[_Union[_common_pb2.MenuTransition, _Mapping]] = ..., timing: _Optional[_Union[_common_pb2.ExecutionTiming, _Mapping]] = ..., component_id: _Optional[int] = ..., source: _Optional[_Union[_common_pb2.MenuComponentSource, str]] = ..., already_closed: bool = ..., click_count: _Optional[int] = ...) -> None: ...

class MenuSnapshot(_message.Message):
    __slots__ = ("menu_type", "active_tab", "components", "snapshot_id")
    MENU_TYPE_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_TAB_FIELD_NUMBER: _ClassVar[int]
    COMPONENTS_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    menu_type: str
    active_tab: str
    components: _containers.RepeatedCompositeFieldContainer[MenuComponent]
    snapshot_id: str
    def __init__(self, menu_type: _Optional[str] = ..., active_tab: _Optional[str] = ..., components: _Optional[_Iterable[_Union[MenuComponent, _Mapping]]] = ..., snapshot_id: _Optional[str] = ...) -> None: ...

class MenuComponent(_message.Message):
    __slots__ = ("component_id", "source", "label", "visible", "enabled", "item_name", "item_stack", "price")
    COMPONENT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    VISIBLE_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    ITEM_NAME_FIELD_NUMBER: _ClassVar[int]
    ITEM_STACK_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    component_id: int
    source: _common_pb2.MenuComponentSource
    label: str
    visible: bool
    enabled: bool
    item_name: str
    item_stack: int
    price: int
    def __init__(self, component_id: _Optional[int] = ..., source: _Optional[_Union[_common_pb2.MenuComponentSource, str]] = ..., label: _Optional[str] = ..., visible: bool = ..., enabled: bool = ..., item_name: _Optional[str] = ..., item_stack: _Optional[int] = ..., price: _Optional[int] = ...) -> None: ...

class QueryInventoryResult(_message.Message):
    __slots__ = ("items", "slots_used", "slots_total")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    SLOTS_USED_FIELD_NUMBER: _ClassVar[int]
    SLOTS_TOTAL_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[_common_pb2.ItemStack]
    slots_used: int
    slots_total: int
    def __init__(self, items: _Optional[_Iterable[_Union[_common_pb2.ItemStack, _Mapping]]] = ..., slots_used: _Optional[int] = ..., slots_total: _Optional[int] = ...) -> None: ...

class QueryLocationResult(_message.Message):
    __slots__ = ("location",)
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    location: _common_pb2.LocationSnapshot
    def __init__(self, location: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ...) -> None: ...

class QueryMapExitsResult(_message.Message):
    __slots__ = ("exits",)
    EXITS_FIELD_NUMBER: _ClassVar[int]
    exits: _containers.RepeatedCompositeFieldContainer[MapExit]
    def __init__(self, exits: _Optional[_Iterable[_Union[MapExit, _Mapping]]] = ...) -> None: ...

class MapExit(_message.Message):
    __slots__ = ("target_location", "display_name", "direction", "exit_x", "exit_y")
    TARGET_LOCATION_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    EXIT_X_FIELD_NUMBER: _ClassVar[int]
    EXIT_Y_FIELD_NUMBER: _ClassVar[int]
    target_location: str
    display_name: str
    direction: _common_pb2.Direction
    exit_x: int
    exit_y: int
    def __init__(self, target_location: _Optional[str] = ..., display_name: _Optional[str] = ..., direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., exit_x: _Optional[int] = ..., exit_y: _Optional[int] = ...) -> None: ...

class TpResult(_message.Message):
    __slots__ = ("reason", "target", "final", "face_direction", "authorized", "used_by", "timing")
    REASON_FIELD_NUMBER: _ClassVar[int]
    FROM_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    FACE_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZED_FIELD_NUMBER: _ClassVar[int]
    USED_BY_FIELD_NUMBER: _ClassVar[int]
    TIMING_FIELD_NUMBER: _ClassVar[int]
    reason: _capability_params_pb2.TeleportReason
    target: _common_pb2.TilePosition
    final: _common_pb2.LocationSnapshot
    face_direction: _common_pb2.Direction
    authorized: bool
    used_by: str
    timing: _common_pb2.ExecutionTiming
    def __init__(self, reason: _Optional[_Union[_capability_params_pb2.TeleportReason, str]] = ..., target: _Optional[_Union[_common_pb2.TilePosition, _Mapping]] = ..., final: _Optional[_Union[_common_pb2.LocationSnapshot, _Mapping]] = ..., face_direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., authorized: bool = ..., used_by: _Optional[str] = ..., timing: _Optional[_Union[_common_pb2.ExecutionTiming, _Mapping]] = ..., **kwargs) -> None: ...
