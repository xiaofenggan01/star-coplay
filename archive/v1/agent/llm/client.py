from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_TOOL_CALL_RE = re.compile(r"\[TOOL_CALL\]\s*(\{.*?\})(?=\s*\[TOOL_CALL\]|\s*$)", re.DOTALL)

from agent.debug.runtime import TurnTrace, clip_text


@dataclass(slots=True)
class LLMDecision:
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    expectation: str = ""


@dataclass(slots=True)
class ReflectResult:
    outcome: str
    lesson: str
    should_distill: bool
    suggested_rule: dict[str, Any] | None = None


@dataclass(slots=True)
class LLMResponse:
    ok: bool
    value: Any = None
    error_type: str = ""
    error_message: str = ""
    status_code: int | None = None
    latency_ms: int = 0
    request_bytes: int = 0
    response_bytes: int = 0
    response_preview: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
            "response_preview": self.response_preview,
        }


class LLMClient:
    def __init__(self, provider: str, model: str, api_key: str, base_url: str) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.logger = logging.getLogger("agent.llm")

    @property
    def enabled(self) -> bool:
        return all([self.model, self.api_key, self.base_url])

    def plan(self, prompt: dict[str, Any], tools: list[dict[str, Any]], trace: TurnTrace | None = None) -> LLMResponse:
        if not self.enabled:
            return self._failure("plan", "disabled", "llm_not_configured", trace=trace)
        body = {
            "model": self.model,
            "messages": prompt["messages"],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        response = self._call("plan", body, trace)
        if not response.ok:
            return response
        decision = self._parse_plan_response(response.value)
        if decision is None:
            return self._failure(
                "plan",
                "invalid_plan_response",
                "no_valid_tool_decision",
                response=response,
                trace=trace,
            )
        return self._success("plan", decision, response, trace)

    def reflect(self, prompt: dict[str, Any], trace: TurnTrace | None = None) -> LLMResponse:
        if not self.enabled:
            return self._failure("reflect", "disabled", "llm_not_configured", trace=trace)
        body = {
            "model": self.model,
            "messages": prompt["messages"],
            "temperature": 0.0,
        }
        response = self._call("reflect", body, trace)
        if not response.ok:
            return response
        result = self._parse_reflect_response(response.value)
        if result is None:
            return self._failure(
                "reflect",
                "invalid_reflect_response",
                "response_not_valid_json",
                response=response,
                trace=trace,
            )
        return self._success("reflect", result, response, trace)

    def distill(self, prompt: dict[str, Any], trace: TurnTrace | None = None) -> LLMResponse:
        if not self.enabled:
            return self._failure("distill", "disabled", "llm_not_configured", trace=trace)
        body = {
            "model": self.model,
            "messages": prompt["messages"],
            "temperature": 0.0,
        }
        response = self._call("distill", body, trace)
        if not response.ok:
            return response
        result = self._parse_distill_response(response.value)
        if result is None:
            return self._failure(
                "distill",
                "invalid_distill_response",
                "response_not_valid_json_array",
                response=response,
                trace=trace,
            )
        return self._success("distill", result, response, trace)

    def _call(self, stage: str, body: dict[str, Any], trace: TurnTrace | None = None) -> LLMResponse:
        request_text = json.dumps(body, ensure_ascii=False)
        started = time.perf_counter()
        try:
            resp = requests.post(
                self._chat_completions_url(),
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
        except requests.Timeout:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return self._failure(
                stage,
                "timeout",
                "request_timeout",
                latency_ms=latency_ms,
                request_bytes=len(request_text.encode("utf-8")),
                trace=trace,
            )
        except requests.RequestException as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return self._failure(
                stage,
                "request_error",
                clip_text(str(exc), 300),
                latency_ms=latency_ms,
                request_bytes=len(request_text.encode("utf-8")),
                trace=trace,
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        response_text = resp.text or ""
        preview = clip_text(response_text.strip(), 1000)
        meta = {
            "stage": stage,
            "provider": self.provider,
            "model": self.model,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "request_bytes": len(request_text.encode("utf-8")),
            "response_bytes": len(response_text.encode("utf-8")),
            "response_preview": preview,
        }
        if not resp.ok:
            return self._failure(
                stage,
                "http_error",
                f"http_status_{resp.status_code}",
                status_code=resp.status_code,
                latency_ms=latency_ms,
                request_bytes=meta["request_bytes"],
                response_bytes=meta["response_bytes"],
                response_preview=preview,
                trace=trace,
            )
        try:
            data = resp.json()
        except ValueError:
            return self._failure(
                stage,
                "invalid_json",
                "response_not_json",
                status_code=resp.status_code,
                latency_ms=latency_ms,
                request_bytes=meta["request_bytes"],
                response_bytes=meta["response_bytes"],
                response_preview=preview,
                trace=trace,
            )
        payload = LLMResponse(
            ok=True,
            value=data,
            status_code=resp.status_code,
            latency_ms=latency_ms,
            request_bytes=meta["request_bytes"],
            response_bytes=meta["response_bytes"],
            response_preview=preview,
        )
        self._log(stage, {"status": "http_ok", **payload.as_dict()}, trace)
        return payload

    def _success(
        self,
        stage: str,
        value: Any,
        response: LLMResponse,
        trace: TurnTrace | None = None,
    ) -> LLMResponse:
        payload = LLMResponse(
            ok=True,
            value=value,
            status_code=response.status_code,
            latency_ms=response.latency_ms,
            request_bytes=response.request_bytes,
            response_bytes=response.response_bytes,
            response_preview=response.response_preview,
        )
        self._log(stage, {"status": "parsed_ok", **payload.as_dict()}, trace)
        return payload

    def _failure(
        self,
        stage: str,
        error_type: str,
        error_message: str,
        response: LLMResponse | None = None,
        status_code: int | None = None,
        latency_ms: int = 0,
        request_bytes: int = 0,
        response_bytes: int = 0,
        response_preview: str = "",
        trace: TurnTrace | None = None,
    ) -> LLMResponse:
        payload = LLMResponse(
            ok=False,
            error_type=error_type,
            error_message=error_message,
            status_code=status_code if status_code is not None else (response.status_code if response else None),
            latency_ms=latency_ms if latency_ms else (response.latency_ms if response else 0),
            request_bytes=request_bytes if request_bytes else (response.request_bytes if response else 0),
            response_bytes=response_bytes if response_bytes else (response.response_bytes if response else 0),
            response_preview=response_preview or (response.response_preview if response else ""),
        )
        self._log(stage, {"status": "failed", **payload.as_dict()}, trace)
        return payload

    def _log(self, stage: str, payload: dict[str, Any], trace: TurnTrace | None) -> None:
        record = {"stage": stage, "provider": self.provider, "model": self.model, **payload}
        self.logger.info(json.dumps(record, ensure_ascii=False))
        if trace is not None:
            trace.record(f"llm_{stage}", record)

    def _chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _parse_plan_response(self, data: dict[str, Any]) -> LLMDecision | None:
        decisions = self._parse_plan_response_multi(data)
        return decisions[0] if decisions else None

    def _parse_plan_response_multi(self, data: dict[str, Any]) -> list[LLMDecision]:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return []
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            content = message.get("content")
            results = []
            for tc in tool_calls:
                d = self._parse_tool_call(tc, content)
                if d is not None:
                    results.append(d)
            return results
        function_call = message.get("function_call")
        if isinstance(function_call, dict):
            d = self._parse_legacy_function_call(function_call, message.get("content"))
            return [d] if d else []
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            results = self._parse_tool_call_markers(content)
            if results:
                return results
            d = self._parse_json_content(content)
            return [d] if d else []
        return []

    def plan_multi(self, prompt: dict[str, Any], tools: list[dict[str, Any]],
                   trace: TurnTrace | None = None) -> tuple[list[LLMDecision], LLMResponse]:
        if not self.enabled:
            return [], self._failure("plan", "disabled", "llm_not_configured", trace=trace)
        body = {
            "model": self.model,
            "messages": prompt["messages"],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        response = self._call("plan", body, trace)
        if not response.ok:
            return [], response
        decisions = self._parse_plan_response_multi(response.value)
        if not decisions:
            return [], self._failure(
                "plan", "invalid_plan_response", "no_valid_tool_decision",
                response=response, trace=trace,
            )
        self._log("plan", {"status": "parsed_ok", "decision_count": len(decisions)}, trace)
        return decisions, LLMResponse(
            ok=True, value=decisions[0],
            status_code=response.status_code, latency_ms=response.latency_ms,
            request_bytes=response.request_bytes, response_bytes=response.response_bytes,
            response_preview=response.response_preview,
        )

    def _parse_reflect_response(self, data: dict[str, Any]) -> ReflectResult | None:
        text = self._extract_text(data)
        if not text:
            return None
        try:
            obj = json.loads(self._clean_json(text))
        except json.JSONDecodeError:
            return None
        return ReflectResult(
            outcome=obj.get("outcome", "partial"),
            lesson=obj.get("lesson", ""),
            should_distill=bool(obj.get("should_distill", False)),
            suggested_rule=obj.get("suggested_rule"),
        )

    def _parse_distill_response(self, data: dict[str, Any]) -> list[dict[str, Any]] | None:
        text = self._extract_text(data)
        if not text:
            return None
        try:
            result = json.loads(self._clean_json(text))
        except json.JSONDecodeError:
            return None
        return result if isinstance(result, list) else None

    @staticmethod
    def _strip_think(text: str) -> str:
        """移除模型输出的 <think>...</think> 推理内容。"""
        return _THINK_RE.sub("", text).strip()

    def _extract_text(self, data: dict[str, Any]) -> str | None:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if not isinstance(content, str):
            return None
        return self._strip_think(content) or None

    def _clean_json(self, text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        return cleaned

    def _parse_tool_call(self, tool_call: dict[str, Any], content: Any) -> LLMDecision | None:
        function = tool_call.get("function") or {}
        tool_name = function.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        arguments = self._parse_arguments(function.get("arguments"))
        if arguments is None:
            return None
        expectation = ""
        if isinstance(arguments.get("expectation"), str):
            expectation = arguments.pop("expectation")
        return LLMDecision(
            tool_name=tool_name.strip(),
            arguments=arguments,
            reason=self._extract_reason(content, tool_name),
            expectation=expectation,
        )

    def _parse_legacy_function_call(self, function_call: dict[str, Any], content: Any) -> LLMDecision | None:
        tool_name = function_call.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        arguments = self._parse_arguments(function_call.get("arguments"))
        if arguments is None:
            return None
        expectation = ""
        if isinstance(arguments.get("expectation"), str):
            expectation = arguments.pop("expectation")
        return LLMDecision(
            tool_name=tool_name.strip(),
            arguments=arguments,
            reason=self._extract_reason(content, tool_name),
            expectation=expectation,
        )

    def _parse_tool_call_markers(self, content: str) -> list[LLMDecision]:
        cleaned = self._strip_think(content)
        if "[TOOL_CALL]" not in cleaned:
            return []
        results: list[LLMDecision] = []
        for m in _TOOL_CALL_RE.finditer(cleaned):
            try:
                payload = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            name = payload.get("name") or payload.get("tool_name")
            args = payload.get("arguments") or payload.get("parameters") or {}
            if not isinstance(name, str) or not name.strip():
                continue
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                continue
            expectation = ""
            if isinstance(args.get("expectation"), str):
                expectation = args.pop("expectation")
            results.append(LLMDecision(
                tool_name=name.strip(),
                arguments=args,
                reason=str(payload.get("reason", name))[:200],
                expectation=expectation,
            ))
        return results

    def _parse_json_content(self, content: str) -> LLMDecision | None:
        cleaned = self._clean_json(content)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        tool_name = payload.get("tool_name")
        arguments = payload.get("arguments")
        if not isinstance(tool_name, str) or not isinstance(arguments, dict):
            return None
        reason = payload.get("reason", tool_name)
        expectation = payload.get("expectation", "")
        return LLMDecision(
            tool_name=tool_name.strip(),
            arguments=arguments,
            reason=reason if isinstance(reason, str) else tool_name,
            expectation=expectation if isinstance(expectation, str) else "",
        )

    def _parse_arguments(self, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return parsed
        return None

    def _extract_reason(self, content: Any, default: str) -> str:
        if isinstance(content, str) and content.strip():
            cleaned = self._strip_think(content)
            return cleaned[:200] if cleaned else default
        return default
