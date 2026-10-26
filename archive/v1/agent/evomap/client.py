from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from agent.debug.runtime import sanitize_value


def _agent() -> logging.Logger:
    return logging.getLogger("agent")

HUB_URL = "https://evomap.ai"
PROTOCOL = "gep-a2a"
PROTOCOL_VERSION = "1.0.0"


def canonical_json(obj: Any) -> str:
    if isinstance(obj, dict):
        return "{" + ",".join(
            json.dumps(k) + ":" + canonical_json(v)
            for k, v in sorted(obj.items())
        ) + "}"
    if isinstance(obj, list):
        return "[" + ",".join(canonical_json(i) for i in obj) + "]"
    return json.dumps(obj)


def compute_asset_id(asset: dict[str, Any]) -> str:
    stripped = {k: v for k, v in asset.items() if k != "asset_id"}
    return "sha256:" + hashlib.sha256(canonical_json(stripped).encode()).hexdigest()


def _msg_id() -> str:
    return f"msg_{int(time.time())}_{secrets.token_hex(4)}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class GEPClient:
    def __init__(self, store_dir: Path, model: str = "unknown") -> None:
        self.store_dir = store_dir
        self.model = model
        self._node_path = store_dir / "node.json"
        self._node_id: str | None = None
        self._node_secret: str | None = None
        self._load_credentials()

    @property
    def registered(self) -> bool:
        return self._node_id is not None and self._node_secret is not None

    @property
    def node_id(self) -> str | None:
        return self._node_id

    def _load_credentials(self) -> None:
        if not self._node_path.is_file():
            return
        try:
            data = json.loads(self._node_path.read_text(encoding="utf-8"))
            self._node_id = data.get("node_id")
            self._node_secret = data.get("node_secret")
        except (OSError, json.JSONDecodeError):
            pass

    def _save_credentials(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._node_path.write_text(
            json.dumps({"node_id": self._node_id, "node_secret": self._node_secret},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _envelope(self, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        env: dict[str, Any] = {
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "message_type": message_type,
            "message_id": _msg_id(),
            "timestamp": _timestamp(),
            "payload": payload,
        }
        if self._node_id:
            env["sender_id"] = self._node_id
        return env

    def _post(self, path: str, body: dict[str, Any], auth: bool = True, _retries: int = 2) -> dict[str, Any]:
        url = HUB_URL + path
        data = json.dumps(body, ensure_ascii=False).encode()
        for attempt in range(_retries + 1):
            req = Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            if auth and self._node_secret:
                req.add_header("Authorization", f"Bearer {self._node_secret}")
            try:
                with urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode())
            except HTTPError as e:
                body_text = ""
                try:
                    body_text = e.read().decode()
                except Exception:
                    pass
                if e.code == 503 and attempt < _retries:
                    wait_ms = 3000
                    try:
                        detail = json.loads(body_text)
                        wait_ms = int(detail.get("retry_after_ms", 3000))
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass
                    _agent().info("evomap %s 503 retry in %dms (attempt %d)", path, wait_ms, attempt + 1)
                    time.sleep(wait_ms / 1000)
                    continue
                _agent().warning("evomap %s %s: %s %s", path, e.code, e.reason, body_text)
                return {"error": True, "status": e.code, "detail": body_text}
            except (URLError, OSError) as e:
                _agent().warning("evomap %s network error: %s", path, e)
                return {"error": True, "detail": str(e)}
        return {"error": True, "detail": "max retries exceeded"}

    def _post_rest(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = HUB_URL + path
        data = json.dumps(body, ensure_ascii=False).encode()
        req = Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if self._node_secret:
            req.add_header("Authorization", f"Bearer {self._node_secret}")
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode()
            except Exception:
                pass
            _agent().warning("evomap %s %s: %s %s", path, e.code, e.reason, body_text)
            return {"error": True, "status": e.code, "detail": body_text}
        except (URLError, OSError) as e:
            _agent().warning("evomap %s network error: %s", path, e)
            return {"error": True, "detail": str(e)}

    def hello(self) -> dict[str, Any]:
        import platform
        payload: dict[str, Any] = {
            "capabilities": {"domain": "stardew_valley", "type": "game_agent"},
            "model": self.model,
            "env_fingerprint": {"platform": platform.system().lower(), "arch": platform.machine()},
        }
        envelope = self._envelope("hello", payload)
        resp = self._post("/a2a/hello", envelope, auth=False)
        p = resp.get("payload") or resp
        node_id = p.get("your_node_id")
        node_secret = p.get("node_secret")
        if node_id and node_secret:
            self._node_id = node_id
            self._node_secret = node_secret
            self._save_credentials()
            safe = {k: p.get(k) for k in ("status", "your_node_id", "claim_url", "claim_code", "hub_node_id")}
            safe = {k: v for k, v in safe.items() if v is not None}
            _agent().info(
                json.dumps({"evomap_event": "hello_ok", **safe}, ensure_ascii=False)
            )
            print("evomap: registered", node_id)
            if p.get("claim_url"):
                print("evomap: claim_url", p.get("claim_url"))
            if p.get("claim_code"):
                print("evomap: claim_code", p.get("claim_code"))
        else:
            _agent().info(
                json.dumps(
                    {"evomap_event": "hello_fail", "response": sanitize_value(resp)},
                    ensure_ascii=False,
                )
            )
            print("evomap: hello failed — see agent/debug/store/agent.log")
        return resp

    def heartbeat(self) -> dict[str, Any]:
        if not self._node_id:
            return {"error": True, "detail": "not registered"}
        return self._post_rest("/a2a/heartbeat", {"node_id": self._node_id})

    def publish(self, assets: list[dict[str, Any]]) -> dict[str, Any]:
        envelope = self._envelope("publish", {"assets": assets})
        return self._post("/a2a/publish", envelope)

    def fetch(self, signals: list[str] | None = None, asset_type: str = "Capsule") -> dict[str, Any]:
        payload: dict[str, Any] = {"asset_type": asset_type}
        if signals:
            payload["signals"] = signals
        envelope = self._envelope("fetch", payload)
        return self._post("/a2a/fetch", envelope)
