"""Reporting helpers for the evaluation harness."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


STATUS_ORDER = ["passed", "gap", "failed", "error", "skipped"]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: 0 for status in STATUS_ORDER}
    by_tag: dict[str, dict[str, int]] = {}
    by_policy: dict[str, dict[str, int]] = {}
    for result in results:
        status = result.get("status", "error")
        counts[status] = counts.get(status, 0) + 1
        for tag in result.get("tags") or ["untagged"]:
            bucket = by_tag.setdefault(tag, {})
            bucket[status] = bucket.get(status, 0) + 1
        policy_key = result.get("policy") or "skill"
        bucket = by_policy.setdefault(policy_key, {})
        bucket[status] = bucket.get(status, 0) + 1

    aggregate: dict[str, dict[str, Any]] = {}
    failure_kinds: dict[str, int] = {}
    for result in results:
        metrics = result.get("metrics") or {}
        key = result.get("policy") or "skill"
        bucket = aggregate.setdefault(
            key,
            {"runs": 0, "tokens": [], "latency_ms": [], "failures": 0, "errors": 0},
        )
        bucket["runs"] += 1
        if metrics.get("llm_tokens"):
            bucket["tokens"].append(metrics["llm_tokens"])
        if metrics.get("llm_latency_ms"):
            bucket["latency_ms"].append(metrics["llm_latency_ms"])
        if result.get("status") == "failed":
            bucket["failures"] += 1
        if result.get("status") == "error":
            bucket["errors"] += 1
        kind = result.get("failure_kind")
        if kind:
            failure_kinds[kind] = failure_kinds.get(kind, 0) + 1

    for bucket in aggregate.values():
        tokens = bucket.pop("tokens")
        latency = bucket.pop("latency_ms")
        bucket["total_tokens"] = sum(tokens)
        bucket["mean_tokens"] = round(statistics.mean(tokens), 1) if tokens else 0
        bucket["std_tokens"] = round(statistics.pstdev(tokens), 1) if len(tokens) > 1 else 0
        bucket["mean_latency_ms"] = round(statistics.mean(latency), 0) if latency else 0
        bucket["std_latency_ms"] = round(statistics.pstdev(latency), 0) if len(latency) > 1 else 0

    runnable = counts["passed"] + counts["gap"] + counts["failed"] + counts["error"]
    return {
        "total": len(results),
        "runnable": runnable,
        "skipped": counts["skipped"],
        **{key: counts[key] for key in STATUS_ORDER},
        "by_tag": by_tag,
        "by_policy": by_policy,
        "aggregate": aggregate,
        "failure_kinds": failure_kinds,
        "schema_version": 2,
    }


def _icon(status: str) -> str:
    return {
        "passed": "PASS",
        "gap": "GAP ",
        "failed": "FAIL",
        "error": "ERR ",
        "skipped": "SKIP",
    }.get(status, "????")


def render(results: list[dict[str, Any]], summary: dict[str, Any], *, verbose: bool = False) -> str:
    lines: list[str] = []
    header = f"{'ID':<30}{'POLICY':<9}{'STATUS':<7}{'MS':>6}  TITLE"
    lines.append(header)
    lines.append("-" * len(header))
    for result in results:
        lines.append(
            f"{result.get('id', ''):<30}{str(result.get('policy') or '-'):<9}"
            f"{_icon(result.get('status', '')):<7}"
            f"{result.get('duration_ms', 0):>6}  {result.get('title', '')}"
        )
        if verbose or result.get("status") in ("failed", "error"):
            for check in result.get("checks") or []:
                if not check.get("ok"):
                    lines.append(f"    - {check['name']}: {check['detail']}")
            if result.get("error"):
                lines.append(f"    ! {result['error']}")
            if result.get("trace"):
                lines.append(result["trace"])

    aggregate = summary.get("aggregate") or {}
    if any(name in aggregate for name in ("llm", "agent")):
        lines.append("")
        lines.append("policy stats")
        for name in ("routing", "llm", "agent"):
            bucket = aggregate.get(name)
            if not bucket:
                continue
            line = f"  {name:<8} runs={bucket['runs']:<3}"
            if bucket.get("total_tokens"):
                line += (
                    f" tokens={bucket['total_tokens']} "
                    f"(mean {bucket['mean_tokens']} ± {bucket['std_tokens']})"
                )
            if bucket.get("mean_latency_ms"):
                line += (
                    f" latency={bucket['mean_latency_ms']:.0f}ms "
                    f"± {bucket['std_latency_ms']:.0f}"
                )
            lines.append(line)

    failure_kinds = summary.get("failure_kinds") or {}
    if failure_kinds:
        lines.append("  failures: " + ", ".join(f"{k}={v}" for k, v in sorted(failure_kinds.items())))

    lines.append("-" * len(header))
    lines.append(
        f"total={summary['total']}  runnable={summary['runnable']}  "
        f"passed={summary['passed']}  gaps={summary['gap']}  "
        f"failed={summary['failed']}  errors={summary['error']}  skipped={summary['skipped']}"
    )
    return "\n".join(lines)


def write_json(path: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "results": [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "status": r.get("status"),
                "policy": r.get("policy") or ("skill" if r.get("skill") else None),
                "failure_kind": r.get("failure_kind"),
                "tags": r.get("tags"),
                "checks": r.get("checks"),
                "metrics": r.get("metrics"),
                "result": r.get("result"),
                "error": r.get("error"),
                "duration_ms": r.get("duration_ms"),
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
