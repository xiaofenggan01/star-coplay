import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.gateway.server import Gateway, GatewayConfig, Session
from modules.runtime.instance_registry import InstanceRegistry, InstanceState


@pytest.mark.asyncio
async def test_gateway_default_accepting():
    """Gateway 默认接受连接"""
    config = GatewayConfig(admin_secret="test_secret")
    gw = Gateway(config, AsyncMock())
    assert gw.accepting is True
    assert gw.current_connections == 0
    assert gw.max_connections == 50


@pytest.mark.asyncio
async def test_gateway_maintenance_mode():
    """维护模式切换"""
    config = GatewayConfig(admin_secret="test_secret")
    gw = Gateway(config, AsyncMock())
    gw.accepting = False
    assert gw.accepting is False
    gw.accepting = True
    assert gw.accepting is True


@pytest.mark.asyncio
async def test_gateway_kick_nonexistent():
    """踢不存在的实例应返回 False"""
    config = GatewayConfig(admin_secret="test_secret")
    gw = Gateway(config, AsyncMock())
    result = await gw.kick_instance("ai_nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_gateway_kick_existing():
    """踢存在的实例应返回 True"""
    config = GatewayConfig(admin_secret="test_secret")
    gw = Gateway(config, AsyncMock())

    # 模拟一个 session
    mock_ws = AsyncMock()
    mock_inst = MagicMock()
    mock_inst.agent_instance_id = "ai_test123"
    session = Session(connection_id="conn_test", instance=mock_inst, ws=mock_ws)
    gw._sessions_by_instance["ai_test123"] = session

    result = await gw.kick_instance("ai_test123")
    assert result is True
    mock_ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_gateway_max_connections_config():
    """max_connections 从配置读取"""
    config = GatewayConfig(max_connections=10)
    gw = Gateway(config, AsyncMock())
    assert gw.max_connections == 10
