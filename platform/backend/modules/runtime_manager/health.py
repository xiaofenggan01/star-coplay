"""Read-only raw health composition for the internal Runtime Manager API."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from common.observability import logging_sink_state_snapshot
from modules.runtime_manager.domain import RuntimeState

_WORKER_KINDS = (
    "idle_sweep", "reconcile", "authority", "gateway_outbox", "binding_expiry",
)
_WORKER_FIELDS = (
    "last_success_at", "last_error_at", "last_error_code",
    "last_duration_ms", "last_batch_size",
)


class RuntimeManagerHealthService:
    """Composes owner snapshots without invoking recovery or active probes."""

    def __init__(self, coordinator: Any, *, workers: Any = None,
                 authority_outbox: Any = None, gateway_outbox: Any = None,
                 audit_sink: Any = None) -> None:
        self._coordinator = coordinator
        self._workers = workers
        self._authority_outbox = authority_outbox
        self._gateway_outbox = gateway_outbox
        self._audit_sink = audit_sink
        self._hosted_gateway_reader = None
        self._hosted_input_reader = None
        self._hosted_mcp_reader = None

    def bind_hosted_diagnostics(self, *, gateway_reader, input_reader,
                                mcp_reader=None) -> None:
        if self._hosted_gateway_reader is not None or self._hosted_input_reader is not None:
            raise RuntimeError("hosted diagnostics already bound")
        self._hosted_gateway_reader = gateway_reader
        self._hosted_input_reader = input_reader
        self._hosted_mcp_reader = mcp_reader

    async def snapshot(self) -> Mapping[str, Any]:
        # ``list_active`` is the canonical product boundary and counts active
        # generation rows just like admission capacity. The production unique
        # index normally permits one non-stopped generation per runtime_id; if
        # that invariant is broken, health deliberately exposes both rows.
        active = tuple(await self._coordinator.repository.list_active())
        owned_reader = getattr(self._coordinator, "owned_handle_keys_snapshot", None)
        owned = owned_reader() if owned_reader is not None else frozenset()
        legitimate_pending = {
            (record.runtime_id, record.generation)
            for record in active
            if record.actual_state is RuntimeState.PROVISIONING and record.started_at is None
        }
        active_keys = {(record.runtime_id, record.generation) for record in active}
        quarantined_reader = getattr(
            self._coordinator.repository, "diagnostic_quarantined_count", None
        )
        quarantined = int(await quarantined_reader()) if quarantined_reader else 0

        worker_source = (
            self._workers.worker_state_snapshot() if self._workers is not None else {}
        )
        workers = {
            kind: {field: worker_source.get(kind, {}).get(field) for field in _WORKER_FIELDS}
            for kind in _WORKER_KINDS
        }
        outboxes = {
            "authority": await self._outbox_snapshot(self._authority_outbox),
            "gateway": await self._outbox_snapshot(self._gateway_outbox),
        }
        sink_source = logging_sink_state_snapshot()
        audit = (
            self._audit_sink.health_state_snapshot()
            if self._audit_sink is not None else _empty_sink(False)
        )
        sinks = {
            "operational": _sink_shape(sink_source.get("operational", {})),
            "metric": _sink_shape(sink_source.get("metric", {})),
            "audit": _sink_shape(audit),
        }
        provider_reader = getattr(self._coordinator, "provider_state_snapshot", None)
        provider_source = provider_reader() if provider_reader is not None else {}
        payload = {
            "schema_version": 2 if self._hosted_gateway_reader is not None else 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "ok": True,
            "component": "runtime_manager",
            "runtime": {
                "active_count": len(active),
                "owned_handle_count": len(owned),
                "orphan_candidate_count": len(active_keys - owned - legitimate_pending),
                "quarantined_count": quarantined,
            },
            "workers": workers,
            "outboxes": outboxes,
            "provider": {
                "last_failure_at": provider_source.get("last_failure_at"),
                "last_failure_code": provider_source.get("last_failure_code"),
                "adapter_kind": provider_source.get("adapter_kind"),
            },
            "sinks": sinks,
        }
        if self._hosted_gateway_reader is not None and self._hosted_input_reader is not None:
            payload["hosted"] = {
                "gateway": self._hosted_gateway_reader(),
                "input": self._hosted_input_reader(),
            }
            if self._hosted_mcp_reader is not None:
                payload["hosted"]["mcp"] = self._hosted_mcp_reader()
        return payload

    @staticmethod
    async def _outbox_snapshot(repository: Any) -> Mapping[str, Any]:
        if repository is None:
            return {
                "pending_count": None, "claimed_count": None,
                "oldest_pending_age_seconds": None, "available": False,
                "error_code": "diagnostic_stats_unavailable",
            }
        try:
            stats = await repository.diagnostic_stats()
            if not isinstance(stats, Mapping):
                raise TypeError("outbox diagnostics must be a mapping")
            if not {"pending", "claimed", "oldest_age_seconds"}.issubset(stats):
                raise KeyError("incomplete outbox diagnostics")
            pending = stats["pending"]
            claimed = stats["claimed"]
            oldest = stats["oldest_age_seconds"]
            if (
                not isinstance(pending, int) or isinstance(pending, bool) or pending < 0
                or not isinstance(claimed, int) or isinstance(claimed, bool) or claimed < 0
                or not isinstance(oldest, (int, float)) or isinstance(oldest, bool)
                or not math.isfinite(oldest) or oldest < 0
            ):
                raise ValueError("invalid outbox diagnostics")
            result = {
                "pending_count": pending,
                "claimed_count": claimed,
                "oldest_pending_age_seconds": float(oldest),
                "available": True,
                "error_code": None,
            }
            return result
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            return {
                "pending_count": None, "claimed_count": None,
                "oldest_pending_age_seconds": None, "available": False,
                "error_code": "diagnostic_stats_unavailable",
            }
        except Exception:
            return {
                "pending_count": None, "claimed_count": None,
                "oldest_pending_age_seconds": None, "available": False,
                "error_code": "diagnostic_stats_unavailable",
            }


def _empty_sink(configured: bool) -> Mapping[str, Any]:
    return {
        "configured": configured, "writable": None,
        "last_success_at": None, "last_error_code": None,
    }


def _sink_shape(source: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "configured": bool(source.get("configured", False)),
        "writable": source.get("writable"),
        "last_success_at": source.get("last_success_at"),
        "last_error_code": source.get("last_error_code"),
    }
