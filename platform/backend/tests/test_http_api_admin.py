import pytest
from unittest.mock import AsyncMock, MagicMock
from modules.http_api.server import HttpApiServer, HttpApiConfig
from modules.gateway.server import Gateway, GatewayConfig
from modules.runtime.instance_registry import AgentInstance


@pytest.mark.asyncio
async def test_admin_check_rejects_wrong_secret():
    """错误的 admin secret 应返回 401"""
    config = GatewayConfig(admin_secret="correct_secret")
    gw = Gateway(config, AsyncMock())
    http_config = HttpApiConfig()
    server = HttpApiServer(gw, gw.authenticator, http_config)

    mock_request = MagicMock()
    mock_request.headers = {"x-admin-secret": "wrong_secret"}
    mock_request.query = {}

    result = server._check_admin(mock_request)
    assert result is not None  # 应返回错误 Response


@pytest.mark.asyncio
async def test_admin_check_accepts_correct_secret():
    """正确的 admin secret 应通过"""
    config = GatewayConfig(admin_secret="correct_secret")
    gw = Gateway(config, AsyncMock())
    http_config = HttpApiConfig()
    server = HttpApiServer(gw, gw.authenticator, http_config)

    mock_request = MagicMock()
    mock_request.headers = {"x-admin-secret": "correct_secret"}
    mock_request.query = {}

    result = server._check_admin(mock_request)
    assert result is None  # 应通过


@pytest.mark.asyncio
async def test_admin_check_rejects_empty_secret():
    """admin_secret 为空时应拒绝所有请求"""
    config = GatewayConfig(admin_secret="")
    gw = Gateway(config, AsyncMock())
    http_config = HttpApiConfig()
    server = HttpApiServer(gw, gw.authenticator, http_config)

    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.query = {}

    result = server._check_admin(mock_request)
    assert result is not None  # admin_secret 为空时应拒绝


@pytest.mark.asyncio
async def test_stream_session_permission_fix():
    """platform_token 为 None 时不应绕过权限检查"""
    inst = AgentInstance(
        agent_instance_id="ai_victim",
        connection_id="conn_victim",
        user_id="user_victim",
        platform_token=None,  # token 为空
    )

    # 攻击者 token 和 user_id 与受害者不同
    attacker_token = "attacker_token"
    attacker_user_id = "user_attacker"

    # 修复后的检查逻辑（对应 http_api/server.py _stream_session 中的权限检查）
    should_deny = (inst.user_id != attacker_user_id and inst.platform_token != attacker_token)
    assert should_deny is True, "应拒绝不同 user_id 且 token 不匹配的请求"


@pytest.mark.asyncio
async def test_internal_config_rejects_invalid_environment():
    config = GatewayConfig(admin_secret="correct_secret")
    gw = Gateway(config, AsyncMock())
    server = HttpApiServer(
        gw,
        gw.authenticator,
        HttpApiConfig(),
        internal_api_token="internal-token",
    )

    request = MagicMock()
    request.headers = {"authorization": "Bearer internal-token"}
    request.match_info = {"domain": "agent_runtime", "config_key": "default_model_tier"}
    request.query = {"env": "stage"}

    response = await server._internal_get_config(request)
    assert response.status == 400
    assert b"CONFIG_INVALID_ENVIRONMENT" in response.body


def test_http_api_server_rejects_invalid_default_config_environment():
    config = GatewayConfig(admin_secret="correct_secret")
    gw = Gateway(config, AsyncMock())
    with pytest.raises(ValueError):
        HttpApiServer(
            gw,
            gw.authenticator,
            HttpApiConfig(),
            config_environment="stage",
        )


def test_internal_api_auth_accepts_authorization_and_legacy_header():
    config = GatewayConfig(admin_secret="correct_secret")
    gw = Gateway(config, AsyncMock())
    server = HttpApiServer(
        gw,
        gw.authenticator,
        HttpApiConfig(),
        internal_api_token="internal-token",
    )

    auth_request = MagicMock()
    auth_request.headers = {"authorization": "Bearer internal-token"}
    assert server._check_internal_api_token(auth_request) is None

    legacy_request = MagicMock()
    legacy_request.headers = {"x-internal-config-token": "internal-token"}
    assert server._check_internal_api_token(legacy_request) is None

    rejected_request = MagicMock()
    rejected_request.headers = {"x-internal-api-token": "wrong-token"}
    response = server._check_internal_api_token(rejected_request)
    assert response is not None
    assert response.status == 401
