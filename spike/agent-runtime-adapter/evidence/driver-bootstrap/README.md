# Driver Bootstrap Evidence

Captured: 2026-07-11

Scope: validate the `codex-claude-driver` execution path, explicit model selection, and session resume. No business implementation was modified.

## Result

- Claude Code CLI: `2.1.207`.
- Proxy: `http://127.0.0.1:7890` reachable; proxied request reached Anthropic and returned HTTP 404 at the root path.
- Explicit model: workspace-local delegate passes `--model "$model"`, with `CLAUDE_DRIVER_MODEL` defaulting to `claude-sonnet-5`.
- Session: `5f465248-8e0b-471b-b33e-745f9238a8a2`.
- Turn 1 stored a marker only in conversation context.
- Turn 2 resumed the same session and recalled the marker without receiving it in that turn.
- Both JSON results report `modelUsage.claude-sonnet-5`.
- Claude changed no files.

## Evidence

- `delegate-claude-used.sh`: exact workspace-local wrapper used.
- `turn-1-claude.json`: first session result.
- `turn-2-claude.json`: resumed session result.
- `DRIVER_STATE.snapshot.json`: verified driver state after two turns.
- `skill-snapshot.md`, `claude-driver-script-snapshot.sh`, `delegate-template-snapshot.sh`: inspected source snapshots.

## Verification commands

```bash
python3 -m json.tool DRIVER_STATE.json >/dev/null
rg -l 'ORBIT-CEDAR-7319' . --glob '!.driver/**' --glob '!spike/agent-runtime-adapter/evidence/driver-bootstrap/turn-*.json'
python3 - <<'PY'
import json
for p in ['.driver/logs/turn-1-claude.json', '.driver/logs/turn-2-claude.json']:
    d = json.load(open(p))
    print(p, d['session_id'], list(d['modelUsage']), d['result'])
PY
```

The marker search returned no source, test, protocol, or other non-driver files.
