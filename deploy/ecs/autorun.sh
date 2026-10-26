#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="${REPO_DIR:-/root/workspace/starcoplay/star-coplay}"
BRANCH="${BRANCH:-main}"
USE_RELEASE="${USE_RELEASE:-1}"
SYNC_GIT="${SYNC_GIT:-0}"
STARCOPLAY_LOG_DIR="${STARCOPLAY_LOG_DIR:-/var/log/starcoplay}"
LOG_DIR="${LOG_DIR:-$STARCOPLAY_LOG_DIR/systemd}"
PID_DIR="${PID_DIR:-$REPO_DIR/.runtime/pids}"
ENV_FILE="${ENV_FILE:-/etc/star-coplay.env}"
CONDA_ENV="${CONDA_ENV:-starcoplay-ws}"
CONDA_SH="${CONDA_SH:-}"
NPM_MODE="${NPM_MODE:-prod}"
NEXT_HOST="${NEXT_HOST:-0.0.0.0}"
NEXT_PORT="${NEXT_PORT:-3000}"

mkdir -p "$LOG_DIR" "$PID_DIR" "$STARCOPLAY_LOG_DIR/operations" "$STARCOPLAY_LOG_DIR/metrics" "$STARCOPLAY_LOG_DIR/audit" "$STARCOPLAY_LOG_DIR/sessions" "$STARCOPLAY_LOG_DIR/systemd"
chmod 0700 "$STARCOPLAY_LOG_DIR/audit"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_DIR/autorun.log"; }

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

resolve_conda_sh() {
  if [[ -n "$CONDA_SH" && -f "$CONDA_SH" ]]; then
    echo "$CONDA_SH"
    return 0
  fi
  local d
  for d in "$HOME/miniconda3" "$HOME/anaconda3" "/opt/miniconda3" "$HOME/mambaforge"; do
    if [[ -f "$d/etc/profile.d/conda.sh" ]]; then
      echo "$d/etc/profile.d/conda.sh"
      return 0
    fi
  done
  return 1
}

CONDA_SH="$(resolve_conda_sh)" || {
  echo "conda.sh not found; set CONDA_SH to .../conda.sh" >&2
  exit 1
}
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV"
if [[ "${CONDA_DEFAULT_ENV:-}" != "$CONDA_ENV" ]]; then
  echo "conda activate failed: want $CONDA_ENV, got ${CONDA_DEFAULT_ENV:-}" >&2
  exit 1
fi
PY="${CONDA_PREFIX}/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "no python in conda env: $PY" >&2
  exit 1
fi
if [[ -x "${CONDA_PREFIX}/bin/npm" ]]; then
  NPM="${CONDA_PREFIX}/bin/npm"
else
  NPM="npm"
fi

remote_ref() {
  if [[ "$USE_RELEASE" == "1" ]]; then
    git -C "$REPO_DIR" ls-remote --tags --refs origin \
      | awk -F/ '{print $NF}' | sort -V | tail -n1
  else
    git -C "$REPO_DIR" ls-remote origin "$BRANCH" | awk '{print $1}'
  fi
}

alive() {
  local pf="$PID_DIR/$1.pid"
  [[ -f "$pf" ]] || return 1
  kill -0 "$(cat "$pf")" 2>/dev/null
}

stop_proc() {
  local name="$1"
  local pf="$PID_DIR/$name.pid"
  if [[ ! -f "$pf" ]]; then
    return 0
  fi
  local pid
  pid=$(cat "$pf")
  if kill -0 "$pid" 2>/dev/null; then
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    local i
    for i in $(seq 1 20); do
      if ! kill -0 "$pid" 2>/dev/null; then break; fi
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pf"
}

