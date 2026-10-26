"""Per-session Agent subprocess runner.

Driven via stdio NDJSON:
  stdin  : one JSON message per line from Gateway (Mod-origin frames)
  stdout : one JSON frame per line back to Gateway (Agent-origin frames)
  stderr : log / diagnostics

Special control frame on stdin: {"type": "__stop__"} -> graceful stop.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from pathlib import Path


def _setup_paths() -> None:
    here = Path(__file__).resolve()
    agent_dir = here.parent
    repo_root = agent_dir.parent
    backend_dir = repo_root / "platform" / "backend"
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(backend_dir))
    sys.path.insert(0, str(agent_dir))


_setup_paths()

from text_sanitize import sanitize_for_utf8_json

_NDJSON_OUT = sys.stdout
sys.stdout = sys.stderr

log = logging.getLogger("agent.session_runner")

_out_lock = threading.Lock()


def _emit(frame: dict) -> None:
    try:
        data = (json.dumps(sanitize_for_utf8_json(frame), ensure_ascii=False) + "\n").encode(
            "utf-8", errors="replace"
        )
    except Exception:
        log.exception("emit serialize failed")
        return
    with _out_lock:
        try:
            buf = getattr(_NDJSON_OUT, "buffer", None)
            if buf is not None:
                buf.write(data)
                buf.flush()
            else:
                _NDJSON_OUT.write(data.decode("utf-8", errors="replace"))
                _NDJSON_OUT.flush()
        except Exception:
            log.exception("emit write failed")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--agent-instance-id", required=True)
    p.add_argument("--platform-token", default="")
    p.add_argument("--user-id", default="")
    p.add_argument("--target-player-id", default="")
    p.add_argument("--platform-url", default="")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        from common.observability import configure_json_logging
        from common.settings.env import resolve_log_rotation_settings

        log_max_bytes, log_backup_count = resolve_log_rotation_settings()

        configure_json_logging(
            component="agent-runtime",
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            log_dir=os.environ.get("STARCOPLAY_LOG_DIR", "/var/log/starcoplay"),
            agent_instance_id=args.agent_instance_id,
            user_id=args.user_id,
            target_player_id=args.target_player_id,
            stderr=False,
            max_bytes=log_max_bytes,
            backup_count=log_backup_count,
        )
    except ValueError:
        # Invalid bounded logging settings are startup configuration errors.
        raise
    except Exception:
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO"),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stderr,
        )

    from bridge.ws_bridge import WSBridge
    from main import APP_DIR, build_agent, load_config
    from memory.user_layout import resolve_user_memory_root

    tpid: int | None = None
    if args.target_player_id:
        try: tpid = int(args.target_player_id)
        except ValueError: tpid = None

    legacy_transport = os.environ.get("STARCOPLAY_LEGACY_BRIDGE_TRANSPORT", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    bridge = WSBridge(
        sender=_emit,
        target_player_id=tpid,
        protocol_transport=not legacy_transport,
        protocol_session_id=args.agent_instance_id,
    )

    config = load_config()

    # ── Agentee persona env vars (injected by backend) ──────────────────
    agentee_display_name = os.environ.get("STARCOPLAY_AGENTEE_DISPLAY_NAME", "").strip() or None
    agentee_user_call_name = os.environ.get("STARCOPLAY_AGENTEE_USER_CALL_NAME", "").strip() or None
    agentee_persona_card = os.environ.get("STARCOPLAY_AGENTEE_PERSONA_CARD") or None

    if agentee_display_name or agentee_user_call_name or agentee_persona_card:
        sl_cfg = config.setdefault("slow_loop", {})
        if agentee_display_name:
            sl_cfg["agent_name"] = agentee_display_name
            sl_cfg["agentee_display_name"] = agentee_display_name
        # Don't override player_name with user_call_name — player_name should
        # come from the game session (the actual player character name).
        # The persona_card already instructs the agent on how to address the user.
        if agentee_persona_card:
            sl_cfg["agentee_persona_card"] = agentee_persona_card
        log.info(
            "agentee persona loaded: display_name=%s, user_call_name=%s, card_len=%d",
            agentee_display_name or "(none)",
            agentee_user_call_name or "(none)",
            len(agentee_persona_card) if agentee_persona_card else 0,
        )

    raw_user_id = (args.user_id or "").strip()
    if raw_user_id:
        mem_cfg = config.setdefault("memory", {})
        resolved = resolve_user_memory_root(APP_DIR, mem_cfg, raw_user_id)
        mem_cfg["memory_root"] = str(resolved)
    tel = config.setdefault("telemetry", {})
    if args.platform_token:
        tel["platform_token"] = args.platform_token
    tel.setdefault("enabled", True)
    dsn = os.environ.get("DATABASE_URL", "").strip()
    platform_url = (args.platform_url or os.environ.get("STARCOPLAY_PLATFORM_URL") or "").rstrip("/")
    if dsn:
        tel.setdefault("backend", "internal")
        tel.setdefault("dsn", dsn)
    elif platform_url:
        tel.setdefault("backend", "http")
        tel.setdefault("events_url", f"{platform_url}/api/v1/events")

    llm_overrides: dict[str, str] = {}
    relay_base = os.environ.get("LLM_RELAY_BASE_URL", "").rstrip("/")
    relay_key = os.environ.get("LLM_RELAY_API_KEY", "").strip()
    if not relay_base or not relay_key:
        log.error(
            "LLM relay is not configured: LLM_RELAY_BASE_URL and LLM_RELAY_API_KEY are required"
        )
        return 2
    llm_overrides["api_base"] = relay_base if "/v1" in relay_base else f"{relay_base}/api/v1"
    llm_overrides["api_key"] = relay_key

    try:
        handle = build_agent(bridge, config, llm_overrides=llm_overrides, start_telemetry=False)
    except Exception:
        log.exception("build_agent failed")
        return 2

    log.info("session_runner started: agent_instance_id=%s", args.agent_instance_id)

    stop_evt = threading.Event()

    def _reader() -> None:
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    log.warning("bad JSON from stdin: %s", line[:120])
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "__stop__":
                    break
                if msg.get("type") == "external_text_input":
                    try:
                        handle.submit_external_text(
                            text=str(msg.get("text") or ""),
                            source=str(msg.get("source") or "external"),
                            input_id=str(msg.get("input_id") or "") or None,
                            route_key=str(msg.get("route_key") or "") or None,
                            sender_label=str(msg.get("sender_label") or "") or None,
                        )
                    except Exception:
                        log.exception("handle external_text_input failed")
                    continue
                try:
                    bridge.handle_message(msg)
                except Exception:
                    log.exception("bridge.handle_message failed")
        finally:
            stop_evt.set()

    rt = threading.Thread(target=_reader, name="stdin-reader", daemon=True)
    rt.start()

    try:
        stop_evt.wait()
    except KeyboardInterrupt:
        pass

    try:
        handle.stop()
    except Exception:
        log.exception("handle.stop failed")
    log.info("session_runner exiting: agent_instance_id=%s", args.agent_instance_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
