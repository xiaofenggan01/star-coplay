import json
import hashlib
import base64
import asyncio
import time
import uuid
from pathlib import Path

import pytest
from agent.generated.protocol import transport_pb2
from modules.gateway import hosted_session
from modules.gateway.hosted_session import HostedHelloFacts, WebSocketHostedSessionRoute, compute_capability_digest
from modules.mcp.contracts.models import GAME_COMMAND_VERSION, GameCommand, SideEffect


ROOT = Path(__file__).resolve().parents[4]


def fixture_contract():
    return json.loads((ROOT / "protocol/schemas/hosted-route-contract.json").read_text())


def test_shared_gateway_route_contract_fixture_is_internally_canonical():
    fixture = fixture_contract()
    vector = fixture["canonical_digest_vector"]
    lines = sorted({"\t".join(entry) + "\n" for entry in vector["entries"]})
    payload = fixture["capability_snapshot_version"] + "\n" + "".join(lines)
    assert "sha256:" + hashlib.sha256(payload.encode()).hexdigest() == vector["expected"]
    framing = fixture["control_framing"]
    assert framing["proto_status_body"] == "command_status_request"
    assert framing["proto_cancel_body"] == "command_cancel"
    assert framing["v2_cancel"] == {
        "type": "command", "action_id": "cmd-vector", "cancel": True,
    }
    for key, body in (("proto_status_base64", "command_status_request"),
                      ("proto_cancel_base64", "command_cancel")):
        envelope = transport_pb2.Envelope()
        envelope.ParseFromString(base64.b64decode(framing[key]))
        assert envelope.WhichOneof("body") == body
        assert envelope.request_id == framing[key.replace("_base64", "_request_id")]
        assert envelope.session_id == "gs-vector"
        assert getattr(envelope, body).command_id == "cmd-vector"


class _Session:
    connection_id = "connection-vector"
    def __init__(self):
        self.outbound = asyncio.Queue()
    def subscribe(self, *, frame_types=None):
        return asyncio.Queue()
    def unsubscribe(self, _queue):
        pass


def _command(capability="say", payload=None):
    digest = compute_capability_digest((("protocol-2.4.5", "say", "2.4.5"),))
    return GameCommand(
        GAME_COMMAND_VERSION, "gateway-vector-message", 1, "idem-vector", "cmd-vector",
        "tool-vector", capability, "canonical.v1", digest, SideEffect.MUTATING,
        payload or {"message": "hello"}, 2000, "ctx-vector", "nonce-vector", 1,
    )


def _route():
    entries = (("protocol-2.4.5", "say", "2.4.5"),)
    facts = HostedHelloFacts("starcoplay.hosted-route.v1", "nonce-vector",
                             compute_capability_digest(entries), entries)
    return WebSocketHostedSessionRoute("gs-vector", "resume-vector", facts, _Session(),
                                       clock=lambda: 1, control_timeout_s=.001)


@pytest.mark.asyncio
async def test_gateway_production_route_generates_the_fixed_control_vectors(monkeypatch):
    fixture = fixture_contract()["control_framing"]
    monkeypatch.setattr(hosted_session.uuid, "uuid4", lambda: uuid.UUID("11111111-1111-1111-1111-111111111111"))
    monkeypatch.setattr(hosted_session.time, "time", lambda: 1)

    status_route = _route()
    assert (await status_route.dispatch(_command())).state.value == "ACCEPTED"
    await status_route._session.outbound.get()
    status_task = asyncio.create_task(status_route.get_status("cmd-vector"))
    status_frame = await status_route._session.outbound.get()
    assert status_frame["payload"] == fixture["proto_status_base64"]
    await status_task

    cancel_route = _route()
    assert (await cancel_route.dispatch(_command())).state.value == "ACCEPTED"
    await cancel_route._session.outbound.get()
    cancel_task = asyncio.create_task(cancel_route.cancel("cmd-vector"))
    cancel_frame = await cancel_route._session.outbound.get()
    assert cancel_frame["payload"] == fixture["proto_cancel_base64"]
    await cancel_task

    v2_route = _route()
    v2_command = _command("water_crops", {"max_count": 1})
    object.__setattr__(v2_command, "capability_digest", v2_route.facts.capability_digest)
    assert (await v2_route.dispatch(v2_command)).state.value == "ACCEPTED"
    await v2_route._session.outbound.get()
    cancel_result = await v2_route.cancel("cmd-vector")
    assert cancel_result.accepted is True
    assert await v2_route._session.outbound.get() == fixture["v2_cancel"]
