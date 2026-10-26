"""Bridge adapter that connects the production ``SkillContext`` to ``SimWorld``.

``SkillContext`` only needs a small surface: ``is_protocol_transport_enabled``,
``write_command``, ``read_command_result`` and ``cancel_command``.  Results use
the same ``BridgeResult`` dataclass the real FileBridge returns, with the
``protocol_data`` envelope the context unwraps.
"""

from __future__ import annotations

import threading
from typing import Any

from .bootstrap import bootstrap as _bootstrap

_bootstrap()

from bridge.bridge_result import BridgeResult  # noqa: E402

from .sim_world import SimWorld  # noqa: E402


_QUERY_CAPABILITIES = {
    "query_runtime_snapshot",
    "query_inventory_snapshot",
    "query_world_region",
    "query_time",
}


class SimBridge:
    """Synchronous fake FileBridge backed by a ``SimWorld``."""

    def __init__(self, world: SimWorld):
        self.world = world
        self.commands: list[dict[str, Any]] = []
        self._results: dict[str, BridgeResult] = {}
        self._seq = 0
        self._lock = threading.Lock()

    # ── SkillContext bridge protocol ─────────────────────────────────────

    def is_protocol_transport_enabled(self) -> bool:
        return True

    def write_command(self, capability: str, params: dict[str, Any]) -> str:
        with self._lock:
            self._seq += 1
            seq = self._seq
            action_id = f"act_{seq}"
            self._results[action_id] = self._execute(capability, params or {}, action_id, seq)
            return action_id

    def read_command_result(self, action_id: str) -> BridgeResult | None:
        with self._lock:
            return self._results.pop(action_id, None)

    def cancel_command(self, action_id: str) -> None:
        with self._lock:
            self._results.pop(action_id, None)

    # ── internals ────────────────────────────────────────────────────────

    def _execute(self, capability: str, params: dict[str, Any], action_id: str, seq: int) -> BridgeResult:
        if capability in _QUERY_CAPABILITIES:
            ok, data = True, self._query(capability, params)
        else:
            ok, data, error, observation = self.world.apply(capability, params)
            if not ok:
                self.commands.append(
                    {"seq": seq, "capability": capability, "params": params, "ok": False, "error": error}
                )
                return BridgeResult(
                    success=False,
                    status="failed",
                    data={},
                    observation=observation,
                    side_effects=[],
                    error_code=error,
                    message=observation,
                    action_id=action_id,
                    seq=seq,
                )
            self.commands.append({"seq": seq, "capability": capability, "params": params, "ok": True, "error": None})
            return BridgeResult(
                success=True,
                status="ok",
                data={
                    "protocol_capability": capability,
                    "protocol_data_field": capability,
                    "protocol_data": data,
                },
                observation=observation,
                side_effects=[],
                error_code=None,
                message=None,
                action_id=action_id,
                seq=seq,
            )

        self.commands.append({"seq": seq, "capability": capability, "params": params, "ok": True, "error": None})
        return BridgeResult(
            success=True,
            status="ok",
            data={
                "protocol_capability": capability,
                "protocol_data_field": capability,
                "protocol_data": data,
            },
            observation="ok",
            side_effects=[],
            error_code=None,
            message=None,
            action_id=action_id,
            seq=seq,
        )

    def _query(self, capability: str, params: dict[str, Any]) -> dict[str, Any]:
        if capability == "query_runtime_snapshot":
            return self.world.runtime_fact()
        if capability == "query_inventory_snapshot":
            return self.world.inventory_fact()
        if capability == "query_world_region":
            return self.world.world_region_fact(params)
        if capability == "query_time":
            return {"fact": {"time_of_day": self.world.minutes}}
        return {"fact": {}}

    # ── log helpers ──────────────────────────────────────────────────────

    def capability_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for command in self.commands:
            name = command["capability"]
            counts[name] = counts.get(name, 0) + 1
        return counts

    def failed_commands(self) -> list[dict[str, Any]]:
        return [c for c in self.commands if not c["ok"]]
