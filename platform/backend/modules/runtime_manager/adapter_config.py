"""Tenant-bound immutable adapter configuration snapshots."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

# Snapshot provider settings deliberately cover only numeric runtime deadlines.
# Claude ``--settings`` remains an adapter-local test/auth hook and is not part
# of this tenant-controlled contract; no caller-supplied path is accepted here.
_PROVIDER_SETTING_LIMITS = {
    "ready_deadline_seconds": (1.0, 300.0),
    "turn_deadline_seconds": (1.0, 3600.0),
}


def _decode_json_value(raw: Any, field: str) -> Any:
    """Decode the two asyncpg JSONB codec shapes without coercion.

    Production pools may return decoded Python containers or JSON text,
    depending on codec registration.  Anything else fails closed.
    """
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"adapter_{field}_json_invalid") from exc
    return raw


def _json_object(raw: Any, field: str) -> dict[str, Any]:
    value = _decode_json_value(raw, field)
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"adapter_{field}_invalid")
    return dict(value)


def _allowed_tools(raw: Any) -> tuple[str, ...]:
    value = _decode_json_value(raw, "allowed_tools")
    if not isinstance(value, (list, tuple)):
        raise ValueError("adapter_allowed_tools_invalid")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError("adapter_allowed_tools_invalid")
    return tuple(value)


def _provider_settings(raw: Mapping[str, Any]) -> dict[str, float]:
    unknown = set(raw) - set(_PROVIDER_SETTING_LIMITS)
    if unknown:
        raise ValueError("adapter_provider_setting_not_allowed")
    result = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("adapter_provider_setting_invalid")
        low, high = _PROVIDER_SETTING_LIMITS[key]
        if not low <= float(value) <= high:
            raise ValueError("adapter_provider_setting_invalid")
        result[key] = float(value)
    return result


@dataclass(frozen=True)
class AdapterConfigSnapshot:
    runtime_id: str
    user_id: str
    agentee_id: str
    revision: str
    content_hash: str
    provider: str
    model: str
    permission_mode: str
    allowed_tools: tuple[str, ...]
    provider_settings: Mapping[str, Any]

    def metadata(self) -> dict[str, Any]:
        return {
            "adapter_config_revision": self.revision,
            "adapter_config_hash": self.content_hash,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "tools": ",".join(self.allowed_tools),
            **dict(self.provider_settings),
        }

    def document(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id, "user_id": self.user_id,
            "agentee_id": self.agentee_id, "revision": self.revision,
            "content_hash": self.content_hash, "provider": self.provider,
            "model": self.model, "permission_mode": self.permission_mode,
            "allowed_tools": list(self.allowed_tools),
            "provider_settings": dict(self.provider_settings),
        }


class AdapterConfigResolver(Protocol):
    async def resolve(self, reference: str, runtime_id: str, user_id: str,
                      agentee_id: str, provider: str) -> AdapterConfigSnapshot: ...


def _snapshot(runtime_id, user_id, agentee_id, revision, provider, model,
              permission_mode, allowed_tools, provider_settings):
    allowed_tools = _allowed_tools(allowed_tools)
    provider_settings = _json_object(provider_settings, "provider_settings")
    payload = {
        "runtime_id": runtime_id, "user_id": user_id, "agentee_id": agentee_id,
        "revision": revision, "provider": provider, "model": model,
        "permission_mode": permission_mode, "allowed_tools": sorted(set(allowed_tools)),
        "provider_settings": _provider_settings(provider_settings),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return AdapterConfigSnapshot(
        runtime_id, user_id, agentee_id, str(revision), digest, provider, model,
        permission_mode, tuple(payload["allowed_tools"]), dict(payload["provider_settings"]),
    )


class LegacyAdapterConfigResolver:
    """Narrow compatibility fallback; arbitrary opaque references are rejected."""
    async def resolve(self, reference, runtime_id, user_id, agentee_id, provider):
        if reference != "adapter/default":
            raise ValueError("adapter_config_ref_not_found")
        if provider != "claude" and provider != "fake":
            raise ValueError("adapter_provider_mismatch")
        return _snapshot(runtime_id, user_id, agentee_id, "legacy-default-v1", provider,
                         "claude-sonnet-5", "default", (), {})


class DbAdapterConfigResolver:
    def __init__(self, pool) -> None: self.pool = pool

    async def resolve(self, reference, runtime_id, user_id, agentee_id, provider):
        if reference == "adapter/default":
            row = await self.pool.fetchrow(
                "SELECT config,updated_at FROM public.profiles WHERE user_id=$1", user_id
            )
            raw_config = row["config"] if row and row["config"] is not None else {}
            raw = _json_object(raw_config, "profile_config")
            config = _json_object(raw.get("runtime_adapter", {}), "runtime_adapter")
            if config.get("agentee_id") not in (None, agentee_id):
                raise ValueError("adapter_config_owner_mismatch")
            stable_revision = config.get("revision")
            if not stable_revision:
                stable_revision = ("profile-config:" + hashlib.sha256(
                    json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()) if config else "legacy-default-v1"
            return _snapshot(
                runtime_id, user_id, agentee_id,
                stable_revision, provider,
                str(config.get("model", "claude-sonnet-5")),
                str(config.get("permission_mode", "default")),
                config.get("allowed_tools", []), config.get("provider_settings", {}),
            )
        parts = reference.split("/")
        if len(parts) != 3 or parts[0] != "runtime-config":
            raise ValueError("invalid_adapter_config_ref")
        row = await self.pool.fetchrow(
            """SELECT * FROM public.runtime_adapter_configs
               WHERE config_id=$1 AND revision=$2""", parts[1], parts[2]
        )
        if row is None:
            raise ValueError("adapter_config_ref_not_found")
        if (row["runtime_id"], row["user_id"], row["agentee_id"]) != (runtime_id, user_id, agentee_id):
            raise ValueError("adapter_config_owner_mismatch")
        if row["provider"] != provider:
            raise ValueError("adapter_provider_mismatch")
        return _snapshot(runtime_id, user_id, agentee_id, row["revision"], provider,
                         row["model"], row["permission_mode"], row["allowed_tools"],
                         row["provider_settings"])
