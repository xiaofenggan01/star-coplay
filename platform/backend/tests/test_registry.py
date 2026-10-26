import asyncio
import pytest
from modules.runtime.instance_registry import InstanceRegistry, InstanceState, EndedReason


@pytest.mark.asyncio
async def test_find_by_resume_token_returns_none_for_destroyed_instance():
    """resume token 对应的实例已被销毁时，应返回 None 并清理孤立 token"""
    reg = InstanceRegistry()
    inst = await reg.create(connection_id="conn_test")
    await reg.transition(inst, InstanceState.AUTHENTICATING)
    await reg.transition(inst, InstanceState.STARTING)
    await reg.transition(inst, InstanceState.RUNNING)

    token = await reg.issue_resume_token(inst, ttl_s=300)

    # 销毁实例
    await reg.transition(inst, InstanceState.DRAINING)
    await reg.transition(inst, InstanceState.DESTROYED)
    await reg.destroy(inst)

    # resume token 应该返回 None
    result = await reg.find_by_resume_token(token)
    assert result is None


@pytest.mark.asyncio
async def test_find_by_resume_token_expired():
    """过期的 resume token 应返回 None"""
    reg = InstanceRegistry()
    inst = await reg.create(connection_id="conn_test")
    token = await reg.issue_resume_token(inst, ttl_s=0.01)
    await asyncio.sleep(0.02)
    result = await reg.find_by_resume_token(token)
    assert result is None


@pytest.mark.asyncio
async def test_find_by_resume_token_valid():
    """有效的 resume token 应返回实例"""
    reg = InstanceRegistry()
    inst = await reg.create(connection_id="conn_test")
    token = await reg.issue_resume_token(inst, ttl_s=300)
    result = await reg.find_by_resume_token(token)
    assert result is not None
    assert result.agent_instance_id == inst.agent_instance_id
