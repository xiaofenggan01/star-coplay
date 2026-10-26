import pytest
from unittest.mock import AsyncMock, MagicMock

from modules.gateway.server import Gateway, GatewayConfig
from modules.http_api.server import HttpApiConfig, HttpApiServer
from modules.runtime.instance_registry import AgentInstance, InstanceState


@pytest.mark.asyncio
async def test_roadshow_admin_rejects_wrong_secret():
    gw = Gateway(GatewayConfig(admin_secret="secret"), AsyncMock())
    server = HttpApiServer(gw, gw.authenticator, HttpApiConfig(), agent_worker=MagicMock())
    req = MagicMock()
    req.headers = {"x-admin-secret": "wrong"}
    req.query = {}
    assert server._check_admin(req) is not None


@pytest.mark.asyncio
async def test_roadshow_admin_accepts_running_session():
    gw = Gateway(GatewayConfig(admin_secret="secret"), AsyncMock())
    worker = MagicMock()
    worker.send_to_agent = AsyncMock(return_value=True)
    server = HttpApiServer(gw, gw.authenticator, HttpApiConfig(), agent_worker=worker)
    inst = AgentInstance(
        agent_instance_id="ai_demo",
        connection_id="conn_demo",
        user_id="user_demo",
        platform_token="token",
        state=InstanceState.RUNNING,
    )
    gw.registry._by_id[inst.agent_instance_id] = inst
    gw._sessions_by_instance[inst.agent_instance_id] = MagicMock()
    req = MagicMock()
    req.headers = {"x-admin-secret": "secret"}
    req.query = {}
    req.json = AsyncMock(return_value={
        "input_id": "inp_1",
        "text": "hello",
        "agent_instance_id": inst.agent_instance_id,
        "route_key": "main",
    })
    res = await server._admin_roadshow_external_text(req)
    assert res.status == 200
