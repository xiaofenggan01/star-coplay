"""Reporting helpers for the evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


STATUS_ORDER = ["passed", "gap", "failed", "error", "skipped"]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: 0 for status in STATUS_ORDER}
    by_tag: dict[str, dict[str, int]] = {}
    for result in results:
        status = result.get("status", "error")
        counts[status] = counts.get(status, 0) + 1
        for tag in result.get("tags") or ["untagged"]:
            bucket = by_tag.setdefault(tag, {})
            bucket[status] = bucket.get(status, 0) + 1

    runnable = counts["passed"] + counts["gap"] + counts["failed"] + counts["error"]
    return {
        "total": len(results),
        "runnable": runnable,
        "skipped": counts["skipped"],
        **{key: counts[key] for key in STATUS_ORDER},
        "by_tag": by_tag,
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
    header = f"{'ID':<30}{'STATUS':<7}{'MS':>6}  TITLE"
    lines.append(header)
    lines.append("-" * len(header))
    for result in results:
        lines.append(
            f"{result.get('id', ''):<30}{_icon(result.get('status', '')):<7}"
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
