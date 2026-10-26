"""Run the StarCoPlay deterministic evaluation set.

Usage:
    agent/.venv/bin/python eval/run_eval.py
    agent/.venv/bin/python eval/run_eval.py --tag chop
    agent/.venv/bin/python eval/run_eval.py --id chop-001 --verbose
    agent/.venv/bin/python eval/run_eval.py --json eval/report.json
    agent/.venv/bin/python eval/run_eval.py --list

Exit code is 0 when every runnable scenario passes or is a known gap; a real
failure returns 1 so it can gate CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness.report import render, summarize, write_json
from harness.runner import run_scenario


SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"


def load_scenarios(directory: Path) -> list[dict]:
    scenarios = []
    for path in sorted(directory.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec.setdefault("_file", path.name)
        scenarios.append(spec)
    return scenarios


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StarCoPlay deterministic eval set")
    parser.add_argument("--scenarios", default=str(SCENARIO_DIR), help="scenario directory")
    parser.add_argument("--tag", action="append", default=[], help="only run scenarios with this tag")
    parser.add_argument("--id", action="append", default=[], help="only run these scenario ids")
    parser.add_argument("--json", dest="json_path", help="write a JSON report to this path")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--verbose", action="store_true", help="show failing checks and traces")
    args = parser.parse_args(argv)

    scenarios = load_scenarios(Path(args.scenarios))
    if args.tag:
        scenarios = [s for s in scenarios if set(args.tag) & set(s.get("tags") or [])]
    if args.id:
        wanted = set(args.id)
        scenarios = [s for s in scenarios if s.get("id") in wanted]

    if args.list:
        for spec in scenarios:
            print(f"{spec.get('id'):<28} {spec.get('status', 'runnable'):<9} {spec.get('title', '')}")
        return 0

    results = [run_scenario(spec, verbose=args.verbose) for spec in scenarios]
    summary = summarize(results)

    print(render(results, summary, verbose=args.verbose))

    if args.json_path:
        write_json(Path(args.json_path), summary, results)
        print(f"\nJSON report: {args.json_path}")

    return 0 if summary["failed"] == 0 and summary["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
