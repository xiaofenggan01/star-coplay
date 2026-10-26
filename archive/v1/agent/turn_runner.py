from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from agent.bridge.command_sender import CommandSender
from agent.bridge.event_reader import EventReader
from agent.bridge.state_watcher import StateSnapshot
from agent.bridge.status_writer import StatusWriter
from agent.config import AgentConfig
from agent.debug.runtime import DebugRuntime
from agent.execution.tool_base import MacroTool, ToolDecision
from agent.execution.tool_registry import ToolRegistry
from agent.execution.worker import WorkerExecutor
from agent.interface.perceiver import Perceiver
from agent.interface.planner import Planner
from agent.interface.reflector import Reflector
from agent.knowledge.evolver import KnowledgeEvolver
from agent.memory.emotion import detect_emotion
from agent.memory.manager import MemoryManager
from agent.planning.event_reactor import EventReactor
from agent.planning.goal import GoalManager
from agent.planning.mcp import MCPCallback
from agent.planning.plan_queue import PlanQueue
from agent.planning.precondition import PreconditionChecker
from agent.planning.task import Task, TaskLogManager, TaskStatus


@dataclass
class TurnState:
    phase: str = "init"
    day_key: str = ""
    decision: ToolDecision | None = None
    llm_response: Any = None
    cmd_id: str = ""
    execution: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)
    result: list[dict[str, Any]] = field(default_factory=list)
    reflect_result: Any = None
    emotion_detected: str = "neutral"
    task: Task | None = None


