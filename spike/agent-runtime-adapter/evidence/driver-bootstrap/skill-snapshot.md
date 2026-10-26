---
name: codex-claude-driver
description: |
  Use Codex as a project driver/evaluator while delegating concrete execution to Claude Code.
  Use when the user wants Codex to supervise, advance, and verify a workflow while Claude Code performs implementation.
  Provides a proxy-gated Claude delegation script, driver state templates, and a 3-5 turn loop pattern.
  Trigger phrases include: Codex 调 Claude, Codex 调用 Claude, Codex 领导 Claude, Claude executor,
  Claude worker, Codex driver, Codex PM, Driver 模式, 让 Codex 推进 Claude, 让 Claude 执行 Codex 验收,
  codex-claude-driver, driver/evaluator, project driver, workflow driver, delegate to Claude.
argument-hint: "[init|run] [--cwd <workspace>] [--task <text-or-file>]"
user-invocable: true
allowed-tools: Bash, Read, Edit, Grep, Glob
---

# Codex-Claude Driver

Run Codex as the driver/evaluator and Claude Code as the executor.

This skill is for workflow control, not for replacing Claude's technical planning. Codex should drive turns, maintain state, run independent verification, and escalate to the user when needed. Claude should do the concrete implementation work inside bounded tasks.

## Core Contract

- Codex owns: state, task slicing, verification, progress, escalation.
- Claude owns: technical implementation and executor-side reporting.
- User owns: irreversible operations, scope changes, real ambiguity, budget decisions.

## Setup

Initialize a workspace:

```bash
/Users/river/Dev/agent_meta/skills/codex-claude-driver/scripts/claude-driver init --cwd "$PWD"
```

This creates:

```text
.driver/
├── delegate-claude.sh
├── logs/
└── turns/
DRIVER.md
DRIVER_STATE.json
```

The script defaults to proxy `http://127.0.0.1:7890`. Override with `CLAUDE_PROXY_URL` only when the user explicitly provides another proxy.

## Delegation Loop

For each turn:

1. Read `DRIVER.md` and `DRIVER_STATE.json`.
2. Write a complete, bounded task for Claude.
3. Delegate through the proxy-gated wrapper:

```bash
/Users/river/Dev/agent_meta/skills/codex-claude-driver/scripts/claude-driver run --cwd "$PWD" --task "Implement the next bounded task..."
```

4. Independently verify. Do not trust Claude's "done" claim.
5. Update `DRIVER_STATE.json` with evidence: command, output summary, touched files, pass/fail, blocker.
6. Continue, ask Claude to repair, or escalate to the user.

## Verification Rules

- MUST run verification commands from Codex where possible.
- MUST inspect relevant diffs/files before declaring done.
- MUST NOT let Claude push, commit, deploy, delete data, spend money, or change scope without user approval.
- MUST NOT call `claude` directly. Use the wrapper so proxy and logging are enforced.
- MUST keep a hard turn limit, normally 3 to 5 turns for a validation run.
- MUST record evidence in `DRIVER_STATE.json`; memory is not a source of truth.

## Resume Test Pattern

To test session continuity:

1. Turn N: ask Claude to remember a session-only marker and not write it to files.
2. Turn N+1: ask Claude to recall the marker without including it in the prompt.
3. Verify the marker appears in Claude's output, not in source/test/protocol files.

## Escalation Conditions

Stop and ask the user when:

- Claude fails the same task twice.
- Verification is inconclusive.
- The next step requires push, deploy, deletion, credentials, payment, or external side effects.
- The task boundary or product decision is ambiguous.
- Proxy readiness is uncertain.

## Output

End with:

- Turn count and session id.
- Verification commands and results.
- Files changed by Claude.
- Whether Codex edited implementation files.
- Remaining risks and the next real-world validation target.
