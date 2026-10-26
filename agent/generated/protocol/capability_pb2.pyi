from . import common_pb2 as _common_pb2
from . import capability_params_pb2 as _capability_params_pb2
from . import capability_results_pb2 as _capability_results_pb2
from . import query_pb2 as _query_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CommandSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    COMMAND_SOURCE_UNSPECIFIED: _ClassVar[CommandSource]
    COMMAND_SOURCE_LLM: _ClassVar[CommandSource]
    COMMAND_SOURCE_AGENT_RUNTIME: _ClassVar[CommandSource]
    COMMAND_SOURCE_USER: _ClassVar[CommandSource]
    COMMAND_SOURCE_DIAGNOSTIC: _ClassVar[CommandSource]

class CommandStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    COMMAND_STATUS_UNSPECIFIED: _ClassVar[CommandStatus]
    COMMAND_STATUS_ACCEPTED: _ClassVar[CommandStatus]
    COMMAND_STATUS_RUNNING: _ClassVar[CommandStatus]
    COMMAND_STATUS_COMPLETED: _ClassVar[CommandStatus]
    COMMAND_STATUS_REJECTED: _ClassVar[CommandStatus]
    COMMAND_STATUS_FAILED: _ClassVar[CommandStatus]
    COMMAND_STATUS_TIMEOUT: _ClassVar[CommandStatus]
    COMMAND_STATUS_CANCELLED: _ClassVar[CommandStatus]
COMMAND_SOURCE_UNSPECIFIED: CommandSource
COMMAND_SOURCE_LLM: CommandSource
COMMAND_SOURCE_AGENT_RUNTIME: CommandSource
COMMAND_SOURCE_USER: CommandSource
COMMAND_SOURCE_DIAGNOSTIC: CommandSource
COMMAND_STATUS_UNSPECIFIED: CommandStatus
COMMAND_STATUS_ACCEPTED: CommandStatus
COMMAND_STATUS_RUNNING: CommandStatus
COMMAND_STATUS_COMPLETED: CommandStatus
COMMAND_STATUS_REJECTED: CommandStatus
COMMAND_STATUS_FAILED: CommandStatus
COMMAND_STATUS_TIMEOUT: CommandStatus
COMMAND_STATUS_CANCELLED: CommandStatus

