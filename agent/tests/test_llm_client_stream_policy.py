from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_client  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    llm_client._client = None
    llm_client._client_config_hash = ""
    llm_client.clear_session_overrides()
    monkeypatch.setenv("LLM_RELAY_API_KEY", "test-key")
    monkeypatch.setenv("LLM_RELAY_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("LLM_MODEL", "balanced")
    yield
    llm_client._client = None
    llm_client._client_config_hash = ""
    llm_client.clear_session_overrides()


def test_openai_env_vars_are_ignored(monkeypatch):
    created: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(llm_client, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "old-key")
    monkeypatch.setenv("OPENAI_API_BASE", "http://old-provider/v1")

    llm_client._get_client()

    assert created["api_key"] == "test-key"
    assert str(created["base_url"]) == "http://localhost/v1"


def test_missing_relay_config_fails_even_with_openai_env(monkeypatch):
    monkeypatch.delenv("LLM_RELAY_API_KEY", raising=False)
    monkeypatch.delenv("LLM_RELAY_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "old-key")
    monkeypatch.setenv("OPENAI_API_BASE", "http://old-provider/v1")
    monkeypatch.setattr(llm_client, "_read_dotenv_vars", lambda: {})

    with pytest.raises(RuntimeError, match="LLM relay is not configured"):
        llm_client._get_client()


def _fake_non_stream_response():
    msg = SimpleNamespace(content="ok", tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage, id="x", created=0, model="m")


def _fake_stream_chunks():
    delta = SimpleNamespace(content="ok", tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason="stop", index=0)
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    chunk = SimpleNamespace(
        id="x", created=0, model="m", choices=[choice], usage=usage
    )
    return iter([chunk])


def _patch_client(monkeypatch, captured_kwargs: dict, stream: bool):
    client = MagicMock()
    if stream:
        client.chat.completions.create.side_effect = (
            lambda **kw: (captured_kwargs.update(kw) or _fake_stream_chunks())
        )
    else:
        client.chat.completions.create.side_effect = (
            lambda **kw: (captured_kwargs.update(kw) or _fake_non_stream_response())
        )
    monkeypatch.setattr(llm_client, "_get_client", lambda: client)


def test_tools_forces_non_stream(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=False)
    llm_client.completion(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "f"}}],
    )
    assert captured["stream"] is False
    assert "extra_body" not in captured


def test_response_format_forces_non_stream(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=False)
    llm_client.completion(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
    )
    assert captured["stream"] is False


def test_no_tools_defaults_to_stream(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=True)
    llm_client.completion(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert captured["stream"] is True


def test_caller_stream_override_respected(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=True)
    llm_client.completion(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "f"}}],
        stream=True,
    )
    assert captured["stream"] is True


@pytest.mark.skip(
    reason=(
        "enable_thinking 参数已在 llm_client.py 中全局禁用（避免非标 OpenAI-compatible API 返回 400）。"
        "当源码重新支持 enable_thinking 注入时，移除此 skip。"
    )
)
def test_qwen_injects_enable_thinking_false(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=True)
    llm_client.completion(
        model="Qwen/Qwen3.5-122B-A10B",
        messages=[{"role": "user", "content": "hi"}],
    )
    eb = captured.get("extra_body") or {}
    assert eb.get("enable_thinking") is False
    assert eb.get("chat_template_kwargs", {}).get("enable_thinking") is False


@pytest.mark.skip(
    reason=(
        "enable_thinking 参数已在 llm_client.py 中全局禁用（避免非标 OpenAI-compatible API 返回 400）。"
        "当源码重新支持 enable_thinking 注入时，移除此 skip。"
    )
)
def test_qwen_with_tools_non_stream_and_thinking_disabled(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=False)
    llm_client.completion(
        model="Qwen/Qwen3.5-122B-A10B",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "f"}}],
    )
    assert captured["stream"] is False
    eb = captured.get("extra_body") or {}
    assert eb.get("enable_thinking") is False


def test_qwen_instruct_skipped(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=True)
    llm_client.completion(
        model="Qwen/Qwen2.5-7B-Instruct",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert "extra_body" not in captured or "enable_thinking" not in (
        captured.get("extra_body") or {}
    )


def test_non_qwen_model_not_touched(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=True)
    llm_client.completion(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert "extra_body" not in captured


def test_caller_extra_body_enable_thinking_preserved(monkeypatch):
    captured: dict = {}
    _patch_client(monkeypatch, captured, stream=True)
    llm_client.completion(
        model="Qwen/Qwen3.5-122B-A10B",
        messages=[{"role": "user", "content": "hi"}],
        extra_body={"enable_thinking": True, "foo": "bar"},
    )
    eb = captured.get("extra_body") or {}
    assert eb.get("enable_thinking") is True
    assert eb.get("foo") == "bar"
