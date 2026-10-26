#!/usr/bin/env python3
"""
Star-CoPlay Mod 本地测试 WS Server（headless）。

模拟 Gateway 最小 proto-native 协议，供 AI 开发工具直接测试 Mod 能力。
无 stdin 依赖，可后台运行。

启动：
    python3 scripts/dev_ws_server.py &

Proto-native 命令（走 ProtocolCore → ProtoCommandDispatcher）：
    写入: /tmp/ws_protocol_cmd.json
    结果: /tmp/ws_protocol_result.json

Mod config.json:
    "WsUrl": "ws://127.0.0.1:8765/ws",
    "PlatformToken": "dev"
"""
import asyncio
import base64
import json
import os
import sys
import uuid
from copy import deepcopy
from datetime import datetime

try:
    import websockets
except ImportError:
    print("pip3 install websockets")
    sys.exit(1)

# Proto imports
PROTO_AVAILABLE = False
try:
    proto_path = os.path.join(os.path.dirname(__file__), "..", "agent", "generated")
    sys.path.insert(0, proto_path)
    sys.path.insert(0, os.path.join(proto_path, "protocol"))
    from google.protobuf.json_format import MessageToDict, ParseDict
    from protocol import transport_pb2, capability_pb2
    PROTO_AVAILABLE = True
except ImportError:
    print("WARNING: Proto imports failed. Protocol envelope commands unavailable.", flush=True)

PORT = 8765
PROTOCOL_CMD_FILE = "/tmp/ws_protocol_cmd.json"
PROTOCOL_RESULT_FILE = "/tmp/ws_protocol_result.json"

mod_ws = None
pending_protocol_results: dict[str, asyncio.Future] = {}

DIRECTION_MAP = {
    "up": "DIRECTION_UP",
    "down": "DIRECTION_DOWN",
    "left": "DIRECTION_LEFT",
    "right": "DIRECTION_RIGHT",
    "north": "DIRECTION_UP",
    "south": "DIRECTION_DOWN",
    "west": "DIRECTION_LEFT",
    "east": "DIRECTION_RIGHT",
}

ENUM_ALIASES = {
    "mode": {
        "exact": "MOVE_TO_EXACT",
        "move_to_exact": "MOVE_TO_EXACT",
        "edge_warp": "MOVE_TO_EDGE_WARP",
        "edgewarp": "MOVE_TO_EDGE_WARP",
        "move_to_edge_warp": "MOVE_TO_EDGE_WARP",
        "adjacent": "MOVE_TO_ADJACENT",
        "move_to_adjacent": "MOVE_TO_ADJACENT",
    },
    "arrival_mode": {
        "exact": "ARRIVAL_EXACT",
        "arrival_exact": "ARRIVAL_EXACT",
        "adjacent": "ARRIVAL_ADJACENT",
        "arrival_adjacent": "ARRIVAL_ADJACENT",
    },
    "source": {
        "page": "MENU_COMPONENT_SOURCE_PAGE",
        "tab": "MENU_COMPONENT_SOURCE_TAB",
    },
    "item_category": {
        "axe": "ITEM_CATEGORY_AXE",
        "pickaxe": "ITEM_CATEGORY_PICKAXE",
        "hoe": "ITEM_CATEGORY_HOE",
        "watering_can": "ITEM_CATEGORY_WATERING_CAN",
        "wateringcan": "ITEM_CATEGORY_WATERING_CAN",
        "scythe": "ITEM_CATEGORY_SCYTHE",
        "fishing_rod": "ITEM_CATEGORY_FISHING_ROD",
        "fishingrod": "ITEM_CATEGORY_FISHING_ROD",
        "weapon": "ITEM_CATEGORY_WEAPON",
    },
}

SOURCE_MAP = {
    "llm": 1,            # COMMAND_SOURCE_LLM
    "agent_runtime": 2,  # COMMAND_SOURCE_AGENT_RUNTIME
    "user": 3,           # COMMAND_SOURCE_USER
    "diagnostic": 4,     # COMMAND_SOURCE_DIAGNOSTIC
}

STATUS_MAP = {
    0: "UNSPECIFIED",
    1: "ACCEPTED",
    2: "RUNNING",
    10: "COMPLETED",
    11: "REJECTED",
    12: "FAILED",
    13: "TIMEOUT",
    14: "CANCELLED",
}


def now():
    return datetime.now().strftime("%H:%M:%S")


