from . import common_pb2 as _common_pb2
from . import capability_pb2 as _capability_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Envelope(_message.Message):
    __slots__ = ("protocol_version", "message_id", "request_id", "session_id", "timestamp_ms", "hello", "hello_ack", "command_request", "command_accepted", "command_progress", "command_result", "command_cancel", "command_status_request", "command_status_response", "error_response")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    HELLO_FIELD_NUMBER: _ClassVar[int]
    HELLO_ACK_FIELD_NUMBER: _ClassVar[int]
    COMMAND_REQUEST_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    COMMAND_PROGRESS_FIELD_NUMBER: _ClassVar[int]
    COMMAND_RESULT_FIELD_NUMBER: _ClassVar[int]
    COMMAND_CANCEL_FIELD_NUMBER: _ClassVar[int]
    COMMAND_STATUS_REQUEST_FIELD_NUMBER: _ClassVar[int]
    COMMAND_STATUS_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    ERROR_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    protocol_version: str
    message_id: str
    request_id: str
    session_id: str
    timestamp_ms: int
    hello: Hello
    hello_ack: HelloAck
    command_request: _capability_pb2.CommandRequest
    command_accepted: _capability_pb2.CommandAccepted
    command_progress: _capability_pb2.CommandProgress
    command_result: _capability_pb2.CommandResult
    command_cancel: _capability_pb2.CommandCancel
    command_status_request: _capability_pb2.CommandStatusRequest
    command_status_response: _capability_pb2.CommandStatusResponse
    error_response: ErrorResponse
    def __init__(self, protocol_version: _Optional[str] = ..., message_id: _Optional[str] = ..., request_id: _Optional[str] = ..., session_id: _Optional[str] = ..., timestamp_ms: _Optional[int] = ..., hello: _Optional[_Union[Hello, _Mapping]] = ..., hello_ack: _Optional[_Union[HelloAck, _Mapping]] = ..., command_request: _Optional[_Union[_capability_pb2.CommandRequest, _Mapping]] = ..., command_accepted: _Optional[_Union[_capability_pb2.CommandAccepted, _Mapping]] = ..., command_progress: _Optional[_Union[_capability_pb2.CommandProgress, _Mapping]] = ..., command_result: _Optional[_Union[_capability_pb2.CommandResult, _Mapping]] = ..., command_cancel: _Optional[_Union[_capability_pb2.CommandCancel, _Mapping]] = ..., command_status_request: _Optional[_Union[_capability_pb2.CommandStatusRequest, _Mapping]] = ..., command_status_response: _Optional[_Union[_capability_pb2.CommandStatusResponse, _Mapping]] = ..., error_response: _Optional[_Union[ErrorResponse, _Mapping]] = ...) -> None: ...

class Hello(_message.Message):
    __slots__ = ("agent_id", "required_protocol_version")
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    required_protocol_version: str
    def __init__(self, agent_id: _Optional[str] = ..., required_protocol_version: _Optional[str] = ...) -> None: ...

class HelloAck(_message.Message):
    __slots__ = ("accepted", "protocol_version", "mod_runtime_id", "session_id", "enabled_capabilities", "error")
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    MOD_RUNTIME_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    ENABLED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    accepted: bool
    protocol_version: str
    mod_runtime_id: str
    session_id: str
    enabled_capabilities: _containers.RepeatedScalarFieldContainer[str]
    error: _common_pb2.Error
    def __init__(self, accepted: bool = ..., protocol_version: _Optional[str] = ..., mod_runtime_id: _Optional[str] = ..., session_id: _Optional[str] = ..., enabled_capabilities: _Optional[_Iterable[str]] = ..., error: _Optional[_Union[_common_pb2.Error, _Mapping]] = ...) -> None: ...

class ErrorResponse(_message.Message):
    __slots__ = ("error",)
    ERROR_FIELD_NUMBER: _ClassVar[int]
    error: _common_pb2.Error
    def __init__(self, error: _Optional[_Union[_common_pb2.Error, _Mapping]] = ...) -> None: ...
