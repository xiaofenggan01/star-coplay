#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="$(mktemp -d)"
TMP_DIR="$TEST_ROOT/logs"
trap 'rm -rf "$TEST_ROOT"' EXIT
mkdir -p "$TMP_DIR/operations" "$TMP_DIR/metrics"

printf '%s\n' \
  '{"level":"INFO","log_id":"log-[.*]","runtime_id":"runtime-1","generation":2,"binding_id":"binding-1","command_id":"command-1","authorization":"Bearer secret"}' \
  '{"level":"WARNING","log_id":"other","runtime_id":"runtime-10","generation":2}' \
  '{"level":"ERROR","log_id":"error-1"}' \
  '{"level":"CRITICAL","log_id":"critical-1"}' \
  'not-json' >"$TMP_DIR/operations/runtime.jsonl"
printf '%s\n' \
  '{"level":"INFO","runtime_id":"runtime-1","generation":"1","log_id":"backup"}' \
  '{"level":"INFO","metric_name":"runtime.started"}' >"$TMP_DIR/operations/runtime.jsonl.1"
printf '%s\n' \
  '{"level":"INFO","name":"runtime.started"}' \
  '{"level":"INFO","name":"runtimeXstarted"}' >"$TMP_DIR/metrics/runtime-metrics.jsonl.7"
printf '%s\n' '{"level":"ERROR","log_id":"excluded-gzip"}' >"$TMP_DIR/operations/runtime.jsonl.gz"
printf '%s\n' '{"level":"ERROR","log_id":"excluded-suffix"}' >"$TMP_DIR/operations/runtime.jsonl.old"
printf '%s\n' '{"level":"ERROR","log_id":"excluded-symlink"}' >"$TEST_ROOT/outside.json"
ln -s "$TEST_ROOT/outside.json" "$TMP_DIR/operations/leak.jsonl.2"

run() { STARCOPLAY_LOG_DIR="$TMP_DIR" bash "$SCRIPT_DIR/starlog.sh" "$@"; }
assert_lines() {
  local expected="$1"
  shift
  local output
  output="$(run "$@")"
  [[ "$(printf '%s\n' "$output" | sed '/^$/d' | wc -l | tr -d ' ')" == "$expected" ]] || {
    printf 'expected %s lines for %s, got:\n%s\n' "$expected" "$*" "$output" >&2
    exit 1
  }
  printf '%s' "$output"
}

assert_lines 1 logid 'log-[.*]' | grep -Fq '"authorization":"[REDACTED]"'
assert_lines 2 runtime runtime-1 >/dev/null
assert_lines 1 runtime runtime-1 2 >/dev/null
assert_lines 1 runtime runtime-1 1 >/dev/null
assert_lines 1 binding binding-1 >/dev/null
assert_lines 1 command command-1 >/dev/null
assert_lines 2 errors >/dev/null
assert_lines 2 metrics runtime.started >/dev/null
[[ -z "$(run logid 'log-.*')" ]]
[[ -z "$(run logid 'excluded-symlink')" ]]
bash -n "$SCRIPT_DIR/starlog.sh"
printf 'starlog tests passed\n'
