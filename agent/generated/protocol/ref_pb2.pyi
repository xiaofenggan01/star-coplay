from . import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RefResolveStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REF_RESOLVE_STATUS_UNSPECIFIED: _ClassVar[RefResolveStatus]
    REF_RESOLVED: _ClassVar[RefResolveStatus]
    REF_RESOLVED_DEGRADED: _ClassVar[RefResolveStatus]
    REF_MISSING: _ClassVar[RefResolveStatus]
    REF_STALE_TYPE_MISMATCH: _ClassVar[RefResolveStatus]
    REF_STALE_GUARD_MISMATCH: _ClassVar[RefResolveStatus]
    REF_STALE_CONTEXT_MISMATCH: _ClassVar[RefResolveStatus]
    REF_AMBIGUOUS: _ClassVar[RefResolveStatus]
    REF_UNSUPPORTED: _ClassVar[RefResolveStatus]
    REF_MALFORMED_REF: _ClassVar[RefResolveStatus]
REF_RESOLVE_STATUS_UNSPECIFIED: RefResolveStatus
REF_RESOLVED: RefResolveStatus
REF_RESOLVED_DEGRADED: RefResolveStatus
REF_MISSING: RefResolveStatus
REF_STALE_TYPE_MISMATCH: RefResolveStatus
REF_STALE_GUARD_MISMATCH: RefResolveStatus
REF_STALE_CONTEXT_MISMATCH: RefResolveStatus
REF_AMBIGUOUS: RefResolveStatus
REF_UNSUPPORTED: RefResolveStatus
REF_MALFORMED_REF: RefResolveStatus

class RefId(_message.Message):
    __slots__ = ("value", "ref_type", "type", "locator_segments", "identity_guard")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    REF_TYPE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    LOCATOR_SEGMENTS_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_GUARD_FIELD_NUMBER: _ClassVar[int]
    value: str
    ref_type: str
    type: str
    locator_segments: _containers.RepeatedScalarFieldContainer[str]
    identity_guard: str
    def __init__(self, value: _Optional[str] = ..., ref_type: _Optional[str] = ..., type: _Optional[str] = ..., locator_segments: _Optional[_Iterable[str]] = ..., identity_guard: _Optional[str] = ...) -> None: ...

class RefResolution(_message.Message):
    __slots__ = ("ref", "status", "ref_type", "type", "fact_kind", "summary", "debug_reason", "error", "debug")
    REF_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REF_TYPE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    FACT_KIND_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    DEBUG_REASON_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    DEBUG_FIELD_NUMBER: _ClassVar[int]
    ref: RefId
    status: RefResolveStatus
    ref_type: str
    type: str
    fact_kind: str
    summary: str
    debug_reason: str
    error: _common_pb2.Error
    debug: _containers.RepeatedCompositeFieldContainer[_common_pb2.StringPair]
    def __init__(self, ref: _Optional[_Union[RefId, _Mapping]] = ..., status: _Optional[_Union[RefResolveStatus, str]] = ..., ref_type: _Optional[str] = ..., type: _Optional[str] = ..., fact_kind: _Optional[str] = ..., summary: _Optional[str] = ..., debug_reason: _Optional[str] = ..., error: _Optional[_Union[_common_pb2.Error, _Mapping]] = ..., debug: _Optional[_Iterable[_Union[_common_pb2.StringPair, _Mapping]]] = ...) -> None: ...