class CommandRequest(_message.Message):
    __slots__ = ("command_id", "timeout_ms", "source", "capability", "say", "emote", "face", "move_to", "go_to", "go_to_bed", "interact", "use_tool", "equip", "open_menu", "query_menu", "menu_click", "menu_close", "query_inventory", "query_location", "query_map_exits", "query_runtime_snapshot", "query_world_region", "query_inventory_snapshot", "query_ui_snapshot", "query_inspect_refs", "tp")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_FIELD_NUMBER: _ClassVar[int]
    SAY_FIELD_NUMBER: _ClassVar[int]
    EMOTE_FIELD_NUMBER: _ClassVar[int]
    FACE_FIELD_NUMBER: _ClassVar[int]
    MOVE_TO_FIELD_NUMBER: _ClassVar[int]
    GO_TO_FIELD_NUMBER: _ClassVar[int]
    GO_TO_BED_FIELD_NUMBER: _ClassVar[int]
    INTERACT_FIELD_NUMBER: _ClassVar[int]
    USE_TOOL_FIELD_NUMBER: _ClassVar[int]
    EQUIP_FIELD_NUMBER: _ClassVar[int]
    OPEN_MENU_FIELD_NUMBER: _ClassVar[int]
    QUERY_MENU_FIELD_NUMBER: _ClassVar[int]
    MENU_CLICK_FIELD_NUMBER: _ClassVar[int]
    MENU_CLOSE_FIELD_NUMBER: _ClassVar[int]
    QUERY_INVENTORY_FIELD_NUMBER: _ClassVar[int]
    QUERY_LOCATION_FIELD_NUMBER: _ClassVar[int]
    QUERY_MAP_EXITS_FIELD_NUMBER: _ClassVar[int]
    QUERY_RUNTIME_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    QUERY_WORLD_REGION_FIELD_NUMBER: _ClassVar[int]
    QUERY_INVENTORY_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    QUERY_UI_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    QUERY_INSPECT_REFS_FIELD_NUMBER: _ClassVar[int]
    TP_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    timeout_ms: int
    source: CommandSource
    capability: str
    say: _capability_params_pb2.SayParams
    emote: _capability_params_pb2.EmoteParams
    face: _capability_params_pb2.FaceParams
    move_to: _capability_params_pb2.MoveToParams
    go_to: _capability_params_pb2.GoToParams
    go_to_bed: _capability_params_pb2.GoToBedParams
    interact: _capability_params_pb2.InteractParams
    use_tool: _capability_params_pb2.UseToolParams
    equip: _capability_params_pb2.EquipParams
    open_menu: _capability_params_pb2.OpenMenuParams
    query_menu: _capability_params_pb2.QueryMenuParams
    menu_click: _capability_params_pb2.MenuClickParams
    menu_close: _capability_params_pb2.MenuCloseParams
    query_inventory: _capability_params_pb2.QueryInventoryParams
    query_location: _capability_params_pb2.QueryLocationParams
    query_map_exits: _capability_params_pb2.QueryMapExitsParams
    query_runtime_snapshot: _query_pb2.QueryRuntimeSnapshotParams
    query_world_region: _query_pb2.QueryWorldRegionParams
    query_inventory_snapshot: _query_pb2.QueryInventorySnapshotParams
    query_ui_snapshot: _query_pb2.QueryUiSnapshotParams
    query_inspect_refs: _query_pb2.QueryInspectRefsParams
    tp: _capability_params_pb2.TpParams
    def __init__(self, command_id: _Optional[str] = ..., timeout_ms: _Optional[int] = ..., source: _Optional[_Union[CommandSource, str]] = ..., capability: _Optional[str] = ..., say: _Optional[_Union[_capability_params_pb2.SayParams, _Mapping]] = ..., emote: _Optional[_Union[_capability_params_pb2.EmoteParams, _Mapping]] = ..., face: _Optional[_Union[_capability_params_pb2.FaceParams, _Mapping]] = ..., move_to: _Optional[_Union[_capability_params_pb2.MoveToParams, _Mapping]] = ..., go_to: _Optional[_Union[_capability_params_pb2.GoToParams, _Mapping]] = ..., go_to_bed: _Optional[_Union[_capability_params_pb2.GoToBedParams, _Mapping]] = ..., interact: _Optional[_Union[_capability_params_pb2.InteractParams, _Mapping]] = ..., use_tool: _Optional[_Union[_capability_params_pb2.UseToolParams, _Mapping]] = ..., equip: _Optional[_Union[_capability_params_pb2.EquipParams, _Mapping]] = ..., open_menu: _Optional[_Union[_capability_params_pb2.OpenMenuParams, _Mapping]] = ..., query_menu: _Optional[_Union[_capability_params_pb2.QueryMenuParams, _Mapping]] = ..., menu_click: _Optional[_Union[_capability_params_pb2.MenuClickParams, _Mapping]] = ..., menu_close: _Optional[_Union[_capability_params_pb2.MenuCloseParams, _Mapping]] = ..., query_inventory: _Optional[_Union[_capability_params_pb2.QueryInventoryParams, _Mapping]] = ..., query_location: _Optional[_Union[_capability_params_pb2.QueryLocationParams, _Mapping]] = ..., query_map_exits: _Optional[_Union[_capability_params_pb2.QueryMapExitsParams, _Mapping]] = ..., query_runtime_snapshot: _Optional[_Union[_query_pb2.QueryRuntimeSnapshotParams, _Mapping]] = ..., query_world_region: _Optional[_Union[_query_pb2.QueryWorldRegionParams, _Mapping]] = ..., query_inventory_snapshot: _Optional[_Union[_query_pb2.QueryInventorySnapshotParams, _Mapping]] = ..., query_ui_snapshot: _Optional[_Union[_query_pb2.QueryUiSnapshotParams, _Mapping]] = ..., query_inspect_refs: _Optional[_Union[_query_pb2.QueryInspectRefsParams, _Mapping]] = ..., tp: _Optional[_Union[_capability_params_pb2.TpParams, _Mapping]] = ...) -> None: ...

