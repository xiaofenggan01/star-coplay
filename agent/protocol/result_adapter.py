"""Canonical Mod-result normalization shared by every MCP deployment profile.

Routers preserve source results and lifecycle state.  This module is the only
authority allowed to translate Protocol 2.4.5 or V2 result payloads into the
canonical MCP output envelope.

Every production tool — 18 Proto and 16 V2 canonical capabilities — is
unconditionally adapted through this single module.  Proto results are
parsed via ``google.protobuf.json_format.ParseDict`` and re-serialised with
``MessageToDict(..., preserving_proto_field_name=True)`` so the canonical
envelope carries the authoritative protobuf field names.  V2 results
preserve their raw ``data`` block verbatim.
"""
from __future__ import annotations

from typing import Any, Mapping

from google.protobuf.json_format import MessageToDict, ParseDict

from agent.generated.protocol import capability_pb2
from .canonical_surface import canonical_specs
from .tool_mapper import ProtocolMappingError


#: Canonical tool name → ``CommandResult`` oneof field name.
#: Derived from the protobuf schema and kept in sync with
#: ``canonical_surface._PROTO_RESULT_FIELD``.
_PROTO_DATA_FIELD: dict[str, str] = {
    "say": "say",
    "emote": "emote",
    "face": "face",
    "move_to": "move_to",
    "go_to": "go_to",
    "go_to_bed": "go_to_bed",
    "interact": "interact",
    "use_tool": "use_tool",
    "equip": "equip",
    "open_menu": "menu",
    "query_menu": "menu_snapshot",
    "menu_click": "menu",
    "menu_close": "menu",
    "query_inventory": "query_inventory",
    "query_location": "query_location",
    "query_map_exits": "query_map_exits",
    "query_runtime_snapshot": "query_runtime_snapshot",
    "query_world_region": "query_world_region",
    "query_inventory_snapshot": "query_inventory_snapshot",
    "query_ui_snapshot": "query_ui_snapshot",
    "query_inspect_refs": "query_inspect_refs",
}
_PROTO_STATUS = {
    "COMMAND_STATUS_COMPLETED": "COMPLETED",
    "COMMAND_STATUS_REJECTED": "REJECTED",
    "COMMAND_STATUS_FAILED": "FAILED",
    "COMMAND_STATUS_TIMEOUT": "TIMEOUT",
    "COMMAND_STATUS_CANCELLED": "CANCELLED",
    "COMPLETED": "COMPLETED",
    "REJECTED": "REJECTED",
    "FAILED": "FAILED",
    "TIMEOUT": "TIMEOUT",
    "CANCELLED": "CANCELLED",
}
_STATE_STATUSES = {
    "SUCCEEDED": frozenset({"COMPLETED"}),
    # The shared router intentionally has one terminal FAILED lifecycle state;
    # the authoritative Protocol result preserves whether the Mod rejected the
    # command before execution or failed while executing it.
    "FAILED": frozenset({"FAILED", "REJECTED"}),
    "TIMED_OUT": frozenset({"TIMEOUT"}),
    "CANCELLED": frozenset({"CANCELLED"}),
}


def normalize_canonical_result(
    canonical_name: str,
    arguments: Mapping[str, Any],
    *,
    expected_command_id: str,
    lifecycle_state: str,
    raw_output: Mapping[str, Any],
    error_code: str | None = None,
) -> Mapping[str, Any] | None:
    """Normalize a terminal source result, preserving UNKNOWN as uncertainty.

    ``None`` means that the router has no authoritative terminal payload.  It
    is intentionally returned for UNKNOWN/non-terminal lifecycle states so the
    caller cannot manufacture a successful or failed canonical result.
    """

    del arguments  # reserved for truthful compatibility adapters in later slices
    if lifecycle_state not in _STATE_STATUSES:
        return None
    if not isinstance(raw_output, Mapping) or not raw_output:
        raise ProtocolMappingError("terminal result is missing its source payload")

    specs = canonical_specs()
    if canonical_name not in specs:
        raise ProtocolMappingError(f"canonical result adapter is not implemented: {canonical_name}")
    spec = specs[canonical_name]
    if spec.source_surface == "protocol-2.4.5":
        normalized = _normalize_proto(
            canonical_name, spec.wire_capability, expected_command_id,
            lifecycle_state, raw_output,
        )
    else:
        normalized = _normalize_v2(
            spec.wire_capability, expected_command_id, lifecycle_state,
            raw_output, error_code,
        )
    return normalized