start_gateway() {
  cd "$REPO_DIR"
  export STARCOPLAY_LOG_DIR
  export STARCOPLAY_STRUCTURED_LOGGING=1
  nohup "$PY" -m app \
    >>"$STARCOPLAY_LOG_DIR/systemd/gateway.stdout.log" 2>>"$STARCOPLAY_LOG_DIR/systemd/gateway.stderr.log" < /dev/null &
  echo $! >"$PID_DIR/gateway.pid"
  log "gateway started pid=$(cat "$PID_DIR/gateway.pid")"
}

start_web() {
  cd "$REPO_DIR/platform/web"
  export HOSTNAME="$NEXT_HOST"
  export PORT="$NEXT_PORT"
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${NEXT_PORT}"/tcp 2>/dev/null || true
    sleep 1
  fi
  local sub="start"
  if [[ "$NPM_MODE" == "dev" ]]; then sub="dev"; fi
  nohup "$NPM" run "$sub" -- -H "$NEXT_HOST" -p "$NEXT_PORT" \
    >>"$LOG_DIR/web.log" 2>>"$LOG_DIR/web.err" < /dev/null &
  echo $! >"$PID_DIR/web.pid"
  log "web started pid=$(cat "$PID_DIR/web.pid") mode=$NPM_MODE host=$NEXT_HOST port=$NEXT_PORT"
}

install_deps_and_build() (
  set -e
  cd "$REPO_DIR"
  "$PY" -m pip install -r platform/backend/requirements.txt
  "$PY" -m pip install -r agent/requirements.txt
  cd "$REPO_DIR/platform/web"
  if [[ -f package-lock.json ]]; then
    "$NPM" ci
  else
    "$NPM" install
  fi
  if [[ "$NPM_MODE" != "dev" ]]; then
    "$NPM" run build
  fi
)

update_and_build() (
  set -e
  cd "$REPO_DIR"
  git fetch --tags --force --prune origin
  if [[ "$USE_RELEASE" == "1" ]]; then
    tag="$1"
    if [[ -z "$tag" ]]; then
      log "no release tag yet, fallback to branch $BRANCH"
      git checkout -f "$BRANCH"
      git reset --hard "origin/$BRANCH"
    else
      git fetch origin "refs/tags/$tag:refs/tags/$tag" 2>/dev/null || true
      git checkout -f "tags/$tag"
      log "checked out tag $tag"
    fi
  else
    git checkout -f "$BRANCH"
    git reset --hard "origin/$BRANCH"
    log "fast-forwarded $BRANCH"
  fi
  install_deps_and_build
)

restart_all() {
  stop_proc gateway
  stop_proc web
  start_web
  start_gateway
}

deploy_once() {
  local at
  at="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo "?")"
  if [[ "${SYNC_GIT}" == "1" ]]; then
    local cur_ref
    cur_ref="$(remote_ref || echo "")"
    log "deploy start SYNC_GIT=1 ref=$cur_ref use_release=$USE_RELEASE branch=$BRANCH"
    if ! update_and_build "$cur_ref"; then
      log "deploy failed: update_and_build"
      exit 1
    fi
  else
    log "deploy start from current tree HEAD=$at (set SYNC_GIT=1 or run deploy-sync to fetch/checkout from origin)"
    if ! install_deps_and_build; then
      log "deploy failed: install_deps_and_build"
      exit 1
    fi
  fi
  restart_all
  log "deploy done"
}

cmd="${1:-deploy}"
case "$cmd" in
  stop)
    stop_proc gateway
    stop_proc web
    log "stopped all"
    exit 0
    ;;
  status)
    for n in gateway web; do
      if alive "$n"; then
        echo "$n: running pid=$(cat "$PID_DIR/$n.pid")"
      else
        echo "$n: stopped"
      fi
    done
    exit 0
    ;;
  deploy|run)
    deploy_once
    ;;
  deploy-sync)
    SYNC_GIT=1
    deploy_once
    ;;
  *) echo "usage: $0 [deploy|run|deploy-sync|stop|status]" >&2; exit 2 ;;
esac
