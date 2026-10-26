"""
llm_client — 统一 LLM 调用封装。

底层使用 openai SDK，对外暴露 completion() 函数，
保持与原调用签名兼容，降低迁移成本。

配置优先级（均来自 .env / 环境变量）：
    1. 函数参数中的 model
    2. 环境变量 LLM_MODEL（模型档位，默认由平台 System LLM Router 解析）
    3. 环境变量 LLM_RELAY_BASE_URL / LLM_RELAY_API_KEY（平台注入的 System LLM 中转）

未配置 LLM_MODEL 时写入 ERROR 日志，不抛异常。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

from memory.event_writer import sanitize_for_utf8_json

log = logging.getLogger("llm_client")

# 模块级单例，避免每次调用都创建 Client
_client: OpenAI | None = None
_client_config_hash: str = ""

# 会话级覆盖（由 build_agent/session_runner 设置），优先级高于 .env
_SESSION_API_BASE: str = ""
_SESSION_API_KEY: str = ""


def set_session_overrides(api_base: str = "", api_key: str = "") -> None:
    """设置进程级平台 LLM relay 覆盖，优先级高于 os.environ 与 .env。

    为空字符串时不覆盖对应项；传入非空新值会使下次 completion 重建 Client。
    """
    global _SESSION_API_BASE, _SESSION_API_KEY, _client, _client_config_hash
    if api_base:
        _SESSION_API_BASE = api_base
    if api_key:
        _SESSION_API_KEY = api_key
    _client = None
    _client_config_hash = ""


def clear_session_overrides() -> None:
    global _SESSION_API_BASE, _SESSION_API_KEY, _client, _client_config_hash
    _SESSION_API_BASE = ""
    _SESSION_API_KEY = ""
    _client = None
    _client_config_hash = ""

# .env 路径（编译后在 exe 旁；源码模式在 agent/ 下）
if getattr(sys, 'frozen', False):
    _ENV_PATH = Path(sys.executable).parent / ".env"
else:
    _ENV_PATH = Path(__file__).parent / ".env"


def _read_dotenv_vars() -> dict[str, str]:
    """直接从 .env 文件读取 LLM 相关变量（不写入 os.environ）。"""
    vars: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return vars
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        vars[key] = value
    return vars


def _get_llm_env() -> dict[str, str]:
    """获取平台 relay LLM 配置，优先读 .env 文件（覆盖 os.environ 中的旧值）。"""
    env = dict(os.environ)
    env.update(_read_dotenv_vars())  # .env 文件优先，覆盖进程启动时的旧值
    return env


def _config_hash(env: dict[str, str]) -> str:
    """计算 LLM 相关配置的 hash，用于检测配置是否变化。"""
    keys = ["LLM_RELAY_API_KEY", "LLM_RELAY_BASE_URL", "LLM_MODEL"]
    return "|".join(f"{k}={env.get(k, '')}" for k in keys)


def _get_client() -> OpenAI:
    global _client, _client_config_hash
    env = _get_llm_env()
    # 会话覆盖优先级最高
    if _SESSION_API_KEY:
        env["LLM_RELAY_API_KEY"] = _SESSION_API_KEY
    if _SESSION_API_BASE:
        env["LLM_RELAY_BASE_URL"] = _SESSION_API_BASE
    current_hash = _config_hash(env)
    if _client is None or current_hash != _client_config_hash:
        api_key = (env.get("LLM_RELAY_API_KEY") or "").strip()
        base_url = (env.get("LLM_RELAY_BASE_URL") or "").strip()
        if not base_url or not api_key:
            raise RuntimeError(
                "LLM relay is not configured. "
                "Agent LLM calls must use LLM_RELAY_BASE_URL and LLM_RELAY_API_KEY."
            )

        _client = OpenAI(api_key=api_key, base_url=base_url)
        _client_config_hash = current_hash
        log.info(
            "LLM client initialized via platform relay — base_url=%s model_tier=%s",
            base_url,
            env.get("LLM_MODEL", ""),
        )
        log.debug("LLM client 已（重）建，配置 hash=%s", current_hash[:32])
    return _client


_QWEN_NON_THINKING_MARKERS = ("instruct", "-coder", "-vl")


def _is_qwen_thinking_model(model: str) -> bool:
    m = (model or "").lower()
    if "qwen" not in m:
        return False
    if any(tag in m for tag in _QWEN_NON_THINKING_MARKERS):
        return False
    return True


def _normalize_model(model: str) -> str:
    """返回模型档位；平台 System LLM Router 负责解析实际模型。"""
    return model


def _normalize_temperature(model: str, temperature: float) -> float:
    """Kimi models only accept temperature=1."""
    if "kimi" in (model or "").lower():
        return 1.0
    return temperature


def get_default_model() -> str:
    """从环境变量读取默认模型档位。

    读取 LLM_MODEL，未配置时写 ERROR 日志。

    Returns:
        模型档位（如 "balanced"），未配置时返回空字符串。
    """
    env = _get_llm_env()
    model = env.get("LLM_MODEL", "").strip()
    if not model:
        log.error(
            "LLM_MODEL 未在 .env 中配置。"
            " 请通过平台注入 LLM_MODEL=<模型档位>（如 balanced）。"
        )
        return ""
    return model


def _normalize_stream_usage(u: Any) -> Any:
    from openai.types.completion_usage import CompletionUsage

    if u is None:
        return None
    if isinstance(u, CompletionUsage):
        return u
    if isinstance(u, dict):
        pt = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        ct = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
        tt = u.get("total_tokens")
        if tt is None:
            tt = pt + ct
        return CompletionUsage(
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=int(tt),
        )
    pt = int(getattr(u, "prompt_tokens", None) or getattr(u, "input_tokens", None) or 0)
    ct = int(getattr(u, "completion_tokens", None) or getattr(u, "output_tokens", None) or 0)
    tt = getattr(u, "total_tokens", None)
    if tt is None:
        tt = pt + ct
    return CompletionUsage(prompt_tokens=pt, completion_tokens=ct, total_tokens=int(tt))


def _approx_completion_usage(
    messages: list[dict[str, Any]],
    full_content: str,
    tool_calls_list: list[Any] | None,
) -> Any:
    from openai.types.completion_usage import CompletionUsage

    try:
        enc_in = json.dumps(messages, ensure_ascii=False)
    except (TypeError, ValueError):
        enc_in = str(messages)
    pin = len(enc_in) // 4
    out_parts: list[str] = [full_content or ""]
    if tool_calls_list:
        for tc in tool_calls_list:
            fn = getattr(getattr(tc, "function", None), "name", None) or ""
            args = getattr(getattr(tc, "function", None), "arguments", None) or ""
            out_parts.append(str(fn))
            out_parts.append(str(args))
    enc_out = "".join(out_parts)
    cout = len(enc_out) // 4
    return CompletionUsage(
        prompt_tokens=pin,
        completion_tokens=cout,
        total_tokens=pin + cout,
    )


def completion(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 1500,
    temperature: float = 0.7,
    timeout: float | None = None,
    **kwargs: Any,
) -> Any:
    """调用 LLM，返回 OpenAI ChatCompletion 响应对象。

    签名兼容旧调用方，方便迁移。
    timeout 参数会传递给底层 HTTP 客户端。
    model 未配置或调用失败时写 ERROR 日志并向上抛出异常。

    流式策略：
    - 如果 kwargs 含 response_format 或 tools → 强制非流式
    - 否则对推理模型强制流式聚合（某些中转站非流式不返回 content）
    """
    client = _get_client()
    normalized = _normalize_model(model)

    log.debug("LLM 调用: model=%s, messages=%d条, max_tokens=%d, timeout=%s",
              normalized, len(messages), max_tokens, timeout)

    try:
        temperature = _normalize_temperature(normalized, temperature)
        call_kwargs: dict[str, Any] = {
            "model": normalized,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **kwargs,
        }
        if timeout is not None:
            call_kwargs["timeout"] = timeout

        if "stream" not in call_kwargs:
            if "tools" in call_kwargs or "response_format" in call_kwargs:
                call_kwargs["stream"] = False
            else:
                call_kwargs["stream"] = True

        if _is_qwen_thinking_model(normalized) and "extra_body" not in call_kwargs:
            call_kwargs["extra_body"] = {
                "enable_thinking": False,
                "chat_template_kwargs": {"enable_thinking": False},
            }

        call_kwargs = sanitize_for_utf8_json(call_kwargs)

        if call_kwargs.get("stream"):
            if "stream_options" not in call_kwargs:
                call_kwargs["stream_options"] = {"include_usage": True}
            stream = client.chat.completions.create(**call_kwargs)

            # 聚合所有 chunk（同时处理 content 和 tool_calls）
            content_parts: list[str] = []
            # tool_calls 增量拼接：{index: {"name": str, "arguments": str}}
            tc_accum: dict[int, dict[str, str]] = {}
            first_chunk = None
            final_finish_reason = "stop"
            stream_usage: Any = None

            for chunk in stream:
                if first_chunk is None:
                    first_chunk = chunk
                cu = getattr(chunk, "usage", None)
                if cu is not None:
                    stream_usage = cu
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    # 拼接 content
                    if delta.content:
                        content_parts.append(delta.content)
                    # 拼接 tool_calls（增量格式）
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tc_accum:
                                tc_accum[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc_delta.id:
                                tc_accum[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tc_accum[idx]["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tc_accum[idx]["arguments"] += tc_delta.function.arguments
                    # 取最后一帧的 finish_reason
                    fr = chunk.choices[0].finish_reason
                    if fr is not None:
                        final_finish_reason = fr

            # 构造完整响应对象（模拟非流式响应）
            if first_chunk:
                from openai.types.chat import ChatCompletion, ChatCompletionMessage, ChatCompletionMessageToolCall
                from openai.types.chat.chat_completion import Choice
                from openai.types.chat.chat_completion_message_tool_call import Function

                full_content = ''.join(content_parts) if content_parts else ""

                # 构造 tool_calls 列表
                tool_calls_list = None
                if tc_accum:
                    tool_calls_list = []
                    for idx in sorted(tc_accum.keys()):
                        tc = tc_accum[idx]
                        tool_calls_list.append(ChatCompletionMessageToolCall(
                            id=tc["id"] or f"call_{idx}",
                            type="function",
                            function=Function(
                                name=tc["name"],
                                arguments=tc["arguments"],
                            ),
                        ))

                message = ChatCompletionMessage(
                    role="assistant",
                    content=full_content if full_content else None,
                    tool_calls=tool_calls_list,
                )

                choice = Choice(
                    index=0,
                    message=message,
                    finish_reason=final_finish_reason,
                )

                usage_obj = _normalize_stream_usage(stream_usage)
                if usage_obj is None:
                    usage_obj = _approx_completion_usage(
                        messages, full_content, tool_calls_list
                    )

                response = ChatCompletion(
                    id=first_chunk.id,
                    choices=[choice],
                    created=first_chunk.created,
                    model=first_chunk.model,
                    object="chat.completion",
                    usage=usage_obj,
                )

                tc_count = len(tool_calls_list) if tool_calls_list else 0
                log.debug("流式聚合完成: content=%d字符, tool_calls=%d个, finish_reason=%s",
                         len(full_content), tc_count, final_finish_reason)

                return response
            else:
                raise RuntimeError("流式响应为空")
        else:
            # 非推理模型或已指定 stream 参数，直接调用
            return client.chat.completions.create(**call_kwargs)

    except Exception as e:
        status_code = getattr(e, "status_code", None)
        response = getattr(e, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        if status_code == 429:
            log.warning(
                "LLM 请求被限流(429): model=%s。中转站返回 Too Many Requests，当前调用会失败并由上层决定是否重试。",
                normalized,
                extra={
                    "event": "agent.llm.rate_limited",
                    "capability": "llm_completion",
                    "error_code": "llm_rate_limited",
                },
            )

        log.error(
            "LLM 调用失败: model=%s, error=%s。"
            " 请检查平台注入的 LLM_MODEL / LLM_RELAY_API_KEY / LLM_RELAY_BASE_URL 配置。",
            normalized,
            e,
            extra={
                "event": "agent.llm.failed",
                "capability": "llm_completion",
                "error_code": f"llm_http_{status_code}" if status_code else "llm_call_failed",
            },
        )
        raise
