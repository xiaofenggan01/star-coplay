"""Dynamic Hosted Runtime rollout backed by system_configs."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from modules.config.system_config_client import ConfigError, SystemConfigClient

log = logging.getLogger("runtime_manager.rollout")


@dataclass(frozen=True)
class HostedRolloutSnapshot:
    enabled: bool = False
    mode: str = "allowlist"
    user_ids: frozenset[str] = frozenset()
    emails: frozenset[str] = frozenset()

    def allows(self, user_id: str, email: str | None = None) -> bool:
        if not self.enabled:
            return False
        if self.mode == "all_authenticated":
            return True
        return (
            user_id in self.user_ids
            or bool(email and email.strip().lower() in self.emails)
        )


class HostedRolloutProvider:
    """Shared Gateway/Runtime rollout authority.

    ``SystemConfigClient`` owns the 60-second cache. Invalid or unavailable
    configuration replaces the current snapshot with a closed one, so rollout
    failures always preserve the Legacy path.
    """

    def __init__(self, config_client: SystemConfigClient, *, environment: str) -> None:
        self._config_client = config_client
        self._environment = environment
        self._snapshot = HostedRolloutSnapshot()
        self._admitted_user_ids: set[str] = set()
        self._revision = ""

    @property
    def snapshot(self) -> HostedRolloutSnapshot:
        return self._snapshot

    async def is_allowed(self, user_id: str, email: str | None = None) -> bool:
        await self.refresh()
        allowed = self._snapshot.allows(user_id, email)
        if allowed:
            self._admitted_user_ids.add(user_id)
        else:
            self._admitted_user_ids.discard(user_id)
        return allowed

    def is_allowed_cached(self, user_id: str) -> bool:
        return self._snapshot.allows(user_id) or user_id in self._admitted_user_ids

    async def refresh(self) -> HostedRolloutSnapshot:
        try:
            payload = await self._config_client.get_config(
                "agent_runtime", "hosted_rollout", self._environment,
            )
            if payload.get("value_type") != "json":
                raise ConfigError(
                    "CONFIG_TYPE_MISMATCH",
                    "agent_runtime/hosted_rollout must use value_type=json",
                )
            parsed = payload.get("parsed_value")
            snapshot = self._parse(parsed)
            revision = str(payload.get("updated_at", ""))
        except (ConfigError, TypeError, ValueError) as error:
            self._snapshot = HostedRolloutSnapshot()
            self._admitted_user_ids.clear()
            self._revision = ""
            log.warning(
                "Hosted rollout config unavailable; routing to Legacy",
                extra={
                    "event": "runtime.rollout.fail_closed",
                    "error_code": getattr(error, "code", "invalid_rollout_config"),
                    "outcome": "legacy",
                },
            )
            return self._snapshot

        if revision != self._revision or snapshot != self._snapshot:
            self._admitted_user_ids.clear()
        self._snapshot = snapshot
        self._revision = revision
        return snapshot

    @staticmethod
    def _parse(value: Any) -> HostedRolloutSnapshot:
        if not isinstance(value, dict):
            raise ValueError("hosted_rollout must be a JSON object")
        enabled = value.get("enabled")
        mode = value.get("mode")
        user_ids = value.get("user_ids", [])
        emails = value.get("emails", [])
        if not isinstance(enabled, bool):
            raise ValueError("hosted_rollout.enabled must be boolean")
        if mode not in {"allowlist", "all_authenticated"}:
            raise ValueError("hosted_rollout.mode must be allowlist/all_authenticated")
        if not isinstance(user_ids, list) or not all(isinstance(v, str) for v in user_ids):
            raise ValueError("hosted_rollout.user_ids must be a string array")
        if not isinstance(emails, list) or not all(isinstance(v, str) for v in emails):
            raise ValueError("hosted_rollout.emails must be a string array")
        return HostedRolloutSnapshot(
            enabled=enabled,
            mode=mode,
            user_ids=frozenset(v.strip() for v in user_ids if v.strip()),
            emails=frozenset(v.strip().lower() for v in emails if v.strip()),
        )