def build_protocol_envelope(data: dict) -> tuple[str, str, str]:
    """Build a protobuf Envelope from JSON input. Returns (base64_payload, command_id, request_id)."""
    capability = data.get("capability", "")
    params = data.get("params", {})
    timeout_ms = data.get("timeout_ms", 5000)
    source_str = data.get("source", "diagnostic")

    command_id = f"proto-{uuid.uuid4().hex[:8]}"
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    message_id = f"msg-{uuid.uuid4().hex[:8]}"
    source = SOURCE_MAP.get(source_str, 4)

    req = capability_pb2.CommandRequest(
        command_id=command_id,
        timeout_ms=timeout_ms,
        source=source,
        capability=capability,
    )

    set_params(req, capability, params)

    env = transport_pb2.Envelope(
        protocol_version="2.4.5",
        message_id=message_id,
        request_id=request_id,
        session_id="dev-session",
        command_request=req,
    )
    payload = base64.b64encode(env.SerializeToString()).decode()
    return payload, command_id, request_id


def set_params(req, capability: str, params: dict):
    """Set CommandRequest params oneof from JSON by using the protobuf schema."""
    params_fields = capability_pb2.CommandRequest.DESCRIPTOR.oneofs_by_name["params"].fields
    field_names = {field.name for field in params_fields}
    if capability not in field_names:
        raise ValueError(f"unknown proto capability '{capability}'")

    normalized = normalize_params(capability, params)
    sub_message = getattr(req, capability)
    ParseDict(normalized, sub_message, ignore_unknown_fields=False)
    sub_message.SetInParent()

    if req.WhichOneof("params") != capability:
        raise ValueError(f"failed to set params oneof for capability '{capability}'")


def normalize_params(capability: str, params: dict) -> dict:
    """Normalize small human-friendly aliases before protobuf ParseDict."""
    normalized = deepcopy(params or {})

    # Backward-compatible shorthands; the canonical format remains proto field names.
    if capability in ("interact", "use_tool") and "target" in normalized and "target_tile" not in normalized:
        normalized["target_tile"] = normalized.pop("target")
    if capability == "move_to" and "target" in normalized:
        target = normalized.pop("target") or {}
        normalized.setdefault("x", target.get("x", 0))
        normalized.setdefault("y", target.get("y", 0))
    if capability == "tp" and "target" in normalized and isinstance(normalized["target"], dict):
        normalize_direction_value(normalized, "face_direction")

    if capability == "query_inspect_refs" and "refs" in normalized:
        normalized["refs"] = [
            {"value": ref} if isinstance(ref, str) else ref
            for ref in normalized["refs"]
        ]

    normalize_direction_value(normalized, "direction")
    normalize_direction_value(normalized, "face_direction")
    normalize_direction_value(normalized, "adjacent_side")
    normalize_direction_value(normalized, "stand_side")

    for field, aliases in ENUM_ALIASES.items():
        normalize_enum_value(normalized, field, aliases)

    return normalized


def normalize_direction_value(params: dict, field: str):
    if field not in params or not isinstance(params[field], str):
        return
    value = params[field].strip()
    key = value.lower()
    if key in DIRECTION_MAP:
        params[field] = DIRECTION_MAP[key]
    elif key.startswith("direction_"):
        params[field] = value.upper()


def normalize_enum_value(params: dict, field: str, aliases: dict[str, str]):
    if field not in params or not isinstance(params[field], str):
        return
    value = params[field].strip()
    key = value.lower()
    if key in aliases:
        params[field] = aliases[key]
    elif "_" in key:
        params[field] = value.upper()


def build_cancel_envelope(command_id: str, reason: str = "test cancel") -> str:
    """Build a protobuf Cancel Envelope. Returns base64 payload."""
    cancel = capability_pb2.CommandCancel(
        command_id=command_id,
        reason=reason,
    )
    env = transport_pb2.Envelope(
        protocol_version="2.4.5",
        message_id=f"msg-{uuid.uuid4().hex[:8]}",
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        session_id="dev-session",
        command_cancel=cancel,
    )
    return base64.b64encode(env.SerializeToString()).decode()


def parse_protocol_result(payload_b64: str) -> dict:
    """Parse a base64-encoded protobuf Envelope containing a CommandResult into a dict."""
    env = transport_pb2.Envelope()
    env.ParseFromString(base64.b64decode(payload_b64))
    body_case = env.WhichOneof("body")

    if body_case != "command_result":
        return {"body_case": body_case, "raw_request_id": env.request_id}

    cr = env.command_result
    result = {
        "type": "protocol_result",
        "request_id": env.request_id,
        "command_id": cr.command_id,
        "capability": cr.capability,
        "success": cr.success,
        "status": STATUS_MAP.get(cr.status, str(cr.status)),
    }

    if cr.observation and cr.observation.text:
        result["observation"] = cr.observation.text

    if cr.error and cr.error.message:
        result["error"] = {
            "code": str(cr.error.code),
            "message": cr.error.message,
        }

    data_case = cr.WhichOneof("data")
    if data_case:
        result["data_type"] = data_case
        typed = getattr(cr, data_case, None)
        if typed:
            from google.protobuf.json_format import MessageToDict
            result["data"] = MessageToDict(typed, preserving_proto_field_name=True)

    return result


