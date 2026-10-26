from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Direction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DIRECTION_UNSPECIFIED: _ClassVar[Direction]
    DIRECTION_UP: _ClassVar[Direction]
    DIRECTION_DOWN: _ClassVar[Direction]
    DIRECTION_LEFT: _ClassVar[Direction]
    DIRECTION_RIGHT: _ClassVar[Direction]

class ErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ERROR_CODE_UNSPECIFIED: _ClassVar[ErrorCode]
    CAPABILITY_MISSING: _ClassVar[ErrorCode]
    INVALID_PARAMS: _ClassVar[ErrorCode]
    BUSY: _ClassVar[ErrorCode]
    TIMEOUT: _ClassVar[ErrorCode]
    CANCELLED: _ClassVar[ErrorCode]
    EXECUTION_FAILED: _ClassVar[ErrorCode]

class ItemCategory(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ITEM_CATEGORY_UNSPECIFIED: _ClassVar[ItemCategory]
    ITEM_CATEGORY_AXE: _ClassVar[ItemCategory]
    ITEM_CATEGORY_PICKAXE: _ClassVar[ItemCategory]
    ITEM_CATEGORY_HOE: _ClassVar[ItemCategory]
    ITEM_CATEGORY_WATERING_CAN: _ClassVar[ItemCategory]
    ITEM_CATEGORY_SCYTHE: _ClassVar[ItemCategory]
    ITEM_CATEGORY_FISHING_ROD: _ClassVar[ItemCategory]
    ITEM_CATEGORY_WEAPON: _ClassVar[ItemCategory]

class MenuComponentSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MENU_COMPONENT_SOURCE_UNSPECIFIED: _ClassVar[MenuComponentSource]
    MENU_COMPONENT_SOURCE_PAGE: _ClassVar[MenuComponentSource]
    MENU_COMPONENT_SOURCE_TAB: _ClassVar[MenuComponentSource]
DIRECTION_UNSPECIFIED: Direction
DIRECTION_UP: Direction
DIRECTION_DOWN: Direction
DIRECTION_LEFT: Direction
DIRECTION_RIGHT: Direction
ERROR_CODE_UNSPECIFIED: ErrorCode
CAPABILITY_MISSING: ErrorCode
INVALID_PARAMS: ErrorCode
BUSY: ErrorCode
TIMEOUT: ErrorCode
CANCELLED: ErrorCode
EXECUTION_FAILED: ErrorCode
ITEM_CATEGORY_UNSPECIFIED: ItemCategory
ITEM_CATEGORY_AXE: ItemCategory
ITEM_CATEGORY_PICKAXE: ItemCategory
ITEM_CATEGORY_HOE: ItemCategory
ITEM_CATEGORY_WATERING_CAN: ItemCategory
ITEM_CATEGORY_SCYTHE: ItemCategory
ITEM_CATEGORY_FISHING_ROD: ItemCategory
ITEM_CATEGORY_WEAPON: ItemCategory
MENU_COMPONENT_SOURCE_UNSPECIFIED: MenuComponentSource
MENU_COMPONENT_SOURCE_PAGE: MenuComponentSource
MENU_COMPONENT_SOURCE_TAB: MenuComponentSource

class Error(_message.Message):
    __slots__ = ("code", "message", "detail")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    code: ErrorCode
    message: str
    detail: str
    def __init__(self, code: _Optional[_Union[ErrorCode, str]] = ..., message: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class StringPair(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: str
    def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class Observation(_message.Message):
    __slots__ = ("text",)
    TEXT_FIELD_NUMBER: _ClassVar[int]
    text: str
    def __init__(self, text: _Optional[str] = ...) -> None: ...

class LocationSnapshot(_message.Message):
    __slots__ = ("map_token", "x", "y", "facing")
    MAP_TOKEN_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    FACING_FIELD_NUMBER: _ClassVar[int]
    map_token: str
    x: int
    y: int
    facing: Direction
    def __init__(self, map_token: _Optional[str] = ..., x: _Optional[int] = ..., y: _Optional[int] = ..., facing: _Optional[_Union[Direction, str]] = ...) -> None: ...

class TilePosition(_message.Message):
    __slots__ = ("map_id", "x", "y")
    MAP_ID_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    map_id: str
    x: int
    y: int
    def __init__(self, map_id: _Optional[str] = ..., x: _Optional[int] = ..., y: _Optional[int] = ...) -> None: ...

class TileRect(_message.Message):
    __slots__ = ("map_id", "x", "y", "width", "height")
    MAP_ID_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    map_id: str
    x: int
    y: int
    width: int
    height: int
    def __init__(self, map_id: _Optional[str] = ..., x: _Optional[int] = ..., y: _Optional[int] = ..., width: _Optional[int] = ..., height: _Optional[int] = ...) -> None: ...

class PixelPosition(_message.Message):
    __slots__ = ("x", "y")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    x: int
    y: int
    def __init__(self, x: _Optional[int] = ..., y: _Optional[int] = ...) -> None: ...

class TilePoint(_message.Message):
    __slots__ = ("x", "y")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    x: int
    y: int
    def __init__(self, x: _Optional[int] = ..., y: _Optional[int] = ...) -> None: ...

class ResourceDelta(_message.Message):
    __slots__ = ("before", "after", "delta")
    BEFORE_FIELD_NUMBER: _ClassVar[int]
    AFTER_FIELD_NUMBER: _ClassVar[int]
    DELTA_FIELD_NUMBER: _ClassVar[int]
    before: int
    after: int
    delta: int
    def __init__(self, before: _Optional[int] = ..., after: _Optional[int] = ..., delta: _Optional[int] = ...) -> None: ...

class ExecutionTiming(_message.Message):
    __slots__ = ("elapsed_frames", "waited_ticks", "timed_out", "completion_reason")
    ELAPSED_FRAMES_FIELD_NUMBER: _ClassVar[int]
    WAITED_TICKS_FIELD_NUMBER: _ClassVar[int]
    TIMED_OUT_FIELD_NUMBER: _ClassVar[int]
    COMPLETION_REASON_FIELD_NUMBER: _ClassVar[int]
    elapsed_frames: int
    waited_ticks: int
    timed_out: bool
    completion_reason: str
    def __init__(self, elapsed_frames: _Optional[int] = ..., waited_ticks: _Optional[int] = ..., timed_out: bool = ..., completion_reason: _Optional[str] = ...) -> None: ...

class TileEffectResult(_message.Message):
    __slots__ = ("target_tile", "stamina", "timing")
    TARGET_TILE_FIELD_NUMBER: _ClassVar[int]
    STAMINA_FIELD_NUMBER: _ClassVar[int]
    TIMING_FIELD_NUMBER: _ClassVar[int]
    target_tile: TilePoint
    stamina: ResourceDelta
    timing: ExecutionTiming
    def __init__(self, target_tile: _Optional[_Union[TilePoint, _Mapping]] = ..., stamina: _Optional[_Union[ResourceDelta, _Mapping]] = ..., timing: _Optional[_Union[ExecutionTiming, _Mapping]] = ...) -> None: ...

class ItemStack(_message.Message):
    __slots__ = ("item_id", "name", "display_name", "stack", "quality", "slot")
    ITEM_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    STACK_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    SLOT_FIELD_NUMBER: _ClassVar[int]
    item_id: str
    name: str
    display_name: str
    stack: int
    quality: int
    slot: int
    def __init__(self, item_id: _Optional[str] = ..., name: _Optional[str] = ..., display_name: _Optional[str] = ..., stack: _Optional[int] = ..., quality: _Optional[int] = ..., slot: _Optional[int] = ...) -> None: ...

class MenuTransition(_message.Message):
    __slots__ = ("menu_type_before", "menu_type_after", "ui_open_before", "ui_open_after")
    MENU_TYPE_BEFORE_FIELD_NUMBER: _ClassVar[int]
    MENU_TYPE_AFTER_FIELD_NUMBER: _ClassVar[int]
    UI_OPEN_BEFORE_FIELD_NUMBER: _ClassVar[int]
    UI_OPEN_AFTER_FIELD_NUMBER: _ClassVar[int]
    menu_type_before: str
    menu_type_after: str
    ui_open_before: bool
    ui_open_after: bool
    def __init__(self, menu_type_before: _Optional[str] = ..., menu_type_after: _Optional[str] = ..., ui_open_before: bool = ..., ui_open_after: bool = ...) -> None: ...
