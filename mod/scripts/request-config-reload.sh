#!/bin/bash
# request-config-reload.sh — 原子发布重载请求并等待结果
#
# 用法:
#   ./request-config-reload.sh [--timeout SECONDS]
#
# 退出码:
#   0   reload 成功 (outcome=succeeded)
#   1   拒绝 (refused_single_flight / refused_busy) 或超时
#   2   reload 失败 (config_read_failed / bridge_rebuild_failed / unknown)
#   3   脚本错误 (参数错误 / 依赖缺失 / 结果解析失败)
#
# 设计:
#   - 无需游戏输入、窗口焦点、Bridge 连接
#   - 请求通过 temp+rename 原子发布
#   - 结果轮询匹配 request_id，最多等待 --timeout 秒（默认 30）
#   - 不读取/写入 token/secret/config 内容
#   - 目录 0700 / 文件 0600（尽力；不支持平台降级）

set -euo pipefail

# ── 配置 ──

CONTROL_DIR="${HOME}/.starcoplay/control"
REQUEST_FILE="${CONTROL_DIR}/reload.request.json"
RESULT_FILE="${CONTROL_DIR}/reload.result.json"
LOCK_DIR="${CONTROL_DIR}/reload.lock"

TIMEOUT=30
if [[ "${1:-}" == "--timeout" ]]; then
    TIMEOUT="${2:-30}"
fi

# ── 依赖检测 ──

fail() { echo "[request-config-reload] $*" >&2; exit 3; }

command -v mkdir  &>/dev/null || fail "mkdir not found"
command -v mv     &>/dev/null || fail "mv not found"
command -v cat    &>/dev/null || fail "cat not found"

# optional but preferred
HAS_JQ=false
command -v jq &>/dev/null && HAS_JQ=true

# ── 目录初始化 ──

mkdir -p "${CONTROL_DIR}"
chmod 0700 "${CONTROL_DIR}" 2>/dev/null || true

# 单发布者锁：请求和结果各只有一个固定槽位，必须显式串行化，避免并发覆盖。
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    fail "another reload request is active (lock: ${LOCK_DIR})"
fi
TMP_REQUEST=""
cleanup() {
    [[ -z "${TMP_REQUEST}" ]] || rm -f "${TMP_REQUEST}"
    rmdir "${LOCK_DIR}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── 构建请求 ──

REQUEST_ID="reload-$(date +%s)-$(od -A n -t x4 -N 4 /dev/urandom 2>/dev/null | tr -d ' ' || echo "$$")"
REQUESTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

REQUEST_JSON=$(cat <<EOF
{
  "request_id": "${REQUEST_ID}",
  "requested_at": "${REQUESTED_AT}"
}
EOF
)

# ── 原子发布 ──

TMP_REQUEST="${REQUEST_FILE}.tmp-$$"
echo "${REQUEST_JSON}" > "${TMP_REQUEST}"
chmod 0600 "${TMP_REQUEST}" 2>/dev/null || true
mv "${TMP_REQUEST}" "${REQUEST_FILE}"

echo "[request-config-reload] 请求已发布: ${REQUEST_ID}" >&2

# ── 等待结果 ──

ELAPSED=0
INTERVAL=1

while [[ ${ELAPSED} -lt ${TIMEOUT} ]]; do
    if [[ -f "${RESULT_FILE}" ]]; then
        # 原子读取：先 rename 到临时文件再读（防止读到半写内容）
        TMP_RESULT="${RESULT_FILE}.read-$$"
        if mv "${RESULT_FILE}" "${TMP_RESULT}" 2>/dev/null; then
            RESULT_JSON=$(cat "${TMP_RESULT}")
            rm -f "${TMP_RESULT}"

            # 提取 outcome 和 detail（优先 jq，回退到 grep/sed）
            if ${HAS_JQ}; then
                OUTCOME=$(echo "${RESULT_JSON}" | jq -r '.outcome // ""')
                DETAIL=$(echo "${RESULT_JSON}" | jq -r '.detail // ""')
                RESULT_ID=$(echo "${RESULT_JSON}" | jq -r '.request_id // ""')
            else
                OUTCOME=$(echo "${RESULT_JSON}" | grep -o '"outcome"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"[[:space:]]*$/\1/')
                DETAIL=$(echo "${RESULT_JSON}" | grep -o '"detail"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"[[:space:]]*$/\1/')
                RESULT_ID=$(echo "${RESULT_JSON}" | grep -o '"request_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"[[:space:]]*$/\1/')
            fi

            # 仅匹配自己的 request_id
            if [[ "${RESULT_ID}" != "${REQUEST_ID}" ]]; then
                echo "[request-config-reload] 忽略不匹配的 request_id: ${RESULT_ID}" >&2
                continue
            fi

            echo "[request-config-reload] 结果: outcome=${OUTCOME} detail=${DETAIL}" >&2

            case "${OUTCOME}" in
                succeeded)
                    exit 0
                    ;;
                refused_single_flight|refused_busy)
                    exit 1
                    ;;
                config_read_failed|bridge_rebuild_failed|unknown)
                    exit 2
                    ;;
                *)
                    echo "[request-config-reload] 未知 outcome: ${OUTCOME}" >&2
                    exit 2
                    ;;
            esac
        fi
    fi

    sleep "${INTERVAL}"
    ELAPSED=$((ELAPSED + INTERVAL))
done

echo "[request-config-reload] 超时 (${TIMEOUT}s): 未收到匹配 ${REQUEST_ID} 的结果" >&2
exit 1
