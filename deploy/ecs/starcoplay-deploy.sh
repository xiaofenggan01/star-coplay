#!/usr/bin/env bash
set -euo pipefail

DEPLOY_REF="${1:-}"
REPO_DIR="${REPO_DIR:-/root/workspace/starcoplay/star-coplay}"
PY="${PY:-/root/miniconda3/envs/starcoplay-ws/bin/python}"
NPM="${NPM:-npm}"
SERVICE_NAME="${SERVICE_NAME:-starcoplay-gateway}"

cd "$REPO_DIR"

git fetch origin --tags --prune

if [[ -z "$DEPLOY_REF" ]]; then
  DEPLOY_REF="main"
fi

if git ls-remote --exit-code --heads origin "$DEPLOY_REF" >/dev/null 2>&1; then
  git checkout -B "$DEPLOY_REF" "origin/$DEPLOY_REF"
  git reset --hard "origin/$DEPLOY_REF"
elif git rev-parse -q --verify "refs/tags/$DEPLOY_REF" >/dev/null; then
  git checkout -f "refs/tags/$DEPLOY_REF"
else
  git fetch origin "refs/tags/$DEPLOY_REF:refs/tags/$DEPLOY_REF"
  git checkout -f "refs/tags/$DEPLOY_REF"
fi

echo "==> HEAD: $(git rev-parse --short HEAD)"

"$PY" -m pip install -r platform/backend/requirements.txt
"$PY" -m pip install -r agent/requirements.txt

cd "$REPO_DIR/platform/web"
if [[ -f package-lock.json ]]; then
  "$NPM" ci
else
  "$NPM" install
fi
"$NPM" run build

systemctl restart "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager -l