async def handle_mod(ws):
    global mod_ws
    mod_ws = ws
    print(f"[{now()}] Mod connected: {ws.remote_address}", flush=True)
    try:
        async for message in ws:
            data = json.loads(message)
            msg_type = data.get("type", "")
            if msg_type == "hello":
                await ws.send(json.dumps({
                    "type": "hello_ok",
                    "agent_instance_id": f"dev-{uuid.uuid4().hex[:8]}",
                    "resumed": False,
                }))
                print(f"[{now()}] Handshake done", flush=True)
            elif msg_type == "protocol_envelope":
                payload = data.get("payload", "")
                request_id = data.get("request_id", "")
                if payload and PROTO_AVAILABLE:
                    parsed = parse_protocol_result(payload)
                    cmd_id = parsed.get("command_id", "")
                    print(f"[{now()}] Proto result: request_id={request_id} command_id={cmd_id} status={parsed.get('status')}", flush=True)
                    if request_id in pending_protocol_results:
                        pending_protocol_results[request_id].set_result(parsed)
                    elif cmd_id in pending_protocol_results:
                        pending_protocol_results[cmd_id].set_result(parsed)
                else:
                    print(f"[{now()}] Proto envelope received (no proto libs to parse)", flush=True)
            elif msg_type == "pong":
                pass
            else:
                pass  # state_snapshot etc, ignore silently
    except websockets.exceptions.ConnectionClosed:
        print(f"[{now()}] Mod disconnected", flush=True)
    finally:
        mod_ws = None


async def send_protocol_command(data: dict, timeout: float = 60) -> dict | None:
    """Build and send a protocol_envelope command. Returns parsed result or None on timeout."""
    global mod_ws
    if mod_ws is None:
        return {"error": "mod not connected"}
    if not PROTO_AVAILABLE:
        return {"error": "protobuf not available"}

    is_cancel = data.get("cancel", False)

    if is_cancel:
        target_cmd_id = data.get("command_id", "")
        reason = data.get("reason", "test cancel")
        payload = build_cancel_envelope(target_cmd_id, reason)
        request_id = f"req-{uuid.uuid4().hex[:8]}"
        future = asyncio.get_event_loop().create_future()
        pending_protocol_results[request_id] = future

        msg = {
            "type": "protocol_envelope",
            "encoding": "base64",
            "payload": payload,
            "capability": "cancel",
            "command_id": target_cmd_id,
            "request_id": request_id,
        }
        await mod_ws.send(json.dumps(msg))
        print(f"[{now()}] Sent proto cancel: command_id={target_cmd_id}", flush=True)
    else:
        payload, command_id, request_id = build_protocol_envelope(data)
        future = asyncio.get_event_loop().create_future()
        pending_protocol_results[request_id] = future

        msg = {
            "type": "protocol_envelope",
            "encoding": "base64",
            "payload": payload,
            "capability": data.get("capability", ""),
            "command_id": command_id,
            "request_id": request_id,
        }
        await mod_ws.send(json.dumps(msg))
        print(f"[{now()}] Sent proto: {data.get('capability')} command_id={command_id} request_id={request_id}", flush=True)

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": "timeout", "request_id": request_id}
    finally:
        pending_protocol_results.pop(request_id, None)


async def protocol_file_watcher():
    last_mtime = 0
    print(f"[{now()}] Protocol file watcher: {PROTOCOL_CMD_FILE}", flush=True)
    while True:
        await asyncio.sleep(0.5)
        try:
            if not os.path.exists(PROTOCOL_CMD_FILE):
                continue
            mtime = os.path.getmtime(PROTOCOL_CMD_FILE)
            if mtime <= last_mtime:
                continue
            last_mtime = mtime
            with open(PROTOCOL_CMD_FILE) as f:
                data = json.load(f)
            os.remove(PROTOCOL_CMD_FILE)

            timeout = data.get("timeout_ms", 5000) / 1000 + 5
            print(f"[{now()}] Protocol file cmd: {data.get('capability', data.get('cancel', '?'))}", flush=True)
            result = await send_protocol_command(data, timeout=timeout)
            with open(PROTOCOL_RESULT_FILE, "w") as f:
                json.dump(result or {"error": "timeout"}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[{now()}] Protocol file watcher error: {e}", flush=True)


async def ping_loop():
    while True:
        await asyncio.sleep(15)
        if mod_ws:
            try:
                await mod_ws.send(json.dumps({"type": "ping"}))
            except Exception:
                pass


async def main():
    server = await websockets.serve(handle_mod, "0.0.0.0", PORT)
    proto_status = "enabled" if PROTO_AVAILABLE else "DISABLED (missing protobuf)"
    print(f"[{now()}] WS server on :{PORT} | proto: {PROTOCOL_CMD_FILE} ({proto_status})", flush=True)
    await asyncio.gather(protocol_file_watcher(), ping_loop())
    server.close()


if __name__ == "__main__":
    asyncio.run(main())
