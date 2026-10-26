# R1 / R10 / R12 Final Gap Verification

Date: 2026-07-11

## Changes

- R1: `RuntimeHarness.start_runtime` now serializes start decisions. Concurrent identical starts return one entry/process; conflicting spec or active generation raises `RuntimeConflictError`.
- R10: `test_core_contract_suite.py` is one parameterized test function shared by Fake and opt-in real Claude. Legacy Python Adapter remains explicitly unimplemented in this Spike.
- R12: provider-neutral `workspace.py` materializes a 0444 PersonaArtifact revision/hash snapshot and removes transient credential/config/revoke/scratch/persona files after stop while retaining owner/log evidence. Fake and Claude call the same cleanup helper.

## Independent commands

```text
python3 -m pytest tests/runtime_manager -q
# 32 passed, 10 skipped

python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py tests/test_gateway_runtime_telemetry.py -q
# 8 passed

RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest tests/runtime_manager/test_core_contract_suite.py -q -s
# 3 passed

RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest tests/runtime_manager/test_claude_adapter_real.py tests/runtime_manager/test_claude_failure_isolation_real.py -q -s
# first run: 8 passed, 1 failed because an existing test read A credential after the new stop cleanup

RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest tests/runtime_manager/test_claude_failure_isolation_real.py::test_dual_runtime_isolation_distinct_credentials_and_cross_mcp_rejection -q -s
# 1 passed after moving the no-secret audit assertion before stop and adding direct post-stop cleanup assertions
```

The real test failure was a stale test ordering assumption, not a Runtime failure: cleanup correctly removed the credential immediately after stopping A. The repaired test caches the credential only for the pre-existing audit non-leak assertion, then proves credential/config/scratch are absent after stop.

No production traffic, Gateway/Mod connection, database, commit, push or deploy was used.
