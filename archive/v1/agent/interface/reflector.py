from __future__ import annotations

from typing import Any

from agent.debug.runtime import TurnTrace
from agent.llm.client import LLMClient, ReflectResult
from agent.llm.prompt_builder import build_reflect_prompt
from agent.memory.manager import MemoryManager


class Reflector:
    def __init__(
        self,
        llm_client: LLMClient,
        memory: MemoryManager,
        reflect_mode: str = "auto",
    ) -> None:
        self.llm = llm_client
        self.memory = memory
        self.reflect_mode = (reflect_mode or "auto").strip().lower()

    def reflect(
        self,
        action: str,
        arguments: dict[str, Any],
        expectation: str,
        actual_result: Any,
        context_summary: str,
        game_time: int = 0,
        day_key: str = "",
        turn_id: str = "",
        request_id: str = "",
        decision_reason: str = "",
        trace: TurnTrace | None = None,
        execution_status: str = "",
    ) -> ReflectResult | None:
        use_rule_only = (
            self.reflect_mode == "auto"
            and execution_status in ("matched", "no_commands")
            and self._all_results_success(actual_result)
        )

        if use_rule_only:
            result = ReflectResult(outcome="success", lesson="", should_distill=False)
            if trace is not None:
                trace.record("reflect_rule_only", {"reason": "auto_skip_llm"})
            self._after_reflect(
                result, action, arguments, expectation, actual_result, context_summary,
                game_time, day_key, turn_id, request_id, decision_reason, trace,
            )
            return result

        recent = self.memory.working_recent(5)
        prompt = build_reflect_prompt(expectation, actual_result, context_summary, recent)
        llm_response = self.llm.reflect(prompt, trace=trace)
        result = llm_response.value if llm_response.ok else None

        if result is None:
            result = self._fallback_reflect(expectation, actual_result)
            if trace is not None:
                trace.record(
                    "reflect_fallback",
                    llm_response.as_dict() if llm_response is not None else {"error_type": "unknown"},
                )
        elif trace is not None:
            trace.record(
                "reflect_result",
                {
                    "outcome": result.outcome,
                    "lesson": result.lesson,
                    "should_distill": result.should_distill,
                    "suggested_rule": result.suggested_rule,
                },
            )

        self._after_reflect(
            result, action, arguments, expectation, actual_result, context_summary,
            game_time, day_key, turn_id, request_id, decision_reason, trace,
        )
        return result

    def _after_reflect(
        self,
        result: ReflectResult,
        action: str,
        arguments: dict[str, Any],
        expectation: str,
        actual_result: Any,
        context_summary: str,
        game_time: int,
        day_key: str,
        turn_id: str,
        request_id: str,
        decision_reason: str,
        trace: TurnTrace | None,
    ) -> None:
        self.memory.push_turn(
            action=action,
            arguments=arguments,
            expectation=expectation,
            result=actual_result,
            outcome=result.outcome,
            lesson=result.lesson,
            context_summary=context_summary,
            game_time=game_time,
            day_key=day_key,
            turn_id=turn_id,
            request_id=request_id,
            decision_reason=decision_reason,
        )

        if result.should_distill or result.outcome == "fail":
            self.memory.record_episode(
                action=action,
                context_summary=context_summary,
                result=actual_result,
                lesson=result.lesson,
                game_time=game_time,
            )

        if result.suggested_rule and isinstance(result.suggested_rule, dict):
            cond = result.suggested_rule.get("condition", "")
            act = result.suggested_rule.get("action", "")
            if cond and act:
                self.memory.add_rule(cond, act)
                if trace is not None:
                    trace.record("reflect_suggested_rule", {"condition": cond, "action": act})

        success_streak = self.memory.working.consecutive_success_count(action)
        if success_streak >= 3 and not result.should_distill:
            result.should_distill = True

    def _all_results_success(self, actual_result: Any) -> bool:
        if isinstance(actual_result, list):
            return bool(actual_result) and all(
                isinstance(r, dict) and r.get("success") for r in actual_result
            )
        if isinstance(actual_result, dict):
            return bool(actual_result.get("success"))
        return False

    def _fallback_reflect(self, expectation: str, actual_result: Any) -> ReflectResult:
        success = False
        if isinstance(actual_result, list):
            success = all(
                isinstance(r, dict) and r.get("success") for r in actual_result
            )
        elif isinstance(actual_result, dict):
            success = bool(actual_result.get("success"))

        if success:
            return ReflectResult(outcome="success", lesson="", should_distill=False)
        return ReflectResult(outcome="fail", lesson="执行未达预期", should_distill=True)
