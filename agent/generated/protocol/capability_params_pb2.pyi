from . import common_pb2 as _common_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EmoteType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EMOTE_TYPE_UNSPECIFIED: _ClassVar[EmoteType]
    EMOTE_HAPPY: _ClassVar[EmoteType]
    EMOTE_SAD: _ClassVar[EmoteType]
    EMOTE_ANGRY: _ClassVar[EmoteType]
    EMOTE_HEART: _ClassVar[EmoteType]
    EMOTE_EXCLAMATION: _ClassVar[EmoteType]
    EMOTE_QUESTION: _ClassVar[EmoteType]
    EMOTE_LAUGH: _ClassVar[EmoteType]
    EMOTE_NOTE: _ClassVar[EmoteType]
    EMOTE_SLEEP: _ClassVar[EmoteType]
    EMOTE_GAME: _ClassVar[EmoteType]
    EMOTE_X: _ClassVar[EmoteType]
    EMOTE_PAUSE: _ClassVar[EmoteType]
    EMOTE_BLUSH: _ClassVar[EmoteType]
    EMOTE_YES: _ClassVar[EmoteType]
    EMOTE_NO: _ClassVar[EmoteType]
    EMOTE_SICK: _ClassVar[EmoteType]
    EMOTE_SURPRISED: _ClassVar[EmoteType]
    EMOTE_HI: _ClassVar[EmoteType]
    EMOTE_TAUNT: _ClassVar[EmoteType]
    EMOTE_UH: _ClassVar[EmoteType]
    EMOTE_MUSIC: _ClassVar[EmoteType]
    EMOTE_JAR: _ClassVar[EmoteType]

class MoveToMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MOVE_TO_MODE_UNSPECIFIED: _ClassVar[MoveToMode]
    MOVE_TO_EXACT: _ClassVar[MoveToMode]
    MOVE_TO_EDGE_WARP: _ClassVar[MoveToMode]
    MOVE_TO_ADJACENT: _ClassVar[MoveToMode]

class ArrivalMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ARRIVAL_MODE_UNSPECIFIED: _ClassVar[ArrivalMode]
    ARRIVAL_EXACT: _ClassVar[ArrivalMode]
    ARRIVAL_ADJACENT: _ClassVar[ArrivalMode]

class TeleportReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TELEPORT_REASON_UNSPECIFIED: _ClassVar[TeleportReason]
    TELEPORT_RECOVERY: _ClassVar[TeleportReason]
    TELEPORT_TEST: _ClassVar[TeleportReason]
    TELEPORT_GO_TO_BED: _ClassVar[TeleportReason]
EMOTE_TYPE_UNSPECIFIED: EmoteType
EMOTE_HAPPY: EmoteType
EMOTE_SAD: EmoteType
EMOTE_ANGRY: EmoteType
EMOTE_HEART: EmoteType
EMOTE_EXCLAMATION: EmoteType
EMOTE_QUESTION: EmoteType
EMOTE_LAUGH: EmoteType
EMOTE_NOTE: EmoteType
EMOTE_SLEEP: EmoteType
EMOTE_GAME: EmoteType
EMOTE_X: EmoteType
EMOTE_PAUSE: EmoteType
EMOTE_BLUSH: EmoteType
EMOTE_YES: EmoteType
EMOTE_NO: EmoteType
EMOTE_SICK: EmoteType
EMOTE_SURPRISED: EmoteType
EMOTE_HI: EmoteType
EMOTE_TAUNT: EmoteType
EMOTE_UH: EmoteType
EMOTE_MUSIC: EmoteType
EMOTE_JAR: EmoteType
MOVE_TO_MODE_UNSPECIFIED: MoveToMode
MOVE_TO_EXACT: MoveToMode
MOVE_TO_EDGE_WARP: MoveToMode
MOVE_TO_ADJACENT: MoveToMode
ARRIVAL_MODE_UNSPECIFIED: ArrivalMode
ARRIVAL_EXACT: ArrivalMode
ARRIVAL_ADJACENT: ArrivalMode
TELEPORT_REASON_UNSPECIFIED: TeleportReason
TELEPORT_RECOVERY: TeleportReason
TELEPORT_TEST: TeleportReason
TELEPORT_GO_TO_BED: TeleportReason

class SayParams(_message.Message):
    __slots__ = ("content",)
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    content: str
    def __init__(self, content: _Optional[str] = ...) -> None: ...

class EmoteParams(_message.Message):
    __slots__ = ("emote_type",)
    EMOTE_TYPE_FIELD_NUMBER: _ClassVar[int]
    emote_type: EmoteType
    def __init__(self, emote_type: _Optional[_Union[EmoteType, str]] = ...) -> None: ...

class FaceParams(_message.Message):
    __slots__ = ("direction",)
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    direction: _common_pb2.Direction
    def __init__(self, direction: _Optional[_Union[_common_pb2.Direction, str]] = ...) -> None: ...

