"""Authenticated internal Runtime Manager product API."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from enum import Enum
import inspect
import json
from typing import Callable, Mapping, Optional

from aiohttp import web

from modules.runtime_manager.contracts import RuntimeInput
from modules.runtime_manager.admission import RuntimeAdmissionPolicy
from modules.runtime_manager.domain import (
    DomainError, HostedRuntimeNotAllowed, InvalidSpec, NotFound,
    ResourceExhausted, RuntimeProductSpec,
)

RUNTIME_PRINCIPAL_KEY = web.AppKey("runtime_principal", Mapping)


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def setup_runtime_manager_routes(app: web.Application, coordinator,
                                 authenticate: Callable[[web.Request], object],
                                 admission_policy: RuntimeAdmissionPolicy | None = None,
                                 health_service=None) -> None:
    admission_policy = admission_policy or RuntimeAdmissionPolicy()
    if health_service is None:
        from modules.runtime_manager.health import RuntimeManagerHealthService
        health_service = RuntimeManagerHealthService(coordinator)
    async def invoke(request: web.Request, operation):
        log_id = str(request.get("log_id", ""))
        def error_response(code: str, message: str, status: int):
            payload = {"code": code, "message": message}
            if log_id:
                payload["log_id"] = log_id
            return web.json_response({"error": payload}, status=status)
        principal = authenticate(request)
        if inspect.isawaitable(principal):
            principal = await principal
        if isinstance(principal, web.StreamResponse):
            return principal
        if not isinstance(principal, Mapping) or not principal.get("service_id"):
            return error_response("unauthorized", "signed service claims required", 401)
        request[RUNTIME_PRINCIPAL_KEY] = principal
        try:
            return await operation()
        except DomainError as error:
            status = (
                400 if isinstance(error, InvalidSpec)
                else 404 if isinstance(error, NotFound)
                else 403 if isinstance(error, HostedRuntimeNotAllowed)
                else 429 if isinstance(error, ResourceExhausted)
                else 409
            )
            return error_response(error.code, str(error), status)
        except (KeyError, TypeError, ValueError) as error:
            return error_response("invalid_spec", str(error), 400)
        except LookupError as error:
            return error_response("not_found", str(error), 404)
        except RuntimeError as error:
            code = str(error) if str(error) in {
                "already_stopping", "operation_in_progress", "adapter_unavailable",
                "ready_timeout", "process_exited", "provider_rejected", "readiness_failed",
                "runtime_quarantined", "binding_not_active",
                "authority_credential_not_issued",
            } else "internal_error"
            status = 409 if code in {
                "already_stopping", "operation_in_progress", "binding_not_active",
                "authority_credential_not_issued",
            } else 503
            return error_response(code, str(error), status)
        except Exception:
            return error_response("internal_error", "runtime manager unavailable", 503)

    def caller(request: web.Request) -> tuple[str, str]:
        principal = request[RUNTIME_PRINCIPAL_KEY]
        user_id = str(principal.get("user_id", "")).strip()
        agentee_id = str(principal.get("agentee_id", "")).strip()
        if not user_id or not agentee_id:
            raise ValueError("missing runtime caller owner context")
        return user_id, agentee_id

    def verify_owner(request: web.Request, record) -> None:
        if request[RUNTIME_PRINCIPAL_KEY].get("all_tenants") is True:
            return
        user_id, agentee_id = caller(request)
        if record.spec.user_id != user_id or record.spec.agentee_id != agentee_id:
            raise LookupError(record.runtime_id)

    def response(record):
        return web.json_response(_jsonable(asdict(record)))

    async def parse_spec(request):
        body = await request.json()
        if "defer_start_until_bound" in body:
            raise ValueError("defer_start_until_bound is removed; use runtimes:provision")
        user_id, agentee_id = caller(request)
        if not body.get("operation_id"):
            raise ValueError("operation_id is required")
        if body["operation_id"] != body.get("activation_key"):
            raise ValueError("operation_id must equal stable activation_key")
        existing = await coordinator.get(body["runtime_id"])
        if existing is not None:
            verify_owner(request, existing)
        if body["user_id"] != user_id or body["agentee_id"] != agentee_id:
            raise LookupError(body["runtime_id"])
        return RuntimeProductSpec(
            runtime_id=body["runtime_id"], generation=int(body["generation"]),
            activation_key=body["activation_key"], user_id=body["user_id"],
            agentee_id=body["agentee_id"], adapter_kind=body["adapter_kind"],
            adapter_config_ref=body["adapter_config_ref"],
            persona_artifact_ref=body["persona_artifact_ref"],
            resource_budget=body.get("resource_budget", {}), idle_ttl_seconds=3600,
            allowed_capabilities=tuple(body.get("allowed_capabilities", ())),
        )

    async def provision(request):
        async def operation():
            spec = await parse_spec(request)
            admission_policy.require_allowed(spec.user_id)
            return response(await coordinator.provision(spec))
        return await invoke(request, operation)

    async def ensure(request):
        async def operation():
            spec = await parse_spec(request)
            admission_policy.require_allowed(spec.user_id)
            return response(await coordinator.ensure(spec))
        return await invoke(request, operation)

    async def get_runtime(request):
        async def operation():
            record = await coordinator.get(request.match_info["runtime_id"])
            if record is None:
                raise LookupError(request.match_info["runtime_id"])
            verify_owner(request, record)
            return response(await coordinator.status_for(record))
        return await invoke(request, operation)

    async def ensure_binding(request):
        async def operation():
            body = await request.json()
            if not body.get("operation_id") or body.get("generation") is None:
                raise ValueError("operation_id and generation are required")
            if any(key in body for key in (
                "binding_id", "game_session_id", "user_id", "agentee_id",
                "game_instance_nonce", "capability_digest", "resume_identity",
            )):
                raise ValueError("Binding authority fields are not accepted")
            current = await coordinator.get(request.match_info["runtime_id"])
            if current is None:
                raise LookupError(request.match_info["runtime_id"])
            verify_owner(request, current)
            binding = await coordinator.ensure_binding(
                current.runtime_id, int(body["generation"]), current.spec.user_id,
                current.spec.agentee_id, body["operation_id"],
            )
            return web.json_response(_jsonable(asdict(binding)))
        return await invoke(request, operation)

    async def desired(request):
        async def operation():
            body = await request.json()
            if not body.get("operation_id"):
                raise ValueError("operation_id is required")
            current_owner = await coordinator.get(request.match_info["runtime_id"])
            if current_owner is not None:
                verify_owner(request, current_owner)
            if body.get("desired_state") == "running":
                current = await coordinator.get(request.match_info["runtime_id"])
                if current is not None:
                    admission_policy.require_allowed(current.spec.user_id)
                if current is not None and current.ready and current.active:
                    return response(current)
                if current is not None and current.active:
                    return response(await coordinator.ensure(current.spec))
                raw = body["runtime_spec"]
                if raw.get("runtime_id") != request.match_info["runtime_id"]:
                    raise ValueError("runtime_id mismatch")
                user_id, agentee_id = caller(request)
                if raw.get("user_id") != user_id or raw.get("agentee_id") != agentee_id:
                    raise LookupError(request.match_info["runtime_id"])
                admission_policy.require_allowed(raw["user_id"])
                return response(await coordinator.ensure(RuntimeProductSpec(
                    runtime_id=raw["runtime_id"], generation=int(raw["generation"]),
                    activation_key=raw["activation_key"], user_id=raw["user_id"],
                    agentee_id=raw["agentee_id"], adapter_kind=raw["adapter_kind"],
                    adapter_config_ref=raw["adapter_config_ref"],
                    persona_artifact_ref=raw["persona_artifact_ref"],
                    resource_budget=raw.get("resource_budget", {}), idle_ttl_seconds=3600,
                    allowed_capabilities=tuple(raw.get("allowed_capabilities", ())),
                )))
            if body.get("desired_state") != "stopped":
                raise ValueError("desired_state must be running or stopped")
            return response(await coordinator.stop(
                request.match_info["runtime_id"], int(body["generation"]), body["operation_id"],
                body.get("reason", "desired_stopped"),
            ))
        return await invoke(request, operation)

    async def input_(request):
        async def operation():
            body = await request.json()
            if not body.get("operation_id"):
                raise ValueError("operation_id is required")
            current = await coordinator.get(request.match_info["runtime_id"])
            if current is None:
                raise LookupError(request.match_info["runtime_id"])
            # This is an operator control-plane endpoint. Tenant authority comes
            # exclusively from signed claims; x-runtime-* headers are never trusted.
            verify_owner(request, current)
            admission_policy.require_allowed(current.spec.user_id)
            if any(key in body for key in ("runtime_id", "user_id", "agentee_id")):
                raise ValueError("runtime owner fields are not accepted")
            content = body.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            try:
                content_size = len(content.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError("content must be valid UTF-8") from exc
            if content_size > getattr(coordinator, "max_input_bytes", 64 * 1024):
                record_rejection = getattr(coordinator, "record_input_rejection", None)
                if record_rejection is not None:
                    record_rejection(
                        request.match_info["runtime_id"], int(body["generation"]),
                        "runtime_input_bytes",
                    )
                raise ResourceExhausted("runtime input exceeds configured byte limit")
            receipt = await coordinator.submit_input_async(
                request.match_info["runtime_id"], int(body["generation"]),
                RuntimeInput(
                    request_id=body["operation_id"], content=content,
                    trace_context={
                        "log_id": str(request.get("log_id", "")),
                        "request_id": str(request.get("request_id", "")),
                        "operation_id": body["operation_id"],
                    },
                ),
            )
            return web.json_response(_jsonable(asdict(receipt)), status=202)
        return await invoke(request, operation)

    async def output_(request):
        async def operation():
            current = await coordinator.get(request.match_info["runtime_id"])
            if current is None:
                raise LookupError(request.match_info["runtime_id"])
            verify_owner(request, current)
            query_params = request.query
            receipt = query_params.get("receipt")
            if receipt is not None:
                try:
                    receipt_payload = json.loads(receipt)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise ValueError("receipt must be a valid JSON InputReceipt") from exc
                if not isinstance(receipt_payload, dict):
                    raise ValueError("receipt must be a JSON object")
            since = query_params.get("since")
            limit_str = query_params.get("limit", "50")
            try:
                limit = int(limit_str)
            except (ValueError, TypeError):
                raise ValueError("limit must be an integer")
            from modules.runtime_manager.contracts import OutputQuery
            query = OutputQuery(receipt=receipt, since=since, limit=limit)
            page = await coordinator.read_output(
                request.match_info["runtime_id"], current.generation, query,
            )
            return web.json_response(_jsonable(asdict(page)))
        return await invoke(request, operation)

    async def roll_config(request):
        async def operation():
            body = await request.json()
            if not body.get("operation_id") or not body.get("adapter_config_ref") or body.get("generation") is None:
                raise ValueError("operation_id, generation and adapter_config_ref are required")
            current = await coordinator.get(request.match_info["runtime_id"])
            if current is None:
                raise LookupError(request.match_info["runtime_id"])
            # Operator control-plane authority comes from the signed service
            # principal; request headers/body cannot self-assert a tenant.
            verify_owner(request, current)
            if any(key in body for key in ("runtime_id", "user_id", "agentee_id")):
                raise ValueError("runtime owner fields are not accepted")
            return response(await coordinator.roll_adapter_config(
                current.runtime_id, body["adapter_config_ref"], body["operation_id"],
                int(body["generation"]),
            ))
        return await invoke(request, operation)

    async def health(request):
        async def operation():
            return web.json_response(_jsonable(await health_service.snapshot()))
        return await invoke(request, operation)

    prefix = "/internal/runtime-manager/v1"
    app.router.add_post(prefix + "/runtimes:provision", provision)
    app.router.add_post(prefix + "/runtimes:ensure", ensure)
    app.router.add_get(prefix + "/runtimes/{runtime_id}", get_runtime)
    app.router.add_post(prefix + "/runtimes/{runtime_id}/bindings:ensure", ensure_binding)
    app.router.add_post(prefix + "/runtimes/{runtime_id}/desired-state", desired)
    app.router.add_post(prefix + "/runtimes/{runtime_id}/input", input_)
    app.router.add_get(prefix + "/runtimes/{runtime_id}/output", output_)
    app.router.add_post(prefix + "/runtimes/{runtime_id}/adapter-config:roll", roll_config)
    app.router.add_get(prefix + "/health", health)
