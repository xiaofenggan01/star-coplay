"""Immutable Hosted Runtime admission policy.

The HTTP boundary owns user rollout admission while the production repository
owns atomic active-Runtime capacity admission.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

from modules.runtime_manager.domain import HostedRuntimeNotAllowed
from common.observability import emit_metric

log = logging.getLogger("runtime_manager.admission")


@dataclass(frozen=True)
class RuntimeAdmissionPolicy:
    allowed_user_ids: frozenset[str] = frozenset()
    allow_all_authenticated: bool = False
    rollout_provider: object | None = None
    max_active_global: int = 20
    max_active_per_user: int = 1

    def require_allowed(self, user_id: str) -> None:
        if self.rollout_provider is not None:
            if self.rollout_provider.is_allowed_cached(user_id):
                return
            self._reject()
            return
        if self.allow_all_authenticated:
            return
        if user_id not in self.allowed_user_ids:
            self._reject()

    @staticmethod
    def _reject() -> None:
        log.warning(
            "Hosted Runtime rejected by rollout policy",
            extra={"event": "runtime.admission.rejected", "policy_kind": "allowlist",
                   "error_code": "hosted_runtime_not_allowed", "outcome": "rejected"},
        )
        emit_metric(
            "runtime_admission_total", "counter", 1, outcome="rejected",
            policy_kind="allowlist", error_code="hosted_runtime_not_allowed",
        )
        raise HostedRuntimeNotAllowed("hosted runtime is not available")
