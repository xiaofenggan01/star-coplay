"""Build Protocol 2.4.5 Envelope messages."""

from __future__ import annotations

import time
import uuid

from agent.generated.protocol import capability_pb2, transport_pb2

from .constants import PROTOCOL_VERSION


def build_command_envelope(
    command_request: capability_pb2.CommandRequest,
    *,
    session_id: str,
    request_id: str | None = None,
    message_id: str | None = None,
    timestamp_ms: int | None = None,
    protocol_version: str = PROTOCOL_VERSION,
) -> transport_pb2.Envelope:
    if not session_id:
        raise ValueError("session_id must be non-empty")
    if command_request.WhichOneof("params") is None:
        raise ValueError("command_request must contain params")
    envelope = transport_pb2.Envelope(
        protocol_version=protocol_version,
        message_id=message_id or _new_message_id(),
        request_id=request_id or command_request.command_id,
        session_id=session_id,
        timestamp_ms=_now_ms() if timestamp_ms is None else timestamp_ms,
    )
    envelope.command_request.CopyFrom(command_request)
    if envelope.WhichOneof("body") != "command_request":
        raise ValueError("Envelope body oneof mismatch")
    return envelope


def serialize_envelope(envelope: transport_pb2.Envelope) -> bytes:
    return envelope.SerializeToString()


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _now_ms() -> int:
    return int(time.time() * 1000)
