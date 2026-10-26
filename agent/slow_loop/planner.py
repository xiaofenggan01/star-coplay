"""
slow_loop.planner — LLM 调用层。

接收组装好的 Prompt（messages 格式），调用 LLM API（通过 llm_client），
解析返回的 JSON 并做两层校验（schema → action_plan 白名单）。

校验失败时自动重试一次 LLM 调用；仍失败则返回 None。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Optional

import llm_client

from report.telemetry import llm_call_data

from .bt_validator import (
    BtValidator,
    DEFAULT_CAPABILITIES,
    validate_action_plan,
)

log = logging.getLogger("slow_loop.planner")

# 匹配 ```json ... ``` 或 ``` ... ``` 代码块
_CODE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


class Planner:
    """LLM 驱动的行动计划规划器。

    输入：结构化 Prompt（messages list[dict] 格式，OpenAI 兼容）
    输出：包含 monologue + action_plan 的 JSON dict

    流程：
        1. _call_llm → 原始文本（json_object 模式）
        2. json.loads / _parse_json(fallback) → dict
        3. _validate_schema → L1 必填字段（monologue + action_plan）
        4. validate_action_plan → L2 action_plan 列表校验 + 白名单
        5. 验证失败 → 重试 1 次 → 仍失败返回 None
    """

    def __init__(
        self,
        llm_fn: Callable | None = None,
        model: str = "",
        max_tokens: int = 1500,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
        max_bt_depth: int = 3,
        max_bt_nodes: int = 15,
    ) -> None:
        self._llm_fn = llm_fn or llm_client.completion
        self._model = model or llm_client.get_default_model()
        if not self._model:
            log.error(
                "Planner: model 未配置。请通过平台注入 LLM_MODEL，"
                " 否则所有 LLM 调用将失败。"
            )
        log.info("Planner 初始化: model=%s", self._model)
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_s = timeout_s
        self._bt_validator = BtValidator(
            max_depth=max_bt_depth,
            max_nodes=max_bt_nodes,
        )
        self._last_llm_meta: dict[str, Any] | None = None
        self._last_failure_reason: str | None = None

    @property
    def last_llm_meta(self) -> dict[str, Any] | None:
        return self._last_llm_meta

    @property
    def last_failure_reason(self) -> str | None:
        return self._last_failure_reason

    def plan(
        self,
        messages: list[dict],
        allowed_capabilities: frozenset[str] | None = None,
    ) -> Optional[dict]:
        """同步调用 LLM，返回验证后的 JSON dict，或 None。

        Args:
            messages: OpenAI messages 格式的 prompt，由 PromptAssembler 组装。
            allowed_capabilities: 动态能力白名单（来自 FileBridge.capabilities()），
                                  未提供时跳过白名单检查。

        Returns:
            通过校验的 JSON dict（含 monologue + action_plan），或 None。
        """
        self._last_llm_meta = None
        self._last_failure_reason = None
        last_error: str = ""
        for attempt in range(2):  # 最多尝试 2 次
            if attempt > 0:
                log.info("重试 LLM 调用 (attempt %d)", attempt + 1)
                # 重试时追加错误反馈，帮助 LLM 修正输出
                retry_messages = messages + [
                    {
                        "role": "user",
                        "content": f"你上次的输出有问题：{last_error}。请重新生成合法的 JSON。",
                    }
                ]
            else:
                retry_messages = messages

            raw = self._call_llm(retry_messages)
            if raw is None:
                last_error = "LLM 调用失败"
                continue

            # L0: json_object 模式保证合法 JSON，直接 loads；兜底走 _parse_json
            try:
                parsed = json.loads(raw) if raw and raw.strip() else None
                if not isinstance(parsed, dict):
                    parsed = None
            except (json.JSONDecodeError, ValueError):
                parsed = self._parse_json(raw)
            if parsed is None:
                last_error = "输出不是合法的 JSON 格式"
                log.warning("JSON 解析失败 (attempt %d)", attempt + 1)
                continue

            # L1: schema 验证（monologue + action_plan）
            if not self._validate_schema(parsed):
                last_error = "缺少必填字段 monologue(str) 或 action_plan(list)"
                log.warning("Schema 验证失败 (attempt %d)", attempt + 1)
                continue

            # L2: action_plan 列表校验
            action_plan = parsed.get("action_plan")
            if action_plan is not None:
                valid, err = validate_action_plan(
                    action_plan, allowed_capabilities=allowed_capabilities
                )
                if not valid:
                    last_error = err
                    log.warning(
                        "action_plan 验证失败 (attempt %d): %s", attempt + 1, err
                    )
                    continue

            log.debug("Planner 输出验证通过")
            self._last_failure_reason = None
            return parsed

        log.error("Planner 两次尝试均失败，返回 None")
        self._last_failure_reason = last_error
        return None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_llm(self, messages: list[dict]) -> Optional[str]:
        """调用 LLM API，返回原始输出文本。"""
        # 每次调用时重新读取 model，确保 Setup UI 修改配置后立即生效
        current_model = llm_client.get_default_model() or self._model
        log.info("Planner._call_llm: model=%s, 即将调用 self._llm_fn", current_model)
        t0 = time.monotonic()
        try:
            response = self._llm_fn(
                model=current_model,
                messages=messages,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                timeout=self._timeout_s,
                response_format={"type": "json_object"},
            )
            latency_ms = (time.monotonic() - t0) * 1000.0
            self._last_llm_meta = llm_call_data(
                current_model, response, latency_ms, trigger="planner"
            )
            content = response.choices[0].message.content
            log.debug("LLM 返回 %d 字符", len(content) if content else 0)
            return content
        except Exception as e:
            log.error("LLM 调用失败: %s", e)
            self._last_llm_meta = None
            return None

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        """从 LLM 输出文本中提取 JSON dict。

        处理以下格式：
            1. 纯 JSON
            2. ```json ... ``` 代码块包裹
            3. 前后有额外文本，JSON 嵌在中间
        """
        if not raw or not raw.strip():
            return None

        # 策略 1: 直接 json.loads
        try:
            result = json.loads(raw.strip())
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

        # 策略 2: 正则提取 markdown 代码块
        match = _CODE_BLOCK_RE.search(raw)
        if match:
            try:
                result = json.loads(match.group(1).strip())
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

        # 策略 3: 找第一个 { 到最后一个 } 之间的内容
        first_brace = raw.find("{")
        last_brace = raw.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            try:
                result = json.loads(raw[first_brace : last_brace + 1])
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    @staticmethod
    def _validate_schema(output: dict) -> bool:
        """L1 校验：检查必填字段存在且类型正确。

        必填：
            - monologue: str — Agent 内心独白
            - action_plan: list — 行动计划列表

        可选：
            - dialogue: dict — 若存在，必须包含 content 字段
        """
        if not isinstance(output.get("monologue"), str):
            return False
        if not isinstance(output.get("action_plan"), list):
            return False

        dialogue = output.get("dialogue")
        if dialogue is not None:
            if not isinstance(dialogue, dict) or "content" not in dialogue:
                return False

        return True
