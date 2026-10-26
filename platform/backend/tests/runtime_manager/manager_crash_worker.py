"""Independent Spike Manager worker for OS-level crash/reconcile tests."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path


def _atomic_write_json(path: object, data: object) -> None:
    """Same-directory temp file + flush + fsync + os.replace for crash-safe writes."""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


from modules.runtime_manager.contracts import RuntimeActualState, RuntimeRecord, RuntimeSpec
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from modules.runtime_manager.adapters.claude.adapter import ClaudeAgentRuntimeAdapter
from modules.runtime_manager.repository import FileRuntimeRepository


async def start_worker(args: argparse.Namespace) -> None:
    adapter = ClaudeAgentRuntimeAdapter() if args.provider == "claude" else FakeAgentRuntimeAdapter()
    private = {"fake_ready_delay": 0.15} if args.provider == "fake" else {
        "model": "claude-sonnet-5",
        "ready_deadline_seconds": 60.0,
        "turn_deadline_seconds": 60.0,
        "controlled_mcp_server": str(Path(__file__).with_name("controlled_mcp_server.py")),
    }
    spec = RuntimeSpec(
        runtime_id=args.runtime_id,
        generation=args.generation,
        user_id="crash-user",
        agentee_id="crash-agentee",
        provider=args.provider,
        workspace_root=Path(args.workspace),
        provider_private_metadata=private,
    )
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    state = RuntimeActualState.STARTING
    if args.state != "starting":
        ready_deadline = float(private.get("ready_deadline_seconds", 5.0))
        ready = await adapter.wait_ready(handle, deadline=ready_deadline)
        if not ready.ready:
            raise RuntimeError(
                "runtime_ready_timeout "
                f"provider={args.provider} runtime_id={spec.runtime_id} "
                f"generation={spec.generation} deadline_seconds={ready_deadline}"
            )
        state = RuntimeActualState.READY
    if args.state == "running":
        state = RuntimeActualState.RUNNING
    elif args.state == "draining":
        state = RuntimeActualState.DRAINING

    record = RuntimeRecord(
        runtime_id=spec.runtime_id,
        generation=spec.generation,
        desired_state=RuntimeActualState.READY,
        actual_state=state,
        workdir=prepared.workdir,
        owner_nonce=str(prepared.provider_private_metadata["nonce"]),
    )
    FileRuntimeRepository(Path(args.repository)).save_all([record])
    owner = json.loads((prepared.workdir / "runtime-owner.json").read_text(encoding="utf-8"))
    _atomic_write_json(args.sentinel, {"manager_pid": os.getpid(), "runtime_pid": owner["pid"], "state": state.value})
    while True:
        await asyncio.sleep(60)


async def reconcile_worker(args: argparse.Namespace) -> None:
    repo = FileRuntimeRepository(Path(args.repository))
    records = repo.load_all()
    converged = []
    decisions = []
    for record in records:
        # Revoke before touching the orphan. A surviving child can no longer
        # use the old generation's MCP credential during reconciliation.
        (record.workdir / "credential.revoked").write_text("revoked", encoding="utf-8")
        (record.workdir / "mcp-revoked").write_text("revoked", encoding="utf-8")
        owner = json.loads((record.workdir / "runtime-owner.json").read_text(encoding="utf-8"))
        adapter = ClaudeAgentRuntimeAdapter() if owner.get("provider") == "claude" else FakeAgentRuntimeAdapter()
        decision = await adapter.reconcile(replace(record, desired_state=RuntimeActualState.STOPPED))
        decisions.append(decision.action.value)
        converged.append(
            replace(
                record,
                desired_state=RuntimeActualState.STOPPED,
                actual_state=RuntimeActualState.STOPPED,
                exit_reason="manager-crash-reconciled",
            )
        )
    repo.save_all(converged)
    _atomic_write_json(args.sentinel, {"decisions": decisions})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("start", "reconcile"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sentinel", required=True)
    parser.add_argument("--runtime-id", default="rt-crash")
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--state", choices=("starting", "ready", "running", "draining"), default="ready")
    parser.add_argument("--provider", choices=("fake", "claude"), default="fake")
    args = parser.parse_args()
    asyncio.run(start_worker(args) if args.mode == "start" else reconcile_worker(args))


if __name__ == "__main__":
    main()