def _normalize_proto(
    canonical_name: str,
    wire_capability: str,
    expected_command_id: str,
    lifecycle_state: str,
    raw: Mapping[str, Any],
) -> Mapping[str, Any]:
    source_success = raw.get("success")
    if type(source_success) is not bool:
        raise ProtocolMappingError("Protocol result success must be a boolean")
    try:
        message = ParseDict(dict(raw), capability_pb2.CommandResult())
        raw = MessageToDict(message, preserving_proto_field_name=True)
    except Exception as exc:
        raise ProtocolMappingError("invalid Protocol CommandResult JSON") from exc
    command_id = _required_string(raw, "command_id")
    if command_id != expected_command_id:
        raise ProtocolMappingError("result command_id does not match dispatch")
    capability = _required_string(raw, "capability")
    if capability != wire_capability:
        raise ProtocolMappingError("result capability does not match dispatch")
    raw_status = _required_string(raw, "status")
    status = _PROTO_STATUS.get(raw_status)
    if status is None or status not in _STATE_STATUSES[lifecycle_state]:
        raise ProtocolMappingError("result status does not match lifecycle state")
    # Proto3 JSON omits false scalars. The parsed message is authoritative for
    # this required envelope field; capability data remains omission-preserving.
    success = message.success
    if success != source_success or success != (status == "COMPLETED"):
        raise ProtocolMappingError("result success does not match status")

    observation = raw.get("observation")
    if observation is not None:
        if not isinstance(observation, Mapping):
            raise ProtocolMappingError("Proto observation must be an object")
        observation = observation.get("text", "")
        if not isinstance(observation, str):
            raise ProtocolMappingError("Proto observation text must be a string")

    data = raw.get(_PROTO_DATA_FIELD[canonical_name], {})
    if not isinstance(data, Mapping):
        raise ProtocolMappingError("Proto capability result must be an object")
    result: dict[str, Any] = {
        "command_id": command_id,
        "capability": capability,
        "success": success,
        "status": status,
        "data": dict(data),
    }
    if observation is not None:
        result["observation"] = observation
    error = _normalize_error(raw.get("error"))
    if error is not None:
        result["error"] = error
    if not success and error is None:
        raise ProtocolMappingError("failed result is missing error details")
    return result


def _normalize_v2(
    wire_capability: str,
    expected_command_id: str,
    lifecycle_state: str,
    raw: Mapping[str, Any],
    route_error_code: str | None,
) -> Mapping[str, Any]:
    command_id = _required_string(raw, "action_id")
    if command_id != expected_command_id:
        raise ProtocolMappingError("result action_id does not match dispatch")
    source_status = _required_string(raw, "status")
    success = raw.get("success")
    if type(success) is not bool:
        raise ProtocolMappingError("V2 result success must be a boolean")
    if source_status in {"ok", "partial"}:
        status = "COMPLETED"
        expected_success = True
    elif source_status == "failed":
        expected_success = False
        code = _source_error_code(raw, route_error_code)
        status = "CANCELLED" if code == "CANCELLED" else (
            "TIMEOUT" if code == "TIMEOUT" or code.endswith("_TIMEOUT") else "FAILED"
        )
    else:
        raise ProtocolMappingError("unsupported V2 result status")
    if success != expected_success or status not in _STATE_STATUSES[lifecycle_state]:
        raise ProtocolMappingError("V2 result does not match lifecycle state")
    data = raw.get("data", {})
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise ProtocolMappingError("V2 result data must be an object")
    result: dict[str, Any] = {
        "command_id": command_id,
        "capability": wire_capability,
        "success": success,
        "status": status,
        "data": dict(data),
    }
    observation = raw.get("observation")
    if observation is not None:
        if not isinstance(observation, str):
            raise ProtocolMappingError("V2 observation must be a string")
        result["observation"] = observation
    error = _normalize_error(raw.get("error"))
    if error is not None:
        result["error"] = error
    if not success and error is None:
        raise ProtocolMappingError("failed result is missing error details")
    return result


def _required_string(value: Mapping[str, Any], key: str) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field:
        raise ProtocolMappingError(f"result {key} must be a non-empty string")
    return field


def _source_error_code(raw: Mapping[str, Any], fallback: str | None) -> str:
    error = raw.get("error")
    if isinstance(error, Mapping) and isinstance(error.get("code"), str):
        return str(error["code"])
    return fallback or ""


def _normalize_error(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ProtocolMappingError("result error must be an object")
    code, message = value.get("code"), value.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        raise ProtocolMappingError("result error requires string code and message")
    result: dict[str, Any] = {"code": code, "message": message}
    if type(value.get("retryable")) is bool:
        result["retryable"] = value["retryable"]
    return result
