from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, TYPE_CHECKING

from aiohttp import web

from common.auth.token_authenticator import TokenAuthenticator
from common.observability import bind_log_context, new_log_id, reset_log_context
from common.settings.env import resolve_config_environment
from modules.gateway.server import Gateway, Session
from modules.runtime.instance_registry import InstanceState
from modules.runtime_manager.contracts import RuntimeInput
from modules.runtime_manager.domain import Conflict, ResourceExhausted

if TYPE_CHECKING:
    from modules.ai_relay_account.service import AIRelayAccountService
    from modules.agentee.persona_service import PersonaInternalService
    from modules.llm.system_router import SystemLLMRouter
    from modules.runtime.agent_worker import AgentSessionWorker

log = logging.getLogger("platform_ws.http_api")


@dataclass
class HttpApiConfig:
    host: str = "0.0.0.0"
    port: int = 8766
    allow_origin: str = "*"

    @classmethod
    def from_env(cls) -> "HttpApiConfig":
        return cls(
            host=os.environ.get("WS_HTTP_HOST", "0.0.0.0"),
            port=int(os.environ.get("WS_HTTP_PORT", "8766")),
            allow_origin=os.environ.get("WS_HTTP_ALLOW_ORIGIN", "*"),
        )


class HttpApiServer:
    def __init__(
        self,
        gateway: Gateway,
        authenticator: TokenAuthenticator,
        config: HttpApiConfig,
        db_pool: Any = None,
        agent_worker: Optional["AgentSessionWorker"] = None,
        ai_relay_account_service: Optional["AIRelayAccountService"] = None,
        persona_service: Optional["PersonaInternalService"] = None,
        system_llm_router: Optional["SystemLLMRouter"] = None,
        internal_api_token: str = "",
        internal_config_token: str = "",
        config_environment: str = "prod",
        runtime_manager: Any = None,
        runtime_manager_enabled: bool = False,
        hosted_mcp_endpoint: Any = None,
        runtime_control_claims_secret: str = "",
        runtime_control_services: Optional[Mapping[str, Mapping[str, object]]] = None,
        runtime_admission_policy: Any = None,
        runtime_manager_health: Any = None,
        runtime_llm_token_signer: Any = None,
    ) -> None:
        self._gateway = gateway
        self._auth = authenticator
        self._config = config
        self._db_pool = db_pool
        self._agent_worker = agent_worker
        self._ai_relay_account_service = ai_relay_account_service
        self._persona_service = persona_service
        self._system_llm_router = system_llm_router
        self._internal_api_token = internal_api_token or internal_config_token
        self._config_environment = resolve_config_environment(config_environment)
        self._runtime_manager = runtime_manager
        self._runtime_manager_enabled = runtime_manager_enabled
        self._hosted_mcp_endpoint = hosted_mcp_endpoint
        self._runtime_control_claims_secret = runtime_control_claims_secret
        self._runtime_control_services = dict(runtime_control_services or {})
        self._runtime_admission_policy = runtime_admission_policy
        self._runtime_manager_health = runtime_manager_health
        self._runtime_llm_token_signer = runtime_llm_token_signer
        self._config_client: Any = None
        self._runner: Optional[web.AppRunner] = None
        self._agent_log_path = os.environ.get("STARCOPLAY_AGENT_LOG") or ""

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/v1/sessions/current", self._get_current_session)
        app.router.add_get("/api/v1/sessions/{id}/stream", self._stream_session)
        app.router.add_get("/api/v1/agent/logs/stream", self._stream_agent_logs)
        app.router.add_post("/api/v1/admin/instances/{id}/kick", self._admin_kick_instance)
        app.router.add_get("/api/v1/admin/gateway/status", self._admin_gateway_status)
        app.router.add_post("/api/v1/admin/gateway/maintenance", self._admin_set_maintenance)
        app.router.add_post("/api/v1/admin/external-text/roadshow", self._admin_roadshow_external_text)
        # System LLM relay backed by NewAPI.
        app.router.add_post("/api/v1/system/chat/completions", self._system_llm_openai_compat)
        # Health check (no auth, no version prefix)
        app.router.add_get("/api/ping", self._ping)
        # Internal APIs (INTERNAL_API_TOKEN protected)
        app.router.add_get("/internal/configs/{domain}/{config_key}", self._internal_get_config)
        app.router.add_post("/internal/persona/parse", self._internal_persona_parse)
        app.router.add_post("/internal/persona/compile", self._internal_persona_compile)
        app.router.add_post("/internal/llm/chat/completions", self._internal_llm_chat_completions)
        # ECS nginx currently preserves the /api prefix when proxying to this service.
        # Keep both forms so Vercel can use PLATFORM_BACKEND_API_URL=http://host/api
        # without requiring a risky nginx rewrite change.
        app.router.add_get("/api/internal/configs/{domain}/{config_key}", self._internal_get_config)
        app.router.add_post("/api/internal/persona/parse", self._internal_persona_parse)
        app.router.add_post("/api/internal/persona/compile", self._internal_persona_compile)
        app.router.add_post("/api/internal/llm/chat/completions", self._internal_llm_chat_completions)
        app.router.add_get("/internal/admin/dashboard", self._internal_admin_dashboard)
        app.router.add_get("/api/internal/admin/dashboard", self._internal_admin_dashboard)
        from modules.ai_relay_account.routes import setup_ai_relay_account_routes
        setup_ai_relay_account_routes(
            app,
            self._ai_relay_account_service,
            self._check_internal_api_token,
        )
        if self._runtime_manager_enabled:
            if self._runtime_manager is None:
                raise RuntimeError("Runtime Manager is enabled but not composed")
            from modules.http_api.runtime_manager_routes import setup_runtime_manager_routes
            setup_runtime_manager_routes(
                app, self._runtime_manager, self._check_runtime_manager_token,
                self._runtime_admission_policy,
                self._runtime_manager_health,
            )
            if self._hosted_mcp_endpoint is None:
                raise RuntimeError("Runtime Manager requires Hosted MCP endpoint")
            app.router.add_get("/mcp/hosted/ws", self._hosted_mcp_endpoint.handle)
            app.router.add_post(
                "/api/v1/hosted-runtimes/{runtime_id}/inputs",
                self._hosted_runtime_input,
            )
            app.router.add_get(
                "/api/v1/hosted-runtimes/{runtime_id}/output",
                self._hosted_runtime_output,
            )
            app.router.add_post(
                "/internal/runtime-llm/v1/messages",
                self._runtime_llm_anthropic_messages,
            )
        app.router.add_route("OPTIONS", "/{tail:.*}", self._cors_preflight)
        app.middlewares.append(self._request_logging_middleware)
        app.middlewares.append(self._cors_middleware)
        return app

    async def start(self) -> None:
        app = self.build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._config.host, self._config.port)
        await site.start()
        log.info("HTTP API listening on %s:%d", self._config.host, self._config.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        resp: web.StreamResponse = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = self._config.allow_origin
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return resp

    @web.middleware
    async def _request_logging_middleware(self, request: web.Request, handler):
        request_id = (
            request.headers.get("x-request-id")
            or request.headers.get("x-correlation-id")
            or f"req_{uuid.uuid4().hex[:12]}"
        )
        request["request_id"] = request_id
        log_id = new_log_id()
        request["log_id"] = log_id
        token = bind_log_context(request_id=request_id, log_id=log_id)
        started_at = time.monotonic()
        try:
            try:
                resp: web.StreamResponse = await handler(request)
            except web.HTTPException as exc:
                latency_ms = (time.monotonic() - started_at) * 1000
                level = logging.ERROR if exc.status >= 500 else logging.WARNING
                log.log(
                    level,
                    "http request rejected method=%s path=%s status=%s latency_ms=%.1f",
                    request.method, request.path, exc.status, latency_ms,
                    extra={
                        "event": "gateway.http.request_rejected", "request_id": request_id,
                        "log_id": log_id, "error_code": f"http_{exc.status}",
                        "http_method": request.method, "http_path": request.path,
                        "http_status": exc.status, "latency_ms": round(latency_ms, 1),
                    },
                )
                exc.headers["x-request-id"] = request_id
                exc.headers["x-log-id"] = log_id
                raise
            except Exception:
                latency_ms = (time.monotonic() - started_at) * 1000
                log.exception(
                    "http request failed method=%s path=%s latency_ms=%.1f",
                    request.method, request.path, latency_ms,
                    extra={
                        "event": "gateway.http.request_failed", "request_id": request_id,
                        "log_id": log_id, "error_code": "http_unhandled_exception",
                        "http_method": request.method, "http_path": request.path,
                        "latency_ms": round(latency_ms, 1),
                    },
                )
                raise

            latency_ms = (time.monotonic() - started_at) * 1000
            if not resp.prepared:
                resp.headers["x-request-id"] = request_id
                resp.headers["x-log-id"] = log_id
            if request.path != "/api/ping" or resp.status >= 400:
                level = logging.WARNING if resp.status >= 400 else logging.INFO
                log.log(
                    level,
                    "http request completed method=%s path=%s status=%s latency_ms=%.1f",
                    request.method, request.path, resp.status, latency_ms,
                    extra={
                        "event": "gateway.http.request_completed", "request_id": request_id,
                        "log_id": log_id,
                        "error_code": f"http_{resp.status}" if resp.status >= 400 else "",
                        "http_method": request.method, "http_path": request.path,
                        "http_status": resp.status, "latency_ms": round(latency_ms, 1),
                    },
                )
            return resp
        finally:
            reset_log_context(token)

    async def _ping(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "star-coplay-gateway"})

    async def _cors_preflight(self, request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def _resolve_token(self, request: web.Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        tok = request.query.get("token")
        return tok.strip() if tok else None

    async def _authenticate(self, request: web.Request):
        token = await self._resolve_token(request)
        if not token:
            return None, web.json_response({"error": "missing token"}, status=401)
        res = await self._auth.authenticate(token)
        if not res.ok or not res.user_id:
            status = 403 if res.code == "access_not_approved" else 401
            return None, web.json_response(
                {"error": res.message or "invalid token", "code": res.code},
                status=status,
            )
        return (token, res.user_id), None

    def _check_admin(self, request: web.Request) -> Optional[web.Response]:
        """检查 admin secret，返回 None 表示通过，否则返回错误 Response。"""
        import hmac
        secret = (
            request.headers.get("x-admin-secret", "")
            or request.query.get("admin_secret", "")
        )
        expected = self._gateway._config.admin_secret
        if not expected or not hmac.compare_digest(secret, expected):
            return web.json_response({"error": "unauthorized"}, status=401)
        return None

    async def _get_current_session(self, request: web.Request) -> web.Response:
        creds, err = await self._authenticate(request)
        if err is not None:
            return err
        token, user_id = creds
        inst = await self._gateway.registry.get_by_platform_token(token)
        if inst is None:
            inst = await self._gateway.registry.get_by_user(user_id)
        if inst is None:
            return web.json_response({"online": False})
        session = self._gateway.get_session(inst.agent_instance_id)
        payload = {
            "online": session is not None,
            "agent_instance_id": inst.agent_instance_id,
            "state": inst.state.value,
            "state_changed_at": inst.state_changed_at,
            "connected_at": inst.created_at,
            "target_player_id": inst.target_player_id,
            "farm_session_id": inst.farm_session_id,
            "command_inflight_count": inst.command_inflight_count,
            "last_game_event_at": inst.last_game_event_at,
        }
        runtime_spec = getattr(session, "hosted_runtime_spec", None) if session is not None else None
        if runtime_spec is not None:
            payload["hosted_runtime"] = {
                "runtime_id": runtime_spec.runtime_id,
                "generation": runtime_spec.generation,
                "provider": runtime_spec.adapter_kind,
                "connection_role": session.hosted_connection_role,
            }
        return web.json_response(payload)

    async def _stream_session(self, request: web.Request) -> web.StreamResponse:
        creds, err = await self._authenticate(request)
        if err is not None:
            return err
        token, user_id = creds
        iid = request.match_info["id"]
        inst = await self._gateway.registry.get(iid)
        if inst is None:
            return web.json_response({"error": "not found"}, status=404)
        if inst.user_id != user_id and inst.platform_token != token:
            return web.json_response({"error": "forbidden"}, status=403)
        session = self._gateway.get_session(iid)
        if session is None:
            return web.json_response({"error": "offline"}, status=409)

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        queue = session.subscribe()
        try:
            await resp.write(b": connected\n\n")
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    try:
                        await resp.write(b": ping\n\n")
                    except ConnectionResetError:
                        break
                    continue
                if msg.get("type") == "__closed__":
                    try:
                        await resp.write(b"event: closed\ndata: {}\n\n")
                    except ConnectionResetError:
                        pass
                    break
                payload = json.dumps(msg, ensure_ascii=False)
                try:
                    await resp.write(f"data: {payload}\n\n".encode("utf-8"))
                except ConnectionResetError:
                    break
        finally:
            session.unsubscribe(queue)
        return resp

    async def _stream_agent_logs(self, request: web.Request) -> web.StreamResponse:
        creds, err = await self._authenticate(request)
        if err is not None:
            return err
        token, user_id = creds
        inst = await self._gateway.registry.get_by_platform_token(token)
        if inst is None:
            inst = await self._gateway.registry.get_by_user(user_id)
        if inst is None:
            return web.json_response({"error": "no session"}, status=404)

        log_path = self._resolve_log_path(inst.agent_instance_id)
        if not log_path or not os.path.exists(log_path):
            return web.json_response({"error": "log unavailable"}, status=404)

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, os.SEEK_END)
                start = max(0, f.tell() - 8192)
                f.seek(start)
                tail = f.read()
                if tail:
                    for line in tail.splitlines()[-50:]:
                        payload = json.dumps({"line": line, "ts": time.time()})
                        try:
                            await resp.write(f"data: {payload}\n\n".encode("utf-8"))
                        except ConnectionResetError:
                            return resp
                while True:
                    line = f.readline()
                    if not line:
                        await asyncio.sleep(0.5)
                        try:
                            await resp.write(b": ping\n\n")
                        except ConnectionResetError:
                            return resp
                        continue
                    payload = json.dumps({"line": line.rstrip("\n"), "ts": time.time()})
                    try:
                        await resp.write(f"data: {payload}\n\n".encode("utf-8"))
                    except ConnectionResetError:
                        return resp
        except Exception:
            log.exception("agent log stream failed")
        return resp

    async def _admin_kick_instance(self, request: web.Request) -> web.Response:
        err = self._check_admin(request)
        if err is not None:
            return err
        iid = request.match_info["id"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        reason = body.get("reason", "admin_kick")
        ok = await self._gateway.kick_instance(iid, reason)
        if not ok:
            return web.json_response({"error": "instance not found or already offline"}, status=404)
        return web.json_response({"ok": True, "instance_id": iid})

    async def _admin_gateway_status(self, request: web.Request) -> web.Response:
        err = self._check_admin(request)
        if err is not None:
            return err
        return web.json_response({
            "current_connections": self._gateway.current_connections,
            "max_connections": self._gateway.max_connections,
            "accepting": self._gateway.accepting,
        })

    async def _admin_set_maintenance(self, request: web.Request) -> web.Response:
        err = self._check_admin(request)
        if err is not None:
            return err
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return web.json_response({"error": "enabled must be boolean"}, status=400)
        self._gateway.accepting = not enabled
        return web.json_response({"maintenance": enabled, "accepting": self._gateway.accepting})

    async def _admin_roadshow_external_text(self, request: web.Request) -> web.Response:
        err = self._check_admin(request)
        if err is not None:
            return err
        if self._agent_worker is None:
            return web.json_response({"ok": False, "error": "agent_worker_unavailable"}, status=503)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

        input_id = str(body.get("input_id") or "").strip()
        text = str(body.get("text") or "").strip()
        agent_instance_id = str(body.get("agent_instance_id") or "").strip()
        if not input_id:
            return web.json_response({"ok": False, "error": "input_id_required"}, status=400)
        if not text:
            return web.json_response({"ok": False, "error": "text_required"}, status=400)
        if len(text) > 300:
            return web.json_response({"ok": False, "error": "text_too_long", "max_chars": 300}, status=413)
        if not agent_instance_id:
            return web.json_response({"ok": False, "error": "agent_instance_id_required"}, status=400)

        inst = await self._gateway.registry.get(agent_instance_id)
        if inst is None:
            return web.json_response({"ok": False, "error": "no_session"}, status=404)
        if inst.state != InstanceState.RUNNING:
            return web.json_response(
                {"ok": False, "error": "session_not_running", "state": inst.state.value},
                status=409,
            )
        session = self._gateway.get_session(agent_instance_id)
        if session is None:
            return web.json_response({"ok": False, "error": "no_session"}, status=404)

        msg = {
            "type": "external_text_input",
            "source": "roadshow",
            "input_id": input_id,
            "text": text,
            "received_at": body.get("received_at"),
            "route_key": body.get("route_key"),
        }
        ok = await self._agent_worker.send_to_agent(session, msg)
        if not ok:
            return web.json_response({"ok": False, "error": "delivery_failed"}, status=502)
        log.info(
            "roadshow external text delivered input_id=%s instance=%s route=%s",
            input_id,
            agent_instance_id,
            body.get("route_key"),
        )
        return web.json_response({
            "ok": True,
            "status": "accepted",
            "input_id": input_id,
            "agent_instance_id": agent_instance_id,
        })

    async def _hosted_runtime_input(self, request: web.Request) -> web.Response:
        """Submit an authenticated Platform input to the caller's Hosted Runtime."""
        if not self._runtime_manager_enabled or self._runtime_manager is None:
            return _internal_error_payload(
                "HOSTED_RUNTIME_NOT_CONFIGURED",
                "Hosted Runtime is not configured",
                status=503,
            )
        creds, err = await self._authenticate(request)
        if err is not None:
            return err
        _token, user_id = creds
        try:
            body = await request.json()
        except Exception:
            return _internal_error_payload("INVALID_JSON", "request body must be valid JSON", status=400)
        if not isinstance(body, dict):
            return _internal_error_payload("INVALID_JSON", "request body must be a JSON object", status=400)
        if any(key in body for key in ("runtime_id", "user_id", "agentee_id", "generation")):
            return _internal_error_payload(
                "INVALID_RUNTIME_INPUT",
                "runtime owner and generation fields are not accepted",
                status=400,
            )
        input_id = str(body.get("input_id") or "").strip()
        text = body.get("text")
        if not input_id:
            return _internal_error_payload("INPUT_ID_REQUIRED", "input_id is required", status=400)
        if not isinstance(text, str) or not text.strip():
            return _internal_error_payload("TEXT_REQUIRED", "text must be a non-empty string", status=400)

        runtime_id = request.match_info["runtime_id"]
        try:
            current = await self._runtime_manager.get(runtime_id)
            if current is None or current.spec.user_id != user_id:
                return _internal_error_payload("HOSTED_RUNTIME_NOT_FOUND", "Hosted Runtime not found", status=404)
            if self._runtime_admission_policy is not None:
                self._runtime_admission_policy.require_allowed(user_id)
            receipt = await self._runtime_manager.submit_input_async(
                runtime_id,
                current.generation,
                RuntimeInput(
                    request_id=f"platform:{input_id}",
                    content=text.strip(),
                    trace_context={
                        "source": "platform",
                        "input_id": input_id,
                        "operation_id": f"platform:{input_id}",
                        "request_id": str(request.get("request_id", "")),
                        "log_id": str(request.get("log_id", "")),
                    },
                ),
            )
        except ResourceExhausted as exc:
            return _internal_error_payload("HOSTED_RUNTIME_BUSY", str(exc), status=429)
        except Conflict as exc:
            return _internal_error_payload("HOSTED_RUNTIME_INPUT_CONFLICT", str(exc), status=409)
        except RuntimeError as exc:
            code = str(exc)
            status = 409 if code in {
                "operation_in_progress", "binding_not_active",
                "authority_credential_not_issued", "already_stopping",
            } else 503
            return _internal_error_payload(
                code.upper() if status == 409 else "HOSTED_RUNTIME_UNAVAILABLE",
                code,
                status=status,
            )
        except LookupError:
            return _internal_error_payload("HOSTED_RUNTIME_NOT_FOUND", "Hosted Runtime not found", status=404)

        from common.observability import emit_metric
        emit_metric(
            "hosted_runtime_input_total", "counter", 1,
            input_source="platform", outcome="accepted",
        )
        payload = asdict(receipt)
        payload["input_id"] = input_id
        return web.json_response(payload, status=202)

    async def _hosted_runtime_output(self, request: web.Request) -> web.Response:
        """Read the authenticated caller's sanitised Hosted Runtime transcript."""
        if not self._runtime_manager_enabled or self._runtime_manager is None:
            return _internal_error_payload(
                "HOSTED_RUNTIME_NOT_CONFIGURED", "Hosted Runtime is not configured", status=503,
            )
        creds, err = await self._authenticate(request)
        if err is not None:
            return err
        _token, user_id = creds
        runtime_id = request.match_info["runtime_id"]
        current = await self._runtime_manager.get(runtime_id)
        if current is None or current.spec.user_id != user_id:
            return _internal_error_payload("HOSTED_RUNTIME_NOT_FOUND", "Hosted Runtime not found", status=404)
        try:
            limit = int(request.query.get("limit", "50"))
            from modules.runtime_manager.contracts import OutputQuery
            query = OutputQuery(
                receipt=request.query.get("receipt"),
                since=request.query.get("since"),
                limit=limit,
            )
            page = await self._runtime_manager.read_output(
                runtime_id, current.generation, query,
            )
        except (TypeError, ValueError) as exc:
            return _internal_error_payload("INVALID_OUTPUT_QUERY", str(exc), status=400)
        except LookupError:
            return _internal_error_payload("HOSTED_RUNTIME_NOT_FOUND", "Hosted Runtime not found", status=404)
        except RuntimeError as exc:
            return _internal_error_payload("HOSTED_RUNTIME_OUTPUT_UNAVAILABLE", str(exc), status=409)
        return web.json_response(asdict(page))

    # ------------------------------------------------------------------
    # Internal System LLM Router
    # ------------------------------------------------------------------

    async def _system_llm_openai_compat(self, request: web.Request) -> web.StreamResponse:
        if self._system_llm_router is None:
            return _internal_error_payload(
                "SYSTEM_LLM_ROUTER_NOT_CONFIGURED",
                "System LLM Router is not configured",
                status=503,
            )

        creds, err = await self._authenticate(request)
        if err is not None:
            return err
        _token, user_id = creds

        try:
            body = await request.json()
        except Exception:
            return _internal_error_payload("INVALID_JSON", "request body must be valid JSON", status=400)
        if not isinstance(body, dict):
            return _internal_error_payload("INVALID_JSON", "request body must be a JSON object", status=400)

        model_tier = (
            _optional_body_string(body, "model")
            or await self._get_user_default_model_tier(user_id)
            or os.environ.get("AI_RELAY_AGENT_MODEL_TIER", "balanced")
        )
        stream = bool(body.get("stream", False))

        from modules.llm.system_router import SystemLLMContext, SystemLLMRouterError

        try:
            result = await self._system_llm_router.chat_completions(
                body,
                context=SystemLLMContext(
                    user_id=user_id,
                    agent_session_id=request.headers.get("x-agent-session-id", ""),
                    request_id=request.get("request_id", ""),
                    capability=request.headers.get("x-llm-capability", "agent_openai_compat"),
                    model_tier=model_tier,
                ),
                stream=stream,
            )
        except SystemLLMRouterError as exc:
            return _internal_error_payload(
                exc.code,
                exc.message,
                status=exc.status,
                upstream_status=exc.upstream_status,
            )
        except Exception:
            log.exception("system llm OpenAI-compatible route failed")
            return _internal_error_payload("SYSTEM_LLM_INTERNAL_ERROR", "internal error", status=500)

        return await self._relay_system_llm_result(request, result, stream=stream)

    async def _runtime_llm_anthropic_messages(self, request: web.Request) -> web.StreamResponse:
        if (self._system_llm_router is None or self._runtime_manager is None
                or self._runtime_llm_token_signer is None):
            return _internal_error_payload(
                "RUNTIME_LLM_RELAY_NOT_CONFIGURED", "Runtime LLM Relay is not configured", status=503,
            )
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        token = token or request.headers.get("x-api-key", "").strip()
        try:
            claims = self._runtime_llm_token_signer.verify(token)
        except ValueError:
            return _internal_error_payload("RUNTIME_LLM_UNAUTHORIZED", "invalid runtime credential", status=401)
        current = await self._runtime_manager.get(claims.runtime_id)
        if (current is None or current.generation != claims.generation
                or current.spec.user_id != claims.user_id):
            return _internal_error_payload("RUNTIME_LLM_STALE_GENERATION", "runtime generation is stale", status=401)
        try:
            body = await request.json()
        except Exception:
            return _internal_error_payload("INVALID_JSON", "request body must be valid JSON", status=400)
        if not isinstance(body, dict):
            return _internal_error_payload("INVALID_JSON", "request body must be a JSON object", status=400)
        stream = bool(body.get("stream", False))
        from modules.llm.system_router import SystemLLMContext, SystemLLMRouterError
        try:
            result = await self._system_llm_router.anthropic_messages(
                body,
                context=SystemLLMContext(
                    user_id=claims.user_id,
                    agent_session_id=f"{claims.runtime_id}:{claims.generation}",
                    request_id=str(request.get("request_id", "")),
                    capability="hosted_runtime_anthropic",
                    model_tier=claims.model_tier,
                ),
                stream=stream,
                anthropic_version=request.headers.get("anthropic-version", "2023-06-01"),
                anthropic_beta=request.headers.get("anthropic-beta", ""),
            )
        except SystemLLMRouterError as exc:
            return _internal_error_payload(
                exc.code, exc.message, status=exc.status,
                upstream_status=exc.upstream_status,
            )
        except Exception:
            log.exception("runtime Anthropic relay failed")
            return _internal_error_payload("RUNTIME_LLM_INTERNAL_ERROR", "internal error", status=500)
        return await self._relay_system_llm_result(request, result, stream=stream)

    async def _internal_llm_chat_completions(self, request: web.Request) -> web.StreamResponse:
        err = self._check_internal_api_token(request)
        if err is not None:
            return err
        if self._system_llm_router is None:
            return _internal_error_payload(
                "SYSTEM_LLM_ROUTER_NOT_CONFIGURED",
                "System LLM Router is not configured",
                status=503,
            )

        try:
            body = await request.json()
        except Exception:
            return _internal_error_payload("INVALID_JSON", "request body must be valid JSON", status=400)
        if not isinstance(body, dict):
            return _internal_error_payload("INVALID_JSON", "request body must be a JSON object", status=400)

        user_id = _required_body_string(body, "user_id")
        model_tier = _required_body_string(body, "model_tier")
        if not user_id:
            return _internal_error_payload("INVALID_PARAM", "user_id is required", status=400)
        if not model_tier:
            return _internal_error_payload("INVALID_PARAM", "model_tier is required", status=400)

        agent_session_id = _optional_body_string(body, "agent_session_id")
        if agent_session_id:
            session_err = await self._check_internal_llm_session_scope(agent_session_id, user_id)
            if session_err is not None:
                return session_err

        payload = body.get("payload")
        if not isinstance(payload, dict):
            return _internal_error_payload("INVALID_PARAM", "payload must be an object", status=400)
        stream = bool(body.get("stream", payload.get("stream", False)))

        from modules.llm.system_router import SystemLLMContext, SystemLLMRouterError

        try:
            result = await self._system_llm_router.chat_completions(
                payload,
                context=SystemLLMContext(
                    user_id=user_id,
                    agent_session_id=agent_session_id,
                    request_id=request.get("request_id", ""),
                    capability=_optional_body_string(body, "capability"),
                    model_tier=model_tier,
                ),
                stream=stream,
            )
        except SystemLLMRouterError as exc:
            return _internal_error_payload(
                exc.code,
                exc.message,
                status=exc.status,
                upstream_status=exc.upstream_status,
            )
        except Exception:
            log.exception("internal system llm route failed")
            return _internal_error_payload("SYSTEM_LLM_INTERNAL_ERROR", "internal error", status=500)

        return await self._relay_system_llm_result(request, result, stream=stream)

    async def _internal_persona_parse(self, request: web.Request) -> web.Response:
        err = self._check_internal_api_token(request)
        if err is not None:
            return err
        if self._persona_service is None:
            return web.json_response(
                {"error": {"code": "PERSONA_SERVICE_NOT_CONFIGURED", "message": "Persona service is not configured"}},
                status=503,
            )
        try:
            body = await request.json()
        except Exception:
            return _internal_error_payload("INVALID_JSON", "request body must be valid JSON", status=400)
        if not isinstance(body, dict):
            return _internal_error_payload("INVALID_JSON", "request body must be a JSON object", status=400)

        try:
            payload = await self._persona_service.parse_persona(body)
        except Exception:
            log.exception("internal persona parse failed")
            return _internal_error_payload("PERSONA_INTERNAL_ERROR", "internal error", status=500)
        return web.json_response(payload)

    async def _internal_persona_compile(self, request: web.Request) -> web.Response:
        err = self._check_internal_api_token(request)
        if err is not None:
            return err
        if self._persona_service is None:
            return web.json_response(
                {"error": {"code": "PERSONA_SERVICE_NOT_CONFIGURED", "message": "Persona service is not configured"}},
                status=503,
            )
        try:
            body = await request.json()
        except Exception:
            return _internal_error_payload("INVALID_JSON", "request body must be valid JSON", status=400)
        if not isinstance(body, dict):
            return _internal_error_payload("INVALID_JSON", "request body must be a JSON object", status=400)

        try:
            payload = await self._persona_service.compile_persona(body)
        except ValueError as exc:
            return _internal_error_payload("INVALID_PARAM", str(exc), status=400)
        except Exception:
            log.exception("internal persona compile failed")
            return _internal_error_payload("PERSONA_INTERNAL_ERROR", "internal error", status=500)
        return web.json_response(payload)

    async def _relay_system_llm_result(
        self,
        request: web.Request,
        result: Any,
        *,
        stream: bool,
    ) -> web.StreamResponse:
        if stream:
            upstream = result.response
            out = web.StreamResponse(
                status=upstream.status_code,
                headers={
                    "Content-Type": upstream.headers.get("content-type", "text/event-stream").split(";")[0],
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "X-Resolved-Model": result.resolved_model,
                },
            )
            await out.prepare(request)
            async for chunk in self._system_llm_router.stream_response(upstream):
                try:
                    await out.write(chunk)
                except ConnectionResetError:
                    break
            return out

        upstream = result.response
        try:
            upstream_body = upstream.content
            status = upstream.status_code
            raw_ct = upstream.headers.get("content-type", "application/json")
            content_type = raw_ct.split(";")[0].strip()
            charset = None
            for part in raw_ct.split(";")[1:]:
                part = part.strip()
                if part.lower().startswith("charset="):
                    charset = part.split("=", 1)[1].strip()
        finally:
            await upstream.aclose()
        out = web.Response(
            body=upstream_body,
            status=status,
            content_type=content_type,
            charset=charset,
        )
        out.headers["X-Resolved-Model"] = result.resolved_model
        return out

    async def _check_internal_llm_session_scope(
        self,
        agent_session_id: str,
        user_id: str,
    ) -> web.Response | None:
        inst = await self._gateway.registry.get(agent_session_id)
        if inst is None:
            return _internal_error_payload(
                "AGENT_SESSION_NOT_FOUND",
                "agent session not found",
                status=404,
            )
        if inst.user_id != user_id:
            return _internal_error_payload(
                "AGENT_SESSION_FORBIDDEN",
                "agent session does not belong to user_id",
                status=403,
            )
        return None

    # ------------------------------------------------------------------
    # Internal config read
    # ------------------------------------------------------------------

    def _check_internal_api_token(self, request: web.Request) -> Optional[web.Response]:
        import hmac
        auth = request.headers.get("authorization", "")
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            token = request.headers.get("x-internal-api-token", "")
        if not token:
            token = request.headers.get("x-internal-config-token", "")
        expected = self._internal_api_token
        if not expected or not hmac.compare_digest(token, expected):
            return web.json_response(
                {"error": {"code": "CONFIG_UNAUTHORIZED", "message": "unauthorized"}},
                status=401,
            )
        return None

    async def _check_runtime_manager_token(self, request: web.Request):
        """Verify and atomically consume a Runtime Control service claim."""
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        try:
            from modules.http_api.runtime_control_claims import RuntimeControlClaimsVerifier
            return await RuntimeControlClaimsVerifier(
                self._runtime_control_claims_secret, self._runtime_control_services, self._db_pool).verify(token)
        except Exception:
            return web.json_response(
                {"error":{"code":"RUNTIME_MANAGER_UNAUTHORIZED","message":"valid signed service claims required"}},
                status=401)

    async def _internal_admin_dashboard(self, request: web.Request) -> web.Response:
        err = self._check_internal_api_token(request)
        if err is not None:
            return err
        try:
            days = int(request.query.get("days", "30"))
        except ValueError:
            days = 0
        if days < 1 or days > 365:
            return _internal_error_payload("INVALID_DAYS", "days must be between 1 and 365", status=400)
        from modules.admin_dashboard.metrics import build_dashboard_metrics

        log_root = Path(os.environ.get("STARCOPLAY_LOG_DIR") or "/var/log/starcoplay")
        payload = await asyncio.to_thread(build_dashboard_metrics, log_root, days=days)
        return web.json_response(payload)

    async def _internal_get_config(self, request: web.Request) -> web.Response:
        err = self._check_internal_api_token(request)
        if err is not None:
            return err

        domain = request.match_info["domain"]
        config_key = request.match_info["config_key"]
        environment = request.query.get("env")

        from modules.config.system_config_client import (
            DbConfigRepository,
            ConfigError,
            SystemConfigClient,
        )
        if self._config_client is None:
            repo = DbConfigRepository(self._db_pool)
            self._config_client = SystemConfigClient(
                repository=repo,
                default_environment=self._config_environment,
            )
        try:
            payload = await self._config_client.get_config(domain, config_key, environment)
        except ConfigError as exc:
            if exc.code == "CONFIG_NOT_FOUND":
                status = 404
            elif exc.code in ("CONFIG_DISABLED", "CONFIG_TYPE_MISMATCH"):
                status = 409
            elif exc.code == "CONFIG_INVALID_ENVIRONMENT":
                status = 400
            elif exc.code in ("CONFIG_SERVICE_ERROR", "CONFIG_PARSE_ERROR"):
                status = 500
            else:
                status = 400
            return web.json_response(
                {"error": {"code": exc.code, "message": exc.message}},
                status=status,
            )
        except Exception:
            log.exception("internal config fetch failed domain=%s key=%s env=%s", domain, config_key, environment)
            return web.json_response(
                {"error": {"code": "CONFIG_SERVICE_ERROR", "message": "internal error"}},
                status=500,
            )
        return web.json_response(payload)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_log_path(self, agent_instance_id: str) -> str:
        per_instance = os.path.join(
            os.path.dirname(self._agent_log_path) if self._agent_log_path else "",
            f"agent-{agent_instance_id}.log",
        )
        if self._agent_log_path and os.path.exists(per_instance):
            return per_instance
        return self._agent_log_path

    async def _get_user_default_model_tier(self, user_id: str) -> str:
        if self._db_pool is None or not user_id:
            return ""
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT preferences FROM public.profiles WHERE user_id = $1",
                    user_id,
                )
        except Exception:
            log.warning("failed to read default model tier for user %s", user_id, exc_info=True)
            return ""
        if not row:
            return ""
        preferences = row["preferences"] or {}
        if isinstance(preferences, str):
            try:
                preferences = json.loads(preferences)
            except Exception:
                return ""
        if not isinstance(preferences, dict):
            return ""
        ai_relay = preferences.get("ai_relay") or {}
        if not isinstance(ai_relay, dict):
            return ""
        value = ai_relay.get("default_model_tier")
        return value.strip() if isinstance(value, str) else ""


def _required_body_string(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    return value.strip() if isinstance(value, str) else ""


def _optional_body_string(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    return value.strip() if isinstance(value, str) else ""


def _internal_error_payload(
    code: str,
    message: str,
    *,
    status: int,
    upstream_status: int | None = None,
) -> web.Response:
    error: dict[str, Any] = {"code": code, "message": message}
    if upstream_status is not None:
        error["upstream_status"] = upstream_status
    return web.json_response({"error": error}, status=status)