class CommandAccepted(_message.Message):
    __slots__ = ("command_id", "accepted_at_ms", "timeout_ms", "cancellable")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    CANCELLABLE_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    accepted_at_ms: int
    timeout_ms: int
    cancellable: bool
    def __init__(self, command_id: _Optional[str] = ..., accepted_at_ms: _Optional[int] = ..., timeout_ms: _Optional[int] = ..., cancellable: bool = ...) -> None: ...

class CommandProgress(_message.Message):
    __slots__ = ("command_id", "status", "observation", "message")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OBSERVATION_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    status: CommandStatus
    observation: _common_pb2.Observation
    message: str
    def __init__(self, command_id: _Optional[str] = ..., status: _Optional[_Union[CommandStatus, str]] = ..., observation: _Optional[_Union[_common_pb2.Observation, _Mapping]] = ..., message: _Optional[str] = ...) -> None: ...

class CommandResult(_message.Message):
    __slots__ = ("command_id", "success", "status", "observation", "error", "capability", "say", "emote", "face", "move_to", "go_to", "go_to_bed", "interact", "use_tool", "equip", "menu", "menu_snapshot", "query_inventory", "query_location", "query_map_exits", "query_runtime_snapshot", "query_world_region", "query_inventory_snapshot", "query_ui_snapshot", "query_inspect_refs", "tp")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OBSERVATION_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_FIELD_NUMBER: _ClassVar[int]
    SAY_FIELD_NUMBER: _ClassVar[int]
    EMOTE_FIELD_NUMBER: _ClassVar[int]
    FACE_FIELD_NUMBER: _ClassVar[int]
    MOVE_TO_FIELD_NUMBER: _ClassVar[int]
    GO_TO_FIELD_NUMBER: _ClassVar[int]
    GO_TO_BED_FIELD_NUMBER: _ClassVar[int]
    INTERACT_FIELD_NUMBER: _ClassVar[int]
    USE_TOOL_FIELD_NUMBER: _ClassVar[int]
    EQUIP_FIELD_NUMBER: _ClassVar[int]
    MENU_FIELD_NUMBER: _ClassVar[int]
    MENU_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    QUERY_INVENTORY_FIELD_NUMBER: _ClassVar[int]
    QUERY_LOCATION_FIELD_NUMBER: _ClassVar[int]
    QUERY_MAP_EXITS_FIELD_NUMBER: _ClassVar[int]
    QUERY_RUNTIME_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    QUERY_WORLD_REGION_FIELD_NUMBER: _ClassVar[int]
    QUERY_INVENTORY_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    QUERY_UI_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    QUERY_INSPECT_REFS_FIELD_NUMBER: _ClassVar[int]
    TP_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    success: bool
    status: CommandStatus
    observation: _common_pb2.Observation
    error: _common_pb2.Error
    capability: str
    say: _capability_results_pb2.SayResult
    emote: _capability_results_pb2.EmoteResult
    face: _capability_results_pb2.FaceResult
    move_to: _capability_results_pb2.MoveToResult
    go_to: _capability_results_pb2.GoToResult
    go_to_bed: _capability_results_pb2.GoToBedResult
    interact: _capability_results_pb2.InteractResult
    use_tool: _capability_results_pb2.UseToolResult
    equip: _capability_results_pb2.EquipResult
    menu: _capability_results_pb2.MenuResult
    menu_snapshot: _capability_results_pb2.MenuSnapshot
    query_inventory: _capability_results_pb2.QueryInventoryResult
    query_location: _capability_results_pb2.QueryLocationResult
    query_map_exits: _capability_results_pb2.QueryMapExitsResult
    query_runtime_snapshot: _query_pb2.QueryRuntimeSnapshotResult
    query_world_region: _query_pb2.QueryWorldRegionResult
    query_inventory_snapshot: _query_pb2.QueryInventorySnapshotResult
    query_ui_snapshot: _query_pb2.QueryUiSnapshotResult
    query_inspect_refs: _query_pb2.QueryInspectRefsResult
    tp: _capability_results_pb2.TpResult
    def __init__(self, command_id: _Optional[str] = ..., success: bool = ..., status: _Optional[_Union[CommandStatus, str]] = ..., observation: _Optional[_Union[_common_pb2.Observation, _Mapping]] = ..., error: _Optional[_Union[_common_pb2.Error, _Mapping]] = ..., capability: _Optional[str] = ..., say: _Optional[_Union[_capability_results_pb2.SayResult, _Mapping]] = ..., emote: _Optional[_Union[_capability_results_pb2.EmoteResult, _Mapping]] = ..., face: _Optional[_Union[_capability_results_pb2.FaceResult, _Mapping]] = ..., move_to: _Optional[_Union[_capability_results_pb2.MoveToResult, _Mapping]] = ..., go_to: _Optional[_Union[_capability_results_pb2.GoToResult, _Mapping]] = ..., go_to_bed: _Optional[_Union[_capability_results_pb2.GoToBedResult, _Mapping]] = ..., interact: _Optional[_Union[_capability_results_pb2.InteractResult, _Mapping]] = ..., use_tool: _Optional[_Union[_capability_results_pb2.UseToolResult, _Mapping]] = ..., equip: _Optional[_Union[_capability_results_pb2.EquipResult, _Mapping]] = ..., menu: _Optional[_Union[_capability_results_pb2.MenuResult, _Mapping]] = ..., menu_snapshot: _Optional[_Union[_capability_results_pb2.MenuSnapshot, _Mapping]] = ..., query_inventory: _Optional[_Union[_capability_results_pb2.QueryInventoryResult, _Mapping]] = ..., query_location: _Optional[_Union[_capability_results_pb2.QueryLocationResult, _Mapping]] = ..., query_map_exits: _Optional[_Union[_capability_results_pb2.QueryMapExitsResult, _Mapping]] = ..., query_runtime_snapshot: _Optional[_Union[_query_pb2.QueryRuntimeSnapshotResult, _Mapping]] = ..., query_world_region: _Optional[_Union[_query_pb2.QueryWorldRegionResult, _Mapping]] = ..., query_inventory_snapshot: _Optional[_Union[_query_pb2.QueryInventorySnapshotResult, _Mapping]] = ..., query_ui_snapshot: _Optional[_Union[_query_pb2.QueryUiSnapshotResult, _Mapping]] = ..., query_inspect_refs: _Optional[_Union[_query_pb2.QueryInspectRefsResult, _Mapping]] = ..., tp: _Optional[_Union[_capability_results_pb2.TpResult, _Mapping]] = ...) -> None: ...

class CommandCancel(_message.Message):
    __slots__ = ("command_id", "reason")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    reason: str
    def __init__(self, command_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class CommandStatusRequest(_message.Message):
    __slots__ = ("command_id",)
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    def __init__(self, command_id: _Optional[str] = ...) -> None: ...

class CommandStatusResponse(_message.Message):
    __slots__ = ("command_id", "status", "terminal", "observation", "error")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_FIELD_NUMBER: _ClassVar[int]
    OBSERVATION_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    status: CommandStatus
    terminal: bool
    observation: _common_pb2.Observation
    error: _common_pb2.Error
    def __init__(self, command_id: _Optional[str] = ..., status: _Optional[_Union[CommandStatus, str]] = ..., terminal: bool = ..., observation: _Optional[_Union[_common_pb2.Observation, _Mapping]] = ..., error: _Optional[_Union[_common_pb2.Error, _Mapping]] = ...) -> None: ...
