"""Build Protocol 2.4.5 CommandRequest messages from mapped params."""

from __future__ import annotations

import uuid

from agent.generated.protocol import capability_pb2

from .tool_mapper import MappedCommand, ProtocolMappingError


def build_command_request(
    mapped: MappedCommand,
    *,
    command_id: str | None = None,
    source: int = capability_pb2.COMMAND_SOURCE_LLM,
    timeout_ms: int | None = None,
) -> capability_pb2.CommandRequest:
    if not mapped.capability:
        raise ProtocolMappingError("capability must be non-empty")
    effective_timeout_ms = mapped.timeout_ms if timeout_ms is None else timeout_ms
    if effective_timeout_ms <= 0:
        raise ProtocolMappingError("timeout_ms must be > 0")
    if source == capability_pb2.COMMAND_SOURCE_UNSPECIFIED:
        raise ProtocolMappingError("source must not be COMMAND_SOURCE_UNSPECIFIED")

    request = capability_pb2.CommandRequest(
        command_id=command_id or _new_command_id(),
        timeout_ms=effective_timeout_ms,
        source=source,
        capability=mapped.capability,
    )
    getattr(request, mapped.oneof_field).CopyFrom(mapped.params)
    selected = request.WhichOneof("params")
    if selected != mapped.oneof_field:
        raise ProtocolMappingError(
            f"CommandRequest params oneof mismatch: expected {mapped.oneof_field}, got {selected}"
        )
    return request


def build_command_requests(
    mapped_commands: list[MappedCommand],
    *,
    source: int = capability_pb2.COMMAND_SOURCE_LLM,
) -> list[capability_pb2.CommandRequest]:
    return [build_command_request(mapped, source=source) for mapped in mapped_commands]


def _new_command_id() -> str:
    return f"cmd_{uuid.uuid4().hex}"
