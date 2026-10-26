"""Run the StarCoPlay deterministic evaluation set.

Usage:
    agent/.venv/bin/python eval/run_eval.py
    agent/.venv/bin/python eval/run_eval.py --tag chop
    agent/.venv/bin/python eval/run_eval.py --id chop-001 --verbose
    agent/.venv/bin/python eval/run_eval.py --json eval/report.json
    agent/.venv/bin/python eval/run_eval.py --list

    # compare the deterministic router with the LLM planner on goal scenarios
    LLM_RELAY_BASE_URL=... LLM_RELAY_API_KEY=... LLM_MODEL=... \
      agent/.venv/bin/python eval/run_eval.py --policy both --runs 3 \
        --transcript-dir eval/transcripts

Exit code is 0 when every runnable scenario passes or is a known gap; a real
failure returns 1 so it can gate CI.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from harness.policies.llm import LlmConfig, LlmPolicy, ScriptedPlannerClient
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


def _write_transcript(directory: Path, result: dict, run_index: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": result.get("id"),
        "run": run_index,
        "status": result.get("status"),
        "metrics": result.get("metrics"),
        "checks": result.get("checks"),
        "result": result.get("result"),
    }
    path = directory / f"{result.get('id')}--run{run_index}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StarCoPlay deterministic eval set")
    parser.add_argument("--scenarios", default=str(SCENARIO_DIR), help="scenario directory")
    parser.add_argument("--tag", action="append", default=[], help="only run scenarios with this tag")
    parser.add_argument("--id", action="append", default=[], help="only run these scenario ids")
    parser.add_argument("--json", dest="json_path", help="write a JSON report to this path")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--verbose", action="store_true", help="show failing checks and traces")
    parser.add_argument(
        "--policy",
        choices=["routing", "llm", "both"],
        default=None,
        help="override goal-scenario policy (default: scenario's own, usually routing)",
    )
    parser.add_argument("--runs", type=int, default=1, help="LLM repetitions per goal scenario")
    parser.add_argument("--llm-model", default="", help="concrete model name for the LLM policy")
    parser.add_argument("--llm-base-url", default="", help="OpenAI-compatible base url (or env)")
    parser.add_argument("--llm-api-key", default="", help="API key (or env)")
    parser.add_argument(
        "--transcript-dir",
        default="",
        help="write one JSON transcript per LLM run into this directory",
    )
    parser.add_argument(
        "--llm-script",
        default="",
        help="offline replay: JSON map of scenario id -> list of raw planner responses",
    )
    args = parser.parse_args(argv)

    scenarios = load_scenarios(Path(args.scenarios))
    if args.tag:
        scenarios = [s for s in scenarios if set(args.tag) & set(s.get("tags") or [])]
    if args.id:
        scenarios = [
            s
            for s in scenarios
            if any(s.get("id") == wanted or str(s.get("id") or "").startswith(wanted) for wanted in args.id)
        ]

    if not scenarios:
        # A CI run that silently evaluates nothing must not look green.
        print(f"no scenarios matched under {args.scenarios}", file=sys.stderr)
        return 2

    if args.list:
        for spec in scenarios:
            print(f"{spec.get('id'):<28} {spec.get('status', 'runnable'):<9} {spec.get('title', '')}")
        return 0

    policies = [args.policy] if args.policy and args.policy != "both" else (
        ["routing", "llm"] if args.policy == "both" else [None]
    )

    llm_script: dict[str, list[str]] = {}
    llm_config: LlmConfig | None = None
    if "llm" in policies:
        if args.llm_script:
            llm_script = json.loads(Path(args.llm_script).read_text(encoding="utf-8"))
            llm_config = LlmConfig(model="scripted")
        else:
            llm_config = LlmConfig(
                model=args.llm_model,
                base_url=args.llm_base_url,
                api_key=args.llm_api_key,
            )
            env_config = LlmConfig.from_env(model=args.llm_model)
            if not llm_config.base_url:
                llm_config.base_url = env_config.base_url
            if not llm_config.api_key:
                llm_config.api_key = env_config.api_key
            if not llm_config.model:
                llm_config.model = env_config.model
            if not llm_config.model:
                print("llm policy needs --llm-model (or LLM_MODEL)", file=sys.stderr)
                return 2
            if not llm_config.api_key:
                print(
                    "llm policy needs an API key (--llm-api-key or LLM_RELAY_API_KEY/OPENAI_API_KEY)",
                    file=sys.stderr,
                )
                return 2

    transcript_dir = Path(args.transcript_dir) if args.transcript_dir else None
    if transcript_dir is not None and "llm" in policies:
        transcript_dir = transcript_dir / _dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    results: list[dict] = []
    for spec in scenarios:
        if spec.get("mode") != "goal":
            results.append(run_scenario(spec, verbose=args.verbose))
            continue

        for chosen in policies:
            if chosen == "llm":
                if llm_script:
                    responses = llm_script.get(str(spec.get("id")))
                    if responses is None:
                        print(f"--llm-script has no entry for {spec.get('id')}", file=sys.stderr)
                        return 2
                    scenario_policy = LlmPolicy(
                        client=ScriptedPlannerClient(list(responses)),
                        config=llm_config,
                    )
                else:
                    scenario_policy = LlmPolicy(config=llm_config)
                for run_index in range(1, max(1, args.runs) + 1):
                    result = run_scenario(
                        spec,
                        verbose=args.verbose,
                        policy="llm",
                        llm_policy=scenario_policy,
                    )
                    result["run"] = run_index
                    results.append(result)
                    if transcript_dir is not None:
                        _write_transcript(transcript_dir, result, run_index)
            else:
                results.append(run_scenario(spec, verbose=args.verbose, policy=chosen))

    summary = summarize(results)
    print(render(results, summary, verbose=args.verbose))

    # Side-by-side policy comparison, only when more than one policy ran.
    policy_stats = summary.get("by_policy") or {}
    if len([p for p in policy_stats if p in ("routing", "llm")]) > 1:
        print("\npolicy comparison")
        for name in ("routing", "llm"):
            bucket = policy_stats.get(name) or {}
            total = sum(bucket.values())
            passed = bucket.get("passed", 0)
            failed = bucket.get("failed", 0) + bucket.get("error", 0)
            gaps = bucket.get("gap", 0)
            print(f"  {name:<8} total={total:<3} passed={passed:<3} failed={failed:<3} gaps={gaps}")

    if args.json_path:
        write_json(Path(args.json_path), summary, results)
        print(f"\nJSON report: {args.json_path}")
    if transcript_dir is not None:
        print(f"transcripts: {transcript_dir}")

    return 0 if summary["failed"] == 0 and summary["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