class TurnRunner:
    def __init__(
        self,
        config: AgentConfig,
        sender: CommandSender,
        event_reader: EventReader,
        status_writer: StatusWriter,
        perceiver: Perceiver,
        registry: ToolRegistry,
        planner: Planner,
        reflector: Reflector,
        evolver: KnowledgeEvolver,
        memory: MemoryManager,
        debug_runtime: DebugRuntime,
        task_log: TaskLogManager,
        mcp: MCPCallback,
        worker: WorkerExecutor,
        goal_manager: GoalManager | None = None,
        plan_queue: PlanQueue | None = None,
        precondition_checker: PreconditionChecker | None = None,
        event_reactor: EventReactor | None = None,
    ) -> None:
        self.config = config
        self.sender = sender
        self.event_reader = event_reader
        self.status_writer = status_writer
        self.perceiver = perceiver
        self.registry = registry
        self.planner = planner
        self.reflector = reflector
        self.evolver = evolver
        self.memory = memory
        self.debug_runtime = debug_runtime
        self.task_log = task_log
        self.mcp = mcp
        self.worker = worker
        self.goal_manager = goal_manager or GoalManager()
        self.plan_queue = plan_queue or PlanQueue()
        self.precondition_checker = precondition_checker or PreconditionChecker()
        self.event_reactor = event_reactor or EventReactor()
        self.last_day_key = ""
        self._cmd_counter = 0
        self._last_action = ""

    def _next_cmd_id(self) -> str:
        self._cmd_counter += 1
        return f"c{self._cmd_counter:04d}"

    def run(
        self,
        snapshot: StateSnapshot,
        last_game_day: int,
        turn_count: int,
    ) -> tuple[int, int]:
        state = TurnState()
        context = self.perceiver.perceive(snapshot.payload)

        if context.status in ("paused", "loading"):
            self.status_writer.idle()
            return last_game_day, turn_count
        if context.status == "shutdown":
            self.status_writer.shutdown()
            return last_game_day, turn_count

        state.day_key = self.memory.build_day_key(snapshot.payload)
        trace = self.debug_runtime.start_turn(state.day_key, snapshot.mtime, context.state_summary())
        trace.attach("state", snapshot.payload)

        # ── Phase 1: Events ──
        state.phase = "events"
        events = self.event_reader.read_new()
        if events:
            trace.attach("events", events)
            self._ingest_events(events, state.day_key, context.time_of_day, trace.turn_id)
            reactive_steps = self.event_reactor.react(events, context, self.goal_manager)
            if reactive_steps:
                self.plan_queue.push_front_many(reactive_steps)
                trace.record("event_reactor_inject", [s.as_dict() for s in reactive_steps])
            if self.event_reactor.is_fast_interrupt(events):
                trace.record("fast_interrupt_cycle", True)

        current_day = context.day_of_month
        if self.last_day_key and state.day_key != self.last_day_key:
            self._handle_day_change(state.day_key, trace, context.time_of_day)

        # ── Phase 2: Watchdog ──
        state.phase = "watchdog"
        self._watchdog_check(state.day_key, context.time_of_day, trace)

        # ── Phase 3: CEO ──
        state.phase = "ceo"
        if self.mcp.active_task is None:
            self.status_writer.thinking(about="规划中…")
            new_task, state.llm_response = self.planner.plan_task(
                context=context,
                memory=self.memory,
                registry=self.registry,
                day_key=state.day_key,
                goal_manager=self.goal_manager,
                plan_queue=self.plan_queue,
                trace=trace,
            )
            if new_task is not None:
                self.mcp.on_task_created(new_task, state.day_key, context.time_of_day, trace.turn_id)
                state.task = new_task
            else:
                self.status_writer.idle()
                summary: dict[str, Any] = {"status": "idle", "day_key": state.day_key, "context": context.state_summary()}
                if state.llm_response is not None and not state.llm_response.ok:
                    summary["llm"] = state.llm_response.as_dict()
                trace.finish("idle", summary)
                self.last_day_key = state.day_key
                print(json.dumps(summary, ensure_ascii=False))
                return current_day, turn_count
        else:
            state.task = self.mcp.active_task

        # ── Phase 4: Worker ──
        state.phase = "worker"
        state.decision = self.worker.resolve_tool(state.task, context, trace)
        if state.decision is None:
            self.mcp.report_result(
                success=True,
                message="all_steps_done",
                day_key=state.day_key,
                game_time=context.time_of_day,
                turn_id=trace.turn_id,
            )
            trace.record("worker_task_complete", {"task_id": state.task.task_id[:8]})
            self.status_writer.idle()
            summary = {"status": "task_complete", "day_key": state.day_key, "task": state.task.description}
            trace.finish("task_complete", summary)
            self.last_day_key = state.day_key
            print(json.dumps(summary, ensure_ascii=False))
            return current_day, turn_count

        # ── Phase 5: Execute ──
        state.phase = "execute"
        state.cmd_id = self._next_cmd_id()
        if state.decision.tool_name == "send_chat":
            self.status_writer.chatting()
        else:
            self.status_writer.acting(task=state.decision.tool_name)

        state.execution = self._execute(state.decision.tool_name, state.decision.arguments, context, state.cmd_id, trace)
        state.outcome = state.execution["outcome"]
        state.result = self._normalize_result(state.outcome)
        exec_status = state.outcome.get("status", "") if isinstance(state.outcome, dict) else ""
        self._last_action = state.decision.tool_name

        # ── Phase 6: Reflect ──
        state.phase = "reflect"
        self.status_writer.thinking(about="反思中…")
        state.reflect_result = self.reflector.reflect(
            action=state.decision.tool_name,
            arguments=state.decision.arguments,
            expectation=state.decision.expectation,
            actual_result=state.result,
            context_summary=context.state_summary(),
            game_time=context.time_of_day,
            day_key=state.day_key,
            turn_id=trace.turn_id,
            request_id=state.cmd_id,
            decision_reason=state.decision.reason,
            trace=trace,
            execution_status=exec_status,
        )

        # ── Phase 7: MCP Callback ──
        state.phase = "mcp_callback"
        success = state.reflect_result and state.reflect_result.outcome == "success"
        if success:
            if not self.goal_manager.is_idle():
                self.goal_manager.advance()
            self.memory.working.last_failure = None
            self.mcp.report_progress(
                step_info=state.decision.tool_name,
                day_key=state.day_key,
                game_time=context.time_of_day,
                turn_id=trace.turn_id,
            )
            if self.worker.is_task_done():
                self.mcp.report_result(
                    success=True,
                    message=f"completed:{state.decision.tool_name}",
                    day_key=state.day_key,
                    game_time=context.time_of_day,
                    turn_id=trace.turn_id,
                )
                trace.record("mcp_task_completed", {"task_id": state.task.task_id[:8]})
        else:
            fail_reason = state.reflect_result.lesson if state.reflect_result else "unknown"
            self.memory.working.last_failure = {
                "action": state.decision.tool_name,
                "reason": fail_reason,
                "expectation": state.decision.expectation,
            }
            self.mcp.report_result(
                success=False,
                message=fail_reason[:120],
                day_key=state.day_key,
                game_time=context.time_of_day,
                turn_id=trace.turn_id,
            )
            self.goal_manager.clear()
            self.plan_queue.clear()
            trace.record("mcp_task_failed", {
                "task_id": state.task.task_id[:8],
                "failed_tool": state.decision.tool_name,
                "reason": fail_reason,
            })

        if state.reflect_result and state.reflect_result.should_distill:
            turn_count += 1
            if turn_count % 5 == 0:
                distill_count = self.evolver.distill_from_day(state.day_key, trace=trace)
                trace.record("conditional_distill", {"day_key": state.day_key, "count": distill_count})

        self.status_writer.idle()

        result_status = state.outcome.get("status", "unknown") if isinstance(state.outcome, dict) else "unknown"
        summary = {
            "status": "completed",
            "turn_id": trace.turn_id,
            "day_key": state.day_key,
            "cmd_id": state.cmd_id,
            "task": state.task.description if state.task else "",
            "task_id": state.task.task_id[:8] if state.task else "",
            "tool": state.decision.tool_name,
            "expect": state.decision.expectation,
            "reason": state.decision.reason,
            "result_status": result_status,
            "outcome": state.reflect_result.outcome if state.reflect_result else "unknown",
            "lesson": state.reflect_result.lesson if state.reflect_result else "",
            "result": state.result,
            "emotion": state.emotion_detected,
        }
        goal = self.goal_manager.current_goal
        if goal:
            summary["goal"] = goal.progress()
        if state.llm_response is not None and not state.llm_response.ok:
            summary["llm"] = state.llm_response.as_dict()
        trace.finish("completed", summary)
        self.last_day_key = state.day_key
        print(json.dumps(summary, ensure_ascii=False))
        if not self.config.dry_run:
            time.sleep(self.config.poll_interval)
        return current_day, turn_count

    # ── Watchdog ──

    def _watchdog_check(self, day_key: str, game_time: int, trace: Any) -> None:
        task = self.mcp.active_task
        if task is None or task.status != TaskStatus.RUNNING:
            return
        task.increment_turn()
        if task.status == TaskStatus.TIMEOUT:
            self.mcp.report_timeout(
                day_key=day_key,
                game_time=game_time,
                turn_id=trace.turn_id,
            )
            self.goal_manager.clear()
            self.plan_queue.clear()
            trace.record("watchdog_timeout", {
                "task_id": task.task_id[:8],
                "elapsed": task.elapsed_turns,
                "timeout": task.timeout_turns,
            })

    # ── Day change ──

    def _handle_day_change(self, new_day_key: str, trace: Any, game_time: int = 0) -> None:
        self.event_reader.reset_cursor()
        goal_summary = self.goal_manager.day_summary()
        if goal_summary:
            self.memory.record_event(
                category="special_event",
                summary=f"day_end_goals | {json.dumps(goal_summary, ensure_ascii=False)}",
                day_key=self.last_day_key,
                game_time=2600,
                turn_id=trace.turn_id,
                priority=2,
            )
        if self.mcp.active_task is not None:
            self.mcp.cancel_active_task(
                reason="day_change",
                day_key=self.last_day_key,
                game_time=2600,
                turn_id=trace.turn_id,
            )
        self.memory.refresh_long_term(self.last_day_key)
        self.goal_manager.clear()
        self.plan_queue.clear()
        maintenance = self.evolver.daily_maintenance(self.last_day_key, trace=trace)
        trace.record("daily_maintenance", maintenance)

    # ── Execution ──

    def _execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: Any,
        cmd_id: str,
        trace: Any,
    ) -> dict[str, Any]:
        tool = self.registry.get(tool_name)
        if isinstance(tool, MacroTool):
            commands = tool.expand(arguments, context)
            for index, command in enumerate(commands):
                command["cmd_id"] = f"{cmd_id}_{index}"
            trace.record("execute_commands", {"tool_name": tool_name, "commands": commands})
            if commands:
                self.sender.send(commands)
                last_cmd_id = commands[-1]["cmd_id"]
                result = self.event_reader.wait_cmd_result(
                    cmd_id=last_cmd_id,
                    timeout=self.config.macro_result_timeout,
                    poll_interval=self.config.poll_interval,
                )
                if result:
                    outcome = {"status": "matched", "results": [result]}
                else:
                    outcome = {"status": "timeout", "results": [{"success": False, "message": "timeout", "error_code": "TIMEOUT"}]}
            else:
                outcome = {"status": "no_commands", "results": [{"success": True, "message": "no tiles to process"}]}
            trace.record("execute_result", outcome)
            return {"commands": commands, "outcome": outcome}

        command = tool.build_command(arguments, cmd_id=cmd_id)
        trace.record("execute_commands", {"tool_name": tool_name, "commands": [command]})
        self.sender.send([command])
        result = self.event_reader.wait_cmd_result(
            cmd_id=cmd_id,
            timeout=self.config.result_timeout,
            poll_interval=self.config.poll_interval,
        )
        if result:
            outcome = {"status": "matched", "results": [result]}
        else:
            outcome = {"status": "timeout", "results": [{"success": False, "message": "timeout", "error_code": "TIMEOUT"}]}
        trace.record("execute_result", outcome)
        return {"commands": [command], "outcome": outcome}

    def _normalize_result(self, outcome: dict[str, Any]) -> list[dict[str, Any]]:
        results = outcome.get("results")
        if isinstance(results, list):
            return results
        return [{"success": False, "message": outcome.get("status", "unknown"), "error_code": "UNKNOWN"}]

    # ── Event ingestion ──

    _STANDING_KEYWORDS = ("每天", "每日", "每个早上", "每次", "always", "every day", "daily")

    def _ingest_events(self, events: list[dict[str, Any]], day_key: str, game_time: int, turn_id: str) -> None:
        context_tags = []
        for event in events:
            event_type = str(event.get("type") or "")
            if event_type == "time_changed":
                continue
            if event_type == "cmd_result":
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                cid = str(data.get("cmd_id") or "")
                self.memory.record_event(
                    category="decision_trace",
                    summary=f"cmd_result | {cid} ok={data.get('success')}",
                    payload=data,
                    day_key=day_key,
                    game_time=game_time,
                    turn_id=turn_id,
                    priority=0,
                )
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            if event_type == "agent_action_cancelled":
                if self.mcp.active_task is not None:
                    self.mcp.cancel_active_task(
                        reason="agent_action_cancelled",
                        day_key=day_key,
                        game_time=game_time,
                        turn_id=turn_id,
                    )
                self.goal_manager.clear()
                self.plan_queue.clear()
                self.memory.record_event(
                    category="special_event",
                    summary=self._event_summary(event_type, data),
                    payload=data,
                    day_key=day_key,
                    game_time=game_time,
                    turn_id=turn_id,
                    priority=3,
                )
                continue

            if event_type == "chat_received":
                message = str(data.get("message") or "").strip()
                if message.startswith("/"):
                    self._handle_player_command(message, day_key, game_time, turn_id)
                    continue
                self._detect_chat_emotion(message, day_key, game_time, turn_id, context_tags)

            if event_type == "user_directive":
                directive = str(data.get("directive") or "").strip()
                if directive:
                    print(f"[user_directive] {directive}")
                    self._inject_player_instruction(
                        directive,
                        day_key,
                        game_time,
                        turn_id,
                        raw_display=f"/agent {directive}",
                    )
                continue

            summary = self._event_summary(event_type, data)
            if not summary:
                continue
            priority = 3 if event_type in {"inventory_changed", "npc_nearby", "player_action"} else 2
            category = "special_event"
            if event_type == "player_dialogue":
                category = "player_dialogue"
                priority = 3
                self._check_standing_order(data)
                text = str(data.get("message") or data.get("text") or "")
                if text:
                    npc = str(data.get("npc_name") or data.get("speaker") or "")
                    self._detect_chat_emotion(text, day_key, game_time, turn_id, context_tags, npc)
            self.memory.record_event(
                category=category,
                summary=summary,
                payload=data,
                day_key=day_key,
                game_time=game_time,
                turn_id=turn_id,
                priority=priority,
            )

    def _detect_chat_emotion(
        self,
        text: str,
        day_key: str,
        game_time: int,
        turn_id: str,
        context_tags: list[str],
        npc_name: str = "",
    ) -> None:
        emotion = detect_emotion(text)
        if emotion != "neutral":
            self.memory.detect_and_record_emotion(
                text=text,
                last_action=self._last_action,
                context_tags=context_tags,
                npc_name=npc_name,
                day_key=day_key,
                game_time=game_time,
                turn_id=turn_id,
            )

    def _handle_player_command(self, message: str, day_key: str, game_time: int, turn_id: str) -> None:
        cmd = message.strip()

        if cmd.lower() in ("/reset_agent", "/reset"):
            print(f"[player_cmd] reset_agent — 清空上下文")
            if self.mcp.active_task is not None:
                self.mcp.cancel_active_task(
                    reason="player_reset",
                    day_key=day_key,
                    game_time=game_time,
                    turn_id=turn_id,
                )
            self.memory.working.clear()
            self.plan_queue.clear()
            self.goal_manager.clear()
            self.status_writer.idle()
            return

        instruction = cmd[1:].strip()
        if not instruction:
            return
        self._inject_player_instruction(instruction, day_key, game_time, turn_id, raw_display=cmd)

    def _inject_player_instruction(
        self,
        instruction: str,
        day_key: str,
        game_time: int,
        turn_id: str,
        *,
        raw_display: str | None = None,
    ) -> None:
        print(f"[player_cmd] instruction: {instruction}")
        self.memory.working.add_player_command(instruction)
        if self.mcp.active_task is not None:
            self.mcp.cancel_active_task(
                reason=f"player_command:{instruction[:40]}",
                day_key=day_key,
                game_time=game_time,
                turn_id=turn_id,
            )
        self.plan_queue.clear()
        self.goal_manager.clear()
        raw = raw_display if raw_display is not None else instruction
        self.memory.record_event(
            category="player_dialogue",
            summary=f"player_command | {instruction}",
            payload={"command": instruction, "raw": raw},
            day_key=day_key,
            game_time=game_time,
            turn_id=turn_id,
            priority=3,
        )

    def _check_standing_order(self, data: dict[str, Any]) -> None:
        text = str(data.get("message") or data.get("text") or "")
        if not text:
            return
        for kw in self._STANDING_KEYWORDS:
            if kw in text:
                self.memory.working.add_standing_order(text)
                return

    def _event_summary(self, event_type: str, data: dict[str, Any]) -> str:
        if event_type == "day_started":
            weather = data.get("weather")
            return f"day_started | weather={weather} day={data.get('day')} season={data.get('season')}"
        if event_type == "day_ending":
            earnings = data.get("earnings_today", 0)
            return f"day_ending | day={data.get('day')} season={data.get('season')} earnings={earnings}"
        if event_type == "inventory_changed":
            added = data.get("added") or []
            removed = data.get("removed") or []
            source = data.get("source", "unknown")
            parts = [f"source={source}"]
            for a in added[:3]:
                price = a.get("sell_price", 0)
                parts.append(f"+{a.get('name')}(x{a.get('count',1)},${price})")
            for r in removed[:3]:
                price = r.get("sell_price", 0)
                parts.append(f"-{r.get('name')}(x{r.get('count',1)},${price})")
            return f"inventory_changed | {' '.join(parts)}"
        if event_type == "npc_nearby":
            talked = "talked" if data.get("talked_today") else "not_talked"
            gifted = "gifted" if data.get("gifted_today") else "not_gifted"
            return f"npc_nearby | {data.get('name')} hearts={data.get('hearts')} dist={data.get('distance')} {talked} {gifted}"
        if event_type == "player_warped":
            return f"player_warped | {data.get('from_location')} -> {data.get('to_location')}"
        if event_type == "energy_low":
            pct = data.get("percentage", data.get("percent", "?"))
            return f"energy_low | {data.get('current')}/{data.get('max')} ({pct}%)"
        if event_type == "player_action":
            return f"player_action | {data.get('action_type')} tool={data.get('tool')} at={data.get('location')}"
        if event_type == "season_changed":
            return f"season_changed | {data.get('from')} -> {data.get('to')} year={data.get('year')}"
        if event_type == "chat_received":
            player = data.get("player_name", "unknown")
            msg = data.get("message", "")
            return f"chat_received | {player}: {msg}"
        if event_type == "agent_action_cancelled":
            return (
                f"agent_action_cancelled | reason={data.get('reason')} "
                f"action={data.get('action_state')} tile=({data.get('tile_x')},{data.get('tile_y')})"
            )
        return event_type
