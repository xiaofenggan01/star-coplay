"""Hosted game-chat input admission and agent-output backflow protection."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from common.observability import emit_metric
from modules.mcp.core.projection import BackgroundJobCompletion
from modules.runtime_manager.contracts import RuntimeInput
from modules.runtime_manager.domain import ResourceExhausted

log = logging.getLogger("platform_ws.gateway.hosted_runtime_input")


def _normalized_message(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def _message_hash(value: str) -> str:
    return hashlib.sha256(_normalized_message(value).encode("utf-8")).hexdigest()


_PLAYER_CANCEL_TERMS = (
    "停下", "停止", "取消", "别做了", "不要继续", "不用去了", "别去了",
    "别去", "算了", "重来", "改成", "换成", "stop", "cancel", "abort", "halt",
)


def _player_cancel_reason(message: str) -> str | None:
    normalized = _normalized_message(message)
    if any(term in normalized for term in _PLAYER_CANCEL_TERMS):
        return "player_requested"
    return None


class HostedBackgroundJobControl(Protocol):
    async def cancel_active(
        self,
        *,
        runtime_id: str,
        generation: int,
        game_session_id: str,
        reason: str,
    ) -> str | None: ...


def _steering_preview(value: RuntimeInput) -> RuntimeInput:
    trace = dict(value.trace_context)
    trace["source"] = "game_chat_steering_preview"
    return RuntimeInput(
        request_id=value.request_id,
        content=(
            "[Steering preview; do not reply or call say for this message. "
            "A normal player turn will follow. Use this only to adjust the "
            "current plan if necessary.]\n"
            f"{value.content}"
        ),
        trace_context=trace,
    )


@dataclass(frozen=True)
class _SayReflection:
    owner_user_id: str
    game_session_id: str
    target_player_id: str
    message_hash: str
    command_id: str
    tool_call_id: str
    operation_id: str
    expires_at: float


@dataclass(frozen=True)
class _QueuedInputReceipt:
    operation_id: str
    status: str = "queued"


@dataclass(frozen=True)
class _PendingRuntimeInput:
    runtime_id: str
    generation: int
    value: RuntimeInput
    queued_at: float
    expires_at: float
    input_source: str


@dataclass(frozen=True)
class _PlayerTurnContext:
    owner_user_id: str
    game_session_id: str
    session: Any


class HostedSayReflectionRegistry:
    """Bounded, process-local registry of Hosted MCP ``say`` emissions.

    The authoritative controlled-player check does not depend on this cache.
    The cache closes races and supports command provenance when a Mod reports a
    reflected chat with missing or stale player identity.
    """

    def __init__(self, *, ttl_seconds: float = 120.0, max_per_session: int = 256,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_per_session = max_per_session
        self._clock = clock
        self._entries: dict[str, deque[_SayReflection]] = defaultdict(deque)
        self._delivered_operations: dict[tuple[str, str, str], float] = {}

    def register(self, *, owner_user_id: str, game_session_id: str,
                 target_player_id: str, message: str, command_id: str,
                 tool_call_id: str, operation_id: str = "") -> None:
        if not owner_user_id or not game_session_id or not message.strip():
            return
        now = self._clock()
        bucket = self._entries[game_session_id]
        self._prune(bucket, now)
        bucket.append(_SayReflection(
            owner_user_id=owner_user_id,
            game_session_id=game_session_id,
            target_player_id=str(target_player_id or ""),
            message_hash=_message_hash(message),
            command_id=command_id,
            tool_call_id=tool_call_id,
            operation_id=operation_id,
            expires_at=now + self._ttl_seconds,
        ))
        while len(bucket) > self._max_per_session:
            bucket.popleft()

    def mark_delivered(self, *, owner_user_id: str, game_session_id: str,
                       operation_id: str) -> None:
        if not owner_user_id or not game_session_id or not operation_id:
            return
        self._delivered_operations[(owner_user_id, game_session_id, operation_id)] = (
            self._clock() + self._ttl_seconds
        )

    def was_delivered(self, *, owner_user_id: str, game_session_id: str,
                      operation_id: str) -> bool:
        key = (owner_user_id, game_session_id, operation_id)
        expires_at = self._delivered_operations.get(key, 0.0)
        if expires_at <= self._clock():
            self._delivered_operations.pop(key, None)
            return False
        return True

    def matches(self, *, owner_user_id: str, game_session_id: str,
                player_id: str | None, message: str, command_id: str = "",
                tool_call_id: str = "") -> bool:
        bucket = self._entries.get(game_session_id)
        if not bucket:
            return False
        now = self._clock()
        self._prune(bucket, now)
        digest = _message_hash(message)
        source_player = str(player_id or "")
        for entry in reversed(bucket):
            if entry.owner_user_id != owner_user_id:
                continue
            if command_id and command_id == entry.command_id:
                return True
            if tool_call_id and tool_call_id == entry.tool_call_id:
                return True
            # Text fallback is intentionally limited to an absent or expected
            # player id so another human repeating the same words is accepted.
            if (digest == entry.message_hash
                    and (not source_player or source_player == entry.target_player_id)):
                return True
        return False

    def _prune(self, bucket: deque[_SayReflection], now: float) -> None:
        while bucket and bucket[0].expires_at <= now:
            bucket.popleft()


class HostedBackgroundJobCompletionRelay:
    """Late-bind MCP completion delivery after Runtime Manager composition."""

    def __init__(self) -> None:
        self._dispatcher: HostedRuntimeInputDispatcher | None = None

    def bind(self, dispatcher: HostedRuntimeInputDispatcher) -> None:
        if self._dispatcher is not None and self._dispatcher is not dispatcher:
            raise RuntimeError("background completion relay already bound")
        self._dispatcher = dispatcher

    async def __call__(self, completion: BackgroundJobCompletion) -> None:
        if self._dispatcher is None:
            raise RuntimeError("background completion relay is not bound")
        await self._dispatcher.dispatch_background_completion(completion)


class HostedTurnCompletionRelay:
    """Late-bind Runtime turn completion after dispatcher composition."""

    def __init__(self) -> None:
        self._dispatcher: HostedRuntimeInputDispatcher | None = None

    def bind(self, dispatcher: HostedRuntimeInputDispatcher) -> None:
        if self._dispatcher is not None and self._dispatcher is not dispatcher:
            raise RuntimeError("turn completion relay already bound")
        self._dispatcher = dispatcher

    async def __call__(self, runtime_id: str, generation: int, value: RuntimeInput,
                       result: Any, error: BaseException | None) -> None:
        if self._dispatcher is None:
            raise RuntimeError("turn completion relay is not bound")
        await self._dispatcher.on_turn_completed(
            runtime_id, generation, value, result, error,
        )


class HostedRuntimeInputDispatcher:
    """Converts trusted controller chat events into billable Runtime inputs."""

    def __init__(self, coordinator: Any, reflections: HostedSayReflectionRegistry,
                 *, clock: Callable[[], float] = time.monotonic,
                 background_jobs: HostedBackgroundJobControl | None = None,
                 event_cache_size: int = 2048,
                 loop_window_seconds: float = 10.0,
                 loop_threshold: int = 3,
                 loop_cooldown_seconds: float = 30.0,
                 max_pending_per_runtime: int = 8,
                 queue_timeout_seconds: float = 600.0,
                 retry_delay_seconds: float = 0.05) -> None:
        self._coordinator = coordinator
        self._reflections = reflections
        self._background_jobs = background_jobs
        self._clock = clock
        self._event_cache_size = event_cache_size
        self._seen_events: set[str] = set()
        self._event_order: deque[str] = deque()
        self._loop_window_seconds = loop_window_seconds
        self._loop_threshold = loop_threshold
        self._loop_cooldown_seconds = loop_cooldown_seconds
        self._repeats: dict[str, deque[float]] = defaultdict(deque)
        self._circuit_open_until: dict[str, float] = {}
        self._max_pending_per_runtime = max_pending_per_runtime
        self._queue_timeout_seconds = queue_timeout_seconds
        self._retry_delay_seconds = retry_delay_seconds
        self._pending: dict[tuple[str, int], deque[_PendingRuntimeInput]] = defaultdict(deque)
        self._pending_compensation: dict[
            tuple[str, int], deque[_PendingRuntimeInput]
        ] = defaultdict(deque)
        self._pending_internal: dict[
            tuple[str, int], deque[_PendingRuntimeInput]
        ] = defaultdict(deque)
        self._queue_workers: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._player_turns: dict[str, _PlayerTurnContext] = {}
        self._cancelled_player_turns: set[str] = set()
        self._silent_turns = 0
        self._compensation_outcomes: dict[str, int] = defaultdict(int)
        self._last_compensation_outcome: str | None = None

    def diagnostics_snapshot(self) -> dict[str, Any]:
        compensation = sum(len(queue) for queue in self._pending_compensation.values())
        player = sum(len(queue) for queue in self._pending.values())
        background = sum(len(queue) for queue in self._pending_internal.values())
        return {
            "queue": {
                "total": compensation + player + background,
                "compensation": compensation,
                "player": player,
                "background": background,
            },
            "delivery": {
                "silent_turns": self._silent_turns,
                "compensation_delivered": self._compensation_outcomes.get("delivered", 0),
                "compensation_failed": sum(
                    self._compensation_outcomes.get(outcome, 0)
                    for outcome in (
                        "failed", "turn_failed", "queue_full", "timeout", "rejected",
                    )
                ),
                "last_outcome": self._last_compensation_outcome,
            },
        }

    def _record_compensation(self, outcome: str) -> None:
        self._compensation_outcomes[outcome] += 1
        self._last_compensation_outcome = outcome

    async def dispatch_game_event(self, session: Any, frame: dict[str, Any]) -> Any | None:
        event = frame.get("event")
        if not isinstance(event, dict) or event.get("type") != "chat_received":
            return None
        owner_user_id = str(getattr(session.instance, "user_id", "") or "")
        game_session_id = str(getattr(session, "hosted_game_session_id", "") or "")
        spec = getattr(session, "hosted_runtime_spec", None)
        if getattr(session, "hosted_connection_role", "observer") != "controller":
            await self._send_status(session, "controller_reconnecting", retryable=True)
            emit_metric(
                "observer_chat_rejected_total", "counter", 1,
                outcome="controller_reconnecting",
            )
            return self._filtered("observer_connection")
        if not owner_user_id or not game_session_id or spec is None:
            return self._filtered("missing_provenance")

        seq = event.get("seq")
        data = event.get("data")
        if not isinstance(seq, int) or seq < 1 or not isinstance(data, dict):
            return self._filtered("missing_provenance")
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            return self._filtered("missing_provenance")
        player_id_value = data.get("player_id")
        player_id = str(player_id_value) if isinstance(player_id_value, (str, int)) else ""
        origin = str(data.get("origin", "")).strip().lower()
        if origin == "agent":
            return self._filtered("agent_origin")
        if origin == "system":
            return self._filtered("system_message")

        facts = getattr(session, "hosted_facts", None)
        controlled_player_id = str(
            getattr(facts, "agent_control_target_player_id", "") or ""
        )
        if player_id and controlled_player_id and player_id == controlled_player_id:
            return self._filtered("controlled_player")
        if self._reflections.matches(
            owner_user_id=owner_user_id,
            game_session_id=game_session_id,
            player_id=player_id or None,
            message=message,
            command_id=str(data.get("command_id", "") or ""),
            tool_call_id=str(data.get("tool_call_id", "") or ""),
        ):
            return self._filtered("agent_say_reflection")
        if not player_id:
            return self._filtered("missing_provenance")

        nonce = str(getattr(facts, "game_instance_nonce", "") or "")
        event_key = f"{owner_user_id}:{game_session_id}:{nonce}:{seq}"
        if event_key in self._seen_events:
            return self._filtered("duplicate_event")
        self._remember_event(event_key)

        repeat_key = f"{owner_user_id}:{game_session_id}:{player_id}:{_message_hash(message)}"
        now = self._clock()
        if self._circuit_open_until.get(repeat_key, 0.0) > now:
            return self._filtered("loop_circuit_open")
        repeats = self._repeats[repeat_key]
        while repeats and repeats[0] <= now - self._loop_window_seconds:
            repeats.popleft()
        repeats.append(now)
        if len(repeats) >= self._loop_threshold:
            self._circuit_open_until[repeat_key] = now + self._loop_cooldown_seconds
            return self._filtered("loop_circuit_open")

        player_name = str(data.get("player_name", "")).strip() or "Player"
        operation_id = f"hosted-chat:{game_session_id}:{nonce}:{seq}"
        trace_context = {
            "source": "game_chat",
            "game_session_id": game_session_id,
            "source_player_id": player_id,
            "event_seq": str(seq),
            "operation_id": operation_id,
        }
        cancel_reason = _player_cancel_reason(message)
        if cancel_reason is not None and self._background_jobs is not None:
            try:
                cancelled_job_id = await self._background_jobs.cancel_active(
                    runtime_id=spec.runtime_id,
                    generation=spec.generation,
                    game_session_id=game_session_id,
                    reason=cancel_reason,
                )
            except Exception:
                log.exception(
                    "active background job cancellation failed",
                    extra={
                        "event": "mcp.background_job.player_cancel_failed",
                        "runtime_id": spec.runtime_id,
                        "generation": spec.generation,
                        "game_session_id": game_session_id,
                        "outcome": "failed",
                    },
                )
            else:
                if cancelled_job_id:
                    trace_context["cancelled_background_job_id"] = cancelled_job_id
        runtime_input = RuntimeInput(
            request_id=operation_id,
            content=f"{player_name}: {message.strip()}",
            trace_context=trace_context,
        )
        self._player_turns[operation_id] = _PlayerTurnContext(
            owner_user_id, game_session_id, session,
        )
        try:
            receipt = await self._coordinator.submit_input_async(
                spec.runtime_id, spec.generation, runtime_input,
            )
        except ResourceExhausted as error:
            if str(error) != "runtime input is already in flight":
                raise
            interrupt = getattr(self._coordinator, "interrupt_input", None)
            interrupted_operation = None
            if interrupt is not None:
                interrupted_operation = await interrupt(
                    spec.runtime_id, spec.generation, "player_superseded",
                )
                if interrupted_operation:
                    self._cancelled_player_turns.add(interrupted_operation)
                    emit_metric(
                        "hosted_runtime_input_total", "counter", 1,
                        input_source="game_chat", outcome="interrupt",
                    )
            if not interrupted_operation:
                # Compatibility path for adapters that predate native abort.
                steer = getattr(self._coordinator, "steer_input", None)
                if steer is not None and await steer(
                    spec.runtime_id, spec.generation, _steering_preview(runtime_input),
                ):
                    emit_metric(
                        "hosted_runtime_input_total", "counter", 1,
                        input_source="game_chat", outcome="steering_preview",
                    )
            receipt = self._queue_input(
                spec.runtime_id,
                spec.generation,
                runtime_input,
                input_source="game_chat",
                player_priority=True,
            )
            if receipt is None:
                self._player_turns.pop(operation_id, None)
                await self._send_status(session, "player_reply_degraded", retryable=False)
                return self._filtered("runtime_input_queue_full")
            emit_metric(
                "hosted_runtime_input_total", "counter", 1,
                input_source="game_chat", outcome="queued",
            )
            return receipt
        except Exception:
            self._player_turns.pop(operation_id, None)
            raise
        emit_metric(
            "hosted_runtime_input_total", "counter", 1,
            input_source="game_chat", outcome="accepted",
        )
        return receipt

    async def on_turn_completed(self, runtime_id: str, generation: int,
                                value: RuntimeInput, result: Any,
                                error: BaseException | None) -> None:
        source = str(value.trace_context.get("source", ""))
        if source not in {"game_chat", "say_compensation"}:
            return
        context = self._player_turns.pop(value.request_id, None)
        if context is None:
            return
        if value.request_id in self._cancelled_player_turns:
            self._cancelled_player_turns.discard(value.request_id)
            return
        delivered = self._reflections.was_delivered(
            owner_user_id=context.owner_user_id,
            game_session_id=context.game_session_id,
            operation_id=value.request_id,
        )
        if delivered:
            if source == "say_compensation":
                self._record_compensation("delivered")
                emit_metric(
                    "say_compensation_total", "counter", 1, outcome="delivered",
                )
            return
        if source == "say_compensation" or error is not None:
            compensation_outcome = (
                "failed" if source == "say_compensation" else "turn_failed"
            )
            self._record_compensation(compensation_outcome)
            emit_metric(
                "say_compensation_total", "counter", 1,
                outcome=compensation_outcome,
            )
            await self._send_status(
                context.session, "player_reply_degraded", retryable=False,
            )
            return

        self._silent_turns += 1
        emit_metric(
            "silent_player_turn_total", "counter", 1, outcome="compensating",
        )
        compensation = RuntimeInput(
            request_id=f"{value.request_id}:say-compensation",
            content=(
                "[The player message just completed without a visible reply. "
                "Call say exactly once now with a concise, natural in-game reply. "
                "Do not include coordinates, tool names, or assistant-only text.]"
            ),
            trace_context={
                "source": "say_compensation",
                "game_session_id": context.game_session_id,
                "parent_operation_id": value.request_id,
            },
        )
        self._player_turns[compensation.request_id] = context
        try:
            await self._coordinator.submit_input_async(
                runtime_id, generation, compensation,
            )
        except ResourceExhausted as exhausted:
            if str(exhausted) != "runtime input is already in flight":
                self._player_turns.pop(compensation.request_id, None)
                raise
            receipt = self._queue_input(
                runtime_id, generation, compensation,
                input_source="say_compensation", player_priority=True,
            )
            if receipt is not None:
                self._record_compensation("queued")
                emit_metric(
                    "say_compensation_total", "counter", 1, outcome="queued",
                )
                return
            self._player_turns.pop(compensation.request_id, None)
            await self._send_status(
                context.session, "player_reply_degraded", retryable=False,
            )
            self._record_compensation("queue_full")
            emit_metric(
                "say_compensation_total", "counter", 1, outcome="queue_full",
            )
            return
        self._record_compensation("accepted")
        emit_metric(
            "say_compensation_total", "counter", 1, outcome="accepted",
        )

    @staticmethod
    async def _send_status(session: Any, code: str, *, retryable: bool) -> None:
        sender = getattr(session, "send", None)
        if sender is None:
            return
        try:
            await sender({
                "type": "hosted_status", "code": code, "retryable": retryable,
            })
        except Exception:
            log.warning(
                "hosted status delivery failed",
                extra={"event": "gateway.hosted_status.failed", "code": code},
            )

    async def dispatch_background_completion(
        self, completion: BackgroundJobCompletion,
    ) -> Any | None:
        runtime_input = RuntimeInput(
            request_id=f"hosted-job-completed:{completion.job_id}",
            content=self._completion_content(completion),
            trace_context={
                "source": "background_job_completion",
                "game_session_id": completion.game_session_ref,
                "binding_ref": completion.binding_ref,
                "job_id": completion.job_id,
                "skill_id": completion.skill_ref.skill_id,
            },
        )
        try:
            receipt = await self._coordinator.submit_input_async(
                completion.runtime_ref, completion.generation, runtime_input,
            )
        except ResourceExhausted as error:
            if str(error) != "runtime input is already in flight":
                raise
            receipt = self._queue_input(
                completion.runtime_ref,
                completion.generation,
                runtime_input,
                input_source="background_job_completion",
                player_priority=False,
            )
            if receipt is None:
                emit_metric(
                    "hosted_runtime_input_total", "counter", 1,
                    input_source="background_job_completion",
                    outcome="queue_full",
                )
                return None
            emit_metric(
                "hosted_runtime_input_total", "counter", 1,
                input_source="background_job_completion", outcome="queued",
            )
            return receipt
        emit_metric(
            "hosted_runtime_input_total", "counter", 1,
            input_source="background_job_completion", outcome="accepted",
        )
        return receipt

    @staticmethod
    def _completion_content(completion: BackgroundJobCompletion) -> str:
        result = {
            "job_id": completion.job_id,
            "skill_id": completion.skill_ref.skill_id,
            "status": completion.state.value.lower(),
            "output": HostedRuntimeInputDispatcher._jsonable_completion_value(
                completion.output,
            ),
            "error_code": completion.error_code,
            "partial_output": (
                HostedRuntimeInputDispatcher._jsonable_completion_value(
                    completion.partial_output,
                )
            ),
            "side_effects_may_have_occurred": (
                completion.side_effects_may_have_occurred
            ),
        }
        encoded = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            default=str,
        )
        if len(encoded.encode("utf-8")) > 12_000:
            encoded = encoded.encode("utf-8")[:12_000].decode(
                "utf-8", errors="ignore",
            ) + "...[truncated]"
        return (
            "[Internal background game task completion. This is not a player "
            "message. Process it in this same Claude session. Inform the player "
            "with exactly one concise say call when the result is relevant. "
            "Do not expose coordinates, tile/map identifiers, tool names, job "
            "identifiers, or raw payloads. Do not answer with assistant text "
            "instead of say.]\n"
            f"{encoded}"
        )

    @staticmethod
    def _jsonable_completion_value(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): HostedRuntimeInputDispatcher._jsonable_completion_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                HostedRuntimeInputDispatcher._jsonable_completion_value(item)
                for item in value
            ]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _queue_input(self, runtime_id: str, generation: int,
                     value: RuntimeInput, *, input_source: str,
                     player_priority: bool) -> _QueuedInputReceipt | None:
        key = (runtime_id, generation)
        compensation_pending = self._pending_compensation[key]
        player_pending = self._pending[key]
        internal_pending = self._pending_internal[key]
        if (len(compensation_pending) + len(player_pending) + len(internal_pending)
                >= self._max_pending_per_runtime):
            if player_priority and internal_pending:
                internal_pending.popleft()
                emit_metric(
                    "hosted_runtime_input_total", "counter", 1,
                    input_source="background_job_completion",
                    outcome="displaced_by_player",
                )
            else:
                return None
        target = (
            compensation_pending if input_source == "say_compensation"
            else player_pending if player_priority else internal_pending
        )
        queued_at = self._clock()
        target.append(_PendingRuntimeInput(
            runtime_id, generation, value, queued_at,
            queued_at + self._queue_timeout_seconds,
            input_source,
        ))
        worker = self._queue_workers.get(key)
        if worker is None or worker.done():
            worker = asyncio.create_task(
                self._drain_queue(key), name=f"hosted-runtime-input-queue:{runtime_id}:{generation}",
            )
            self._queue_workers[key] = worker
        return _QueuedInputReceipt(value.request_id)

    async def _drain_queue(self, key: tuple[str, int]) -> None:
        compensation_pending = self._pending_compensation[key]
        player_pending = self._pending[key]
        internal_pending = self._pending_internal[key]
        try:
            while compensation_pending or player_pending or internal_pending:
                pending = (
                    compensation_pending if compensation_pending
                    else player_pending if player_pending else internal_pending
                )
                item = pending[0]
                remaining = item.expires_at - self._clock()
                if remaining <= 0:
                    pending.popleft()
                    emit_metric(
                        "hosted_runtime_input_total", "counter", 1,
                        input_source=item.input_source, outcome="queue_timeout",
                    )
                    emit_metric(
                        "runtime_input_queue_wait_ms", "timing",
                        max(0.0, self._clock() - item.queued_at) * 1000,
                        input_source=item.input_source, outcome="timeout",
                    )
                    await self._degrade_pending(item, outcome="timeout")
                    continue
                waiter = getattr(self._coordinator, "wait_input_available", None)
                if waiter is not None:
                    available = await waiter(
                        item.runtime_id, item.generation, timeout=remaining,
                    )
                    if not available:
                        pending.popleft()
                        emit_metric(
                            "hosted_runtime_input_total", "counter", 1,
                            input_source=item.input_source, outcome="queue_timeout",
                        )
                        emit_metric(
                            "runtime_input_queue_wait_ms", "timing",
                            max(0.0, self._clock() - item.queued_at) * 1000,
                            input_source=item.input_source, outcome="timeout",
                        )
                        await self._degrade_pending(item, outcome="timeout")
                        continue
                try:
                    await self._coordinator.submit_input_async(
                        item.runtime_id, item.generation, item.value,
                    )
                except ResourceExhausted as error:
                    if str(error) != "runtime input is already in flight":
                        pending.popleft()
                        log.exception("queued runtime input rejected")
                        await self._degrade_pending(item, outcome="rejected")
                        continue
                    if waiter is None:
                        await asyncio.sleep(self._retry_delay_seconds)
                    continue
                except Exception:
                    pending.popleft()
                    log.exception("queued runtime input failed")
                    await self._degrade_pending(item, outcome="failed")
                    continue
                pending.popleft()
                emit_metric(
                    "hosted_runtime_input_total", "counter", 1,
                    input_source=item.input_source, outcome="accepted_from_queue",
                )
                emit_metric(
                    "runtime_input_queue_wait_ms", "timing",
                    max(0.0, self._clock() - item.queued_at) * 1000,
                    input_source=item.input_source, outcome="accepted",
                )
        finally:
            self._queue_workers.pop(key, None)
            if not compensation_pending:
                self._pending_compensation.pop(key, None)
            if not player_pending:
                self._pending.pop(key, None)
            if not internal_pending:
                self._pending_internal.pop(key, None)

    async def _degrade_pending(self, item: _PendingRuntimeInput,
                               *, outcome: str) -> None:
        context = self._player_turns.pop(item.value.request_id, None)
        if context is None:
            return
        await self._send_status(
            context.session, "player_reply_degraded", retryable=False,
        )
        if item.input_source == "say_compensation":
            self._record_compensation(outcome)
            emit_metric(
                "say_compensation_total", "counter", 1, outcome=outcome,
            )

    def _remember_event(self, event_key: str) -> None:
        self._seen_events.add(event_key)
        self._event_order.append(event_key)
        while len(self._event_order) > self._event_cache_size:
            self._seen_events.discard(self._event_order.popleft())

    @staticmethod
    def _filtered(reason: str) -> None:
        emit_metric(
            "hosted_runtime_input_filtered_total", "counter", 1,
            filter_reason=reason,
        )
        log.info(
            "hosted runtime game input filtered reason=%s",
            reason,
            extra={"event": "gateway.hosted_runtime_input.filtered", "reason": reason},
        )
        return None
