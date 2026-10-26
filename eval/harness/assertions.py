"""Assertion evaluation for eval scenarios.

Every scenario declares ``expect`` blocks that are checked against the skill
result, the simulated world snapshot, and the command ledger.  Unknown keys are
reported as ``unknown_check`` instead of silently passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def _compare(actual: Any, spec: Any) -> tuple[bool, str]:
    """Compare ``actual`` against a spec.

    Spec forms:
      scalar              -> equality
      {"min": n}          -> actual >= n
      {"max": n}          -> actual <= n
      {"equals": v}       -> equality
      {"contains": [...]} -> every element present
      {"not_contains": [...]} -> no element present
    """
    if isinstance(spec, dict):
        if "equals" in spec:
            ok = actual == spec["equals"]
            return ok, f"expected equals {spec['equals']!r}, got {actual!r}"
        if "min" in spec:
            try:
                ok = actual is not None and actual >= spec["min"]
            except TypeError:
                return False, f"cannot compare {actual!r} >= {spec['min']!r}"
            return ok, f"expected >= {spec['min']}, got {actual!r}"
        if "max" in spec:
            try:
                ok = actual is not None and actual <= spec["max"]
            except TypeError:
                return False, f"cannot compare {actual!r} <= {spec['max']!r}"
            return ok, f"expected <= {spec['max']}, got {actual!r}"
        if "contains" in spec:
            actual_set = set(actual or [])
            missing = [item for item in spec["contains"] if item not in actual_set]
            return not missing, f"missing {missing}" if missing else "all present"
        if "not_contains" in spec:
            actual_set = set(actual or [])
            present = [item for item in spec["not_contains"] if item in actual_set]
            return not present, f"unexpected {present}" if present else "none present"
        return False, f"unsupported spec {spec!r}"

    ok = actual == spec
    return ok, f"expected {spec!r}, got {actual!r}"


def flatten_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested metric dicts to dotted keys (inventory.Wood etc.)."""
    flat: dict[str, Any] = {}
    for key, value in metrics.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                flat[f"{key}.{sub_key}"] = sub_value
        else:
            flat[key] = value
    return flat


def evaluate(
    expect: dict[str, Any],
    *,
    result: dict[str, Any],
    metrics: dict[str, Any],
    commands: list[dict[str, Any]],
) -> list[Check]:
    checks: list[Check] = []
    flat = flatten_metrics(metrics)

    if "success" in expect:
        ok, detail = _compare(bool(result.get("success")), expect["success"])
        checks.append(Check("success", ok, detail))

    if "status" in expect:
        ok, detail = _compare(result.get("status"), expect["status"])
        checks.append(Check("status", ok, detail))

    for key, spec in (expect.get("data") or {}).items():
        actual = (result.get("data") or {}).get(key)
        ok, detail = _compare(actual, spec)
        checks.append(Check(f"data.{key}", ok, detail))

    for section in ("world", "budget"):
        for key, spec in (expect.get(section) or {}).items():
            ok, detail = _compare(flat.get(key), spec)
            checks.append(Check(f"{section}.{key}", ok, detail))

    command_spec = expect.get("commands") or {}
    names = [c["capability"] for c in commands]
    name_set = set(names)

    if "must_include" in command_spec:
        missing = [n for n in command_spec["must_include"] if n not in name_set]
        checks.append(Check("commands.must_include", not missing, f"missing {missing}" if missing else "all present"))

    if "must_exclude" in command_spec:
        present = [n for n in command_spec["must_exclude"] if n in name_set]
        checks.append(
            Check("commands.must_exclude", not present, f"unexpected {present}" if present else "none present")
        )

    if "max_total" in command_spec:
        ok = len(commands) <= int(command_spec["max_total"])
        checks.append(Check("commands.max_total", ok, f"{len(commands)} commands (max {command_spec['max_total']})"))

    for capability, limit in (command_spec.get("max_per") or {}).items():
        count = names.count(capability)
        ok = count <= int(limit)
        checks.append(Check(f"commands.max_per.{capability}", ok, f"{count} calls (max {limit})"))

    warnings = (result.get("data") or {}).get("warnings") or []
    if "warnings_max" in expect:
        ok = len(warnings) <= int(expect["warnings_max"])
        checks.append(Check("warnings_max", ok, f"{len(warnings)} warnings (max {expect['warnings_max']})"))
    if "warnings_required" in expect:
        codes = {w.get("code") for w in warnings if isinstance(w, dict)}
        missing = [c for c in expect["warnings_required"] if c not in codes]
        checks.append(Check("warnings_required", not missing, f"missing {missing}" if missing else "all present"))

    return checks
