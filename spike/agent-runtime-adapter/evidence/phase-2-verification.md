# Phase 2 Harness Verification

Date: 2026-07-11

## Driver

- Session: `44c47323-72a5-456f-bb4a-dd13936cef12`
- Executor model: `claude-sonnet-5` (local wrapper explicitly passes `--model claude-sonnet-5`)
- Bounded loop: Turns 1-4 complete
- Turn 3 was interrupted after prolonged silence once files were written; Codex independently verified and used Turn 4 only for missing direct evidence.

## Independent verification

From `platform/backend`:

```text
$ python3 -m pytest tests/runtime_manager -q
25 passed in 3.82s

$ python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
6 passed in 0.05s
```

Additional checks:

- `git diff --check`: passed.
- Legacy runtime files and tests: no diff.
- Provider/Gateway executable import scan: no matches.
- Credential/secret pattern scan: no matches.
- Fake runtime process scan after tests: no remaining process.
- `ruff` and `mypy`: not run because neither module is installed in the active Python 3.14 environment.

## Proven behaviors

- Ready requires a matching protocol handshake; PID liveness is insufficient.
- Three turns preserve the same runtime/generation/PID and in-memory marker.
- Stop escalation covers application stop, TERM and KILL.
- Default idle TTL is 600 seconds; injected 10-second TTL does not reclaim at 9.9 seconds and reclaims at 10 seconds.
- Successful turn/MCP result refreshes activity; logs and repeated revoked-credential errors do not.
- Inflight expiry enters DRAINING before stop.
- Fake MCP covers initialize, tools/list, call/result, credential fingerprint and live-process revocation.
- Cross-runtime credentials are rejected without exposing credential text.
- Reconcile checks runtime ID, generation and owner nonce; stale generations are ignored and unknown ownership is quarantined.
- A second registry/adapter reconciles a real Fake child process and terminates a trusted orphan when desired state is STOPPED.
- Two concurrent runtimes isolate WorkDir, environment sentinel, credential/fingerprint, logs and marker state; stopping A does not affect B.

## Scope safeguards

No production traffic, Gateway/Mod connection, database, real credential, Claude Adapter, commit, push or deployment was used. Claude/Gateway-specific names occur only in negative boundary docstrings, not in executable imports or public contract fields.