class MoveToParams(_message.Message):
    __slots__ = ("mode", "x", "y", "direction", "face_direction", "adjacent_side", "timeout_frames")
    MODE_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    FACE_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    ADJACENT_SIDE_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_FRAMES_FIELD_NUMBER: _ClassVar[int]
    mode: MoveToMode
    x: int
    y: int
    direction: _common_pb2.Direction
    face_direction: _common_pb2.Direction
    adjacent_side: _common_pb2.Direction
    timeout_frames: int
    def __init__(self, mode: _Optional[_Union[MoveToMode, str]] = ..., x: _Optional[int] = ..., y: _Optional[int] = ..., direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., face_direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., adjacent_side: _Optional[_Union[_common_pb2.Direction, str]] = ..., timeout_frames: _Optional[int] = ...) -> None: ...

class GoToParams(_message.Message):
    __slots__ = ("map_id", "x", "y", "arrival_mode", "stand_side", "face_direction", "timeout_frames")
    MAP_ID_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    ARRIVAL_MODE_FIELD_NUMBER: _ClassVar[int]
    STAND_SIDE_FIELD_NUMBER: _ClassVar[int]
    FACE_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_FRAMES_FIELD_NUMBER: _ClassVar[int]
    map_id: str
    x: int
    y: int
    arrival_mode: ArrivalMode
    stand_side: _common_pb2.Direction
    face_direction: _common_pb2.Direction
    timeout_frames: int
    def __init__(self, map_id: _Optional[str] = ..., x: _Optional[int] = ..., y: _Optional[int] = ..., arrival_mode: _Optional[_Union[ArrivalMode, str]] = ..., stand_side: _Optional[_Union[_common_pb2.Direction, str]] = ..., face_direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., timeout_frames: _Optional[int] = ...) -> None: ...

class GoToBedParams(_message.Message):
    __slots__ = ("timeout_frames",)
    TIMEOUT_FRAMES_FIELD_NUMBER: _ClassVar[int]
    timeout_frames: int
    def __init__(self, timeout_frames: _Optional[int] = ...) -> None: ...

class InteractParams(_message.Message):
    __slots__ = ("target_tile",)
    TARGET_TILE_FIELD_NUMBER: _ClassVar[int]
    target_tile: _common_pb2.TilePoint
    def __init__(self, target_tile: _Optional[_Union[_common_pb2.TilePoint, _Mapping]] = ...) -> None: ...

class UseToolParams(_message.Message):
    __slots__ = ("target_tile", "charge_level")
    TARGET_TILE_FIELD_NUMBER: _ClassVar[int]
    CHARGE_LEVEL_FIELD_NUMBER: _ClassVar[int]
    target_tile: _common_pb2.TilePoint
    charge_level: int
    def __init__(self, target_tile: _Optional[_Union[_common_pb2.TilePoint, _Mapping]] = ..., charge_level: _Optional[int] = ...) -> None: ...

class EquipParams(_message.Message):
    __slots__ = ("slot", "item_name", "item_category")
    SLOT_FIELD_NUMBER: _ClassVar[int]
    ITEM_NAME_FIELD_NUMBER: _ClassVar[int]
    ITEM_CATEGORY_FIELD_NUMBER: _ClassVar[int]
    slot: int
    item_name: str
    item_category: _common_pb2.ItemCategory
    def __init__(self, slot: _Optional[int] = ..., item_name: _Optional[str] = ..., item_category: _Optional[_Union[_common_pb2.ItemCategory, str]] = ...) -> None: ...

class OpenMenuParams(_message.Message):
    __slots__ = ("menu",)
    MENU_FIELD_NUMBER: _ClassVar[int]
    menu: str
    def __init__(self, menu: _Optional[str] = ...) -> None: ...

class QueryMenuParams(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class MenuClickParams(_message.Message):
    __slots__ = ("component_id", "source", "click_count")
    COMPONENT_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CLICK_COUNT_FIELD_NUMBER: _ClassVar[int]
    component_id: int
    source: _common_pb2.MenuComponentSource
    click_count: int
    def __init__(self, component_id: _Optional[int] = ..., source: _Optional[_Union[_common_pb2.MenuComponentSource, str]] = ..., click_count: _Optional[int] = ...) -> None: ...

class MenuCloseParams(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class QueryInventoryParams(_message.Message):
    __slots__ = ("include_details",)
    INCLUDE_DETAILS_FIELD_NUMBER: _ClassVar[int]
    include_details: bool
    def __init__(self, include_details: bool = ...) -> None: ...

class QueryLocationParams(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class QueryMapExitsParams(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class TpParams(_message.Message):
    __slots__ = ("target", "face_direction", "reason")
    TARGET_FIELD_NUMBER: _ClassVar[int]
    FACE_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    target: _common_pb2.TilePosition
    face_direction: _common_pb2.Direction
    reason: TeleportReason
    def __init__(self, target: _Optional[_Union[_common_pb2.TilePosition, _Mapping]] = ..., face_direction: _Optional[_Union[_common_pb2.Direction, str]] = ..., reason: _Optional[_Union[TeleportReason, str]] = ...) -> None: ...
