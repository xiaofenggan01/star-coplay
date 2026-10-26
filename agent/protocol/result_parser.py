"""Parse Protocol 2.4.5 result envelopes into Agent-consumable results."""

from __future__ import annotations

from google.protobuf.json_format import MessageToDict

from bridge.bridge_result import BridgeResult
from agent.generated.protocol import transport_pb2
from agent.generated.protocol.capability_pb2 import (
    COMMAND_STATUS_COMPLETED,
    CommandResult,
    CommandStatus,
)
from agent.generated.protocol.common_pb2 import ErrorCode


def extract_command_result(envelope: transport_pb2.Envelope):
    if envelope.WhichOneof("body") != "command_result":
        raise ValueError("Envelope body is not command_result")
    return envelope.command_result


def command_result_to_bridge_result(
    result: CommandResult,
    *,
    action_id: str | None = None,
    seq: int = -1,
) -> BridgeResult:
    data_field = result.WhichOneof("data")
    data = {
        "protocol_capability": result.capability,
        "protocol_command_id": result.command_id,
        "protocol_status": CommandStatus.Name(result.status),
        "protocol_data_field": data_field,
    }
    if data_field:
        data["protocol_data"] = MessageToDict(
            getattr(result, data_field),
            preserving_proto_field_name=True,
        )

    error_code = None
    message = None
    if not result.success:
        error_code = (
            ErrorCode.Name(result.error.code)
            if result.error.code
            else "COMMAND_FAILED"
        )
        message = result.error.message or result.error.detail or "Command failed"

    return BridgeResult(
        success=bool(result.success and result.status == COMMAND_STATUS_COMPLETED),
        status="ok" if result.success else "failed",
        data=data,
        observation=result.observation.text,
        side_effects=[],
        error_code=error_code,
        message=message,
        action_id=action_id or result.command_id,
        seq=seq,
    )
