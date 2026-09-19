#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# LTC RAG Bot 一键回滚 · v2（2026-09-18 修复「假回滚」）
#
# 用法:
#   bash rollback.sh                                  # 回滚到默认基线 handoff-20260918
#   bash rollback.sh opt-20260918-cachefix            # 回滚到指定 git tag / commit
#   bash rollback.sh --env-id <envId> --service ltc-rag-bot
#   bash rollback.sh --url https://xxx/              # 回滚后自动探活 /health
#   bash rollback.sh --deploy-id 015 --allow-deploy-id-only
#
# v1 的致命问题（本次修复）：
#   默认目标 v3-20260918-smoke-green 在本仓**不存在** → git rev-parse 失败后
#   只打印一句警告就继续 `tcb cloudrun deploy` → 部署的仍是**当前新代码**，
#   最后还打印「✅ 回滚部署完成」。运维以为回滚了，其实没有 —— 假回滚。
#
# v2 关键行为：
#   1. git 目标无效 → 直接中止（exit 2），绝不继续部署
#   2. 工作区有未提交改动 → 拒绝执行（exit 3），除非 --force-dirty
#   3. 显式 detached 检出，并提示如何回到分支
#   4. 回滚后必须验证：部署记录 normal 100% + HTTP 探活
#
# 退出码: 0 成功 / 2 目标无效 / 3 工作区脏 / 4 部署超时 / 5 探活失败
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

# ── 自我保护：本脚本在第 [1/5] 步会被 git checkout 覆盖 ─────────────
# code/rollback.sh 是 git 跟踪的文件。回滚到旧 tag 时它自己也被换掉，
# 而 bash 是「边读边执行」脚本的 → 运行中脚本被改写会导致执行错乱
# （可能读到新旧混合内容，静默跑错逻辑）。故首次运行先把自身复制到
# 临时文件，再 exec 副本继续跑，使运行中的代码与磁盘解耦。
if [ "${LTC_RB_REEXEC:-0}" != "1" ]; then
  _wd="$(cd "$(dirname "$0")" && pwd)"
  _self="$(mktemp "${TMPDIR:-/tmp}/ltc-rollback-XXXXXX" 2>/dev/null || true)"
  if [ -n "${_self}" ] && cp "$0" "${_self}" && chmod +x "${_self}"; then
    LTC_RB_REEXEC=1 LTC_RB_WORKDIR="${_wd}" LTC_RB_SELF="${_self}" exec bash "${_self}" "$@"
  else
    echo "⚠️  无法创建自保护副本，继续就地执行（git checkout 期间有风险）"
  fi
fi
SELF_COPY_PATH="${LTC_RB_SELF:-}"
[ -n "${SELF_COPY_PATH}" ] && trap 'rm -f "${SELF_COPY_PATH}" 2>/dev/null' EXIT

cd "${LTC_RB_WORKDIR:-$(cd "$(dirname "$0")" && pwd)}"

# ── 默认值（可用命令行覆盖）────────────────────────────────────
DEFAULT_TARGET="handoff-20260918"          # 交接原始基线 = 完全还原 Trae 交付状态
SERVICE="ltc-rag-bot"
ENV_ID="lily0625ai-d1gpwc1vw89141edd"   # ⚠️ 必须与 cloudbaserc.json 的 envId 逐字一致（演练台有断言）
TARGET=""
TARGET_GIVEN=0
RESOLVED=""
DEPLOY_ID=""
URL=""
ALLOW_DEPLOY_ID_ONLY=0
FORCE_DIRTY=0
MAX_POLL="${MAX_POLL:-18}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"

die() { echo; echo "❌ $1"; echo "   中止，未触碰线上。"; exit "$2"; }

# ── 参数解析 ────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --env-id)    ENV_ID="${2:-}"; shift 2 ;;
    --service)   SERVICE="${2:-}"; shift 2 ;;
    --url)       URL="${2:-}"; shift 2 ;;
    --deploy-id) DEPLOY_ID="${2:-}"; shift 2 ;;
    --allow-deploy-id-only) ALLOW_DEPLOY_ID_ONLY=1; shift ;;
    --force-dirty) FORCE_DIRTY=1; shift ;;
    --target)    TARGET="${2:-}"; TARGET_GIVEN=1; shift 2 ;;
    -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
    -*)          die "未知参数: $1（--help 看用法）" 1 ;;
    *)           TARGET="$1"; TARGET_GIVEN=1; shift ;;
  esac
done

[ -n "${TARGET}" ] || TARGET="${DEFAULT_TARGET}"

echo "════════════════════════════════════════════════════════"
echo " LTC RAG Bot 一键回滚"
echo "════════════════════════════════════════════════════════"
echo " 目标    : ${TARGET}"
echo " 服务    : ${SERVICE}"
echo " 环境    : ${ENV_ID}"
echo

# ── 0. 前置检查 ─────────────────────────────────────────────────
echo "[0/5] 前置检查"

if ! command -v tcb >/dev/null 2>&1; then
  die "未找到 tcb CLI。安装：npm i -g @cloudbase/cli && tcb login" 1
fi

if [ -n "${DEPLOY_ID}" ] && [ "${TARGET_GIVEN}" = "0" ]; then
  # 仅指定 DeployId 的情形
  if [ "${ALLOW_DEPLOY_ID_ONLY}" != "1" ]; then
    echo "      ⚠️  只给 --deploy-id 无法回滚代码：CloudBase 无灰度时会用**当前代码**重新构建。"
    echo "          要真的回滚，请同时给 git 目标，例如："
    echo "            bash rollback.sh opt-20260918-cachefix"
    echo "          确认只想切版本号，加 --allow-deploy-id-only。"
    die "拒绝执行『只有 DeployId 的回滚』（会变成假回滚）" 2
  fi
  echo "      ⚠️  --allow-deploy-id-only 已开启：跳过 git 回滚，仅重新触发部署"
else
  # 1. git 目标必须有效 —— 无效就中止，绝不继续
  if ! git rev-parse --verify --quiet "${TARGET}^{commit}" >/dev/null; then
    echo "      现有可用目标："
    git tag -l | sed 's/^/        - /'
    die "git 目标无效：${TARGET}（不是本仓的 tag/commit）" 2
  fi
  RESOLVED="$(git rev-parse --short "${TARGET}^{commit}")"
  echo "      ✅ git 目标有效：${TARGET} → ${RESOLVED}"

  # 2. 工作区必须干净 —— 否则检出会中途失败，留下半残状态
  if [ -n "$(git status --porcelain)" ]; then
    if [ "${FORCE_DIRTY}" = "1" ]; then
      echo "      ⚠️  工作区有未提交改动，--force-dirty 已开启，继续（改动可能丢失）"
    else
      echo "      未提交改动："
      git status --porcelain | sed 's/^/        /'
      die "工作区不干净，回滚前请先 commit 或 stash（或显式加 --force-dirty）" 3
    fi
  else
    echo "      ✅ 工作区干净"
  fi
fi

# ── 1. Git 回滚代码 ─────────────────────────────────────────────
echo
echo "[1/5] Git 回滚代码"
ORIG_REF="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo 'detached')"
if [ "${ALLOW_DEPLOY_ID_ONLY}" = "1" ] && [ -n "${DEPLOY_ID}" ]; then
  echo "      ⏭️  已跳过（--allow-deploy-id-only）"
else
  git checkout --detach "${TARGET}" >/dev/null 2>&1 \
    && echo "      ✅ 已 detached 检出 ${TARGET}（${RESOLVED}）" \
    || die "git checkout ${TARGET} 失败" 1
  echo "      ℹ️  当前为 detached HEAD；回滚结束后用 git checkout ${ORIG_REF} 回到分支"
fi

# ── 2. 查看线上可用版本 ─────────────────────────────────────────
echo
echo "[2/5] 查看 CloudBase 历史版本"
tcb cloudrun record list -s "${SERVICE}" -e "${ENV_ID}" 2>&1 | head -8 | sed 's/^/      /'
[ -n "${DEPLOY_ID}" ] && echo "      ℹ️  指定 DeployId：${DEPLOY_ID}"

# ── 3. 重新部署（CloudBase 无灰度时必须重构建）─────────────────
echo
echo "[3/5] 重新部署被回滚的代码"
echo "      （CloudBase 云托管无灰度切换，回滚 = 用旧代码重新构建部署）"
printf '\n' | tcb cloudrun deploy -s "${SERVICE}" -e "${ENV_ID}" --force 2>&1 | tail -5 | sed 's/^/      /'

# ── 4. 等待部署就绪 ─────────────────────────────────────────────
echo
echo "[4/5] 等待构建 + 部署就绪"
READY=0
i=1
while [ "${i}" -le "${MAX_POLL}" ]; do
  if tcb cloudrun record list -s "${SERVICE}" -e "${ENV_ID}" 2>&1 | grep -q "normal.*100"; then
    echo "      ✅ 部署就绪（约 $((i * POLL_INTERVAL))s）"
    READY=1
    break
  fi
  echo "      构建中... ($((i * POLL_INTERVAL))s)"
  sleep "${POLL_INTERVAL}"
  i=$((i + 1))
done
[ "${READY}" = "1" ] || die "等待部署超时（$((MAX_POLL * POLL_INTERVAL))s）—— 去控制台看构建日志" 4

# ── 5. 验证 ────────────────────────────────────────────────────
echo
echo "[5/5] 回滚验证"
if [ -n "${URL}" ]; then
  BASE="${URL%/}"
  CODE="$(curl -s -o /tmp/.rb_health -w '%{http_code}' --max-time 10 "${BASE}/health" || echo 000)"
  if [ "${CODE}" = "200" ]; then
    echo "      ✅ /health HTTP 200 · $(head -c 160 /tmp/.rb_health)"
  else
    die "/health 探活失败（HTTP ${CODE}）—— 回滚未生效，考虑继续回退更早版本" 5
  fi
  if [ -f .env ]; then
    KEY="$(grep -E '^API_KEY=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
    if [ -n "${KEY}" ]; then
      KC="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "X-API-Key: ${KEY}" "${BASE}/kb" || echo 000)"
      case "${KC}" in
        200) echo "      ✅ /kb 带鉴权 HTTP 200（生产鉴权链路正常）" ;;
        401) echo "      ❌ /kb HTTP 401：容器里 API_KEY 与本地不一致，检查 CloudBase 环境变量" ;;
        *)   echo "      ⚠️  /kb HTTP ${KC}（未知，需人工确认）" ;;
      esac
    fi
  fi
else
  echo "      ⚠️  未给 --url，跳过探活。手工验证："
  echo "          curl -s <你的服务URL>/health"
  echo "          curl -s -H \"X-API-Key: <生产KEY>\" <你的服务URL>/kb"
fi

echo
echo "════════════════════════════════════════════════════════"
if [ -n "${RESOLVED}" ]; then
  echo " ✅ 回滚流程完成（代码 ${RESOLVED} 已重新部署）"
else
  echo " ✅ 回滚流程完成（仅切 CloudBase 版本，代码未变）"
fi
if [ "${ALLOW_DEPLOY_ID_ONLY}" != "1" ] && [ -n "${ORIG_REF}" ]; then
  echo " ℹ️  收尾：确认无误后用 git checkout ${ORIG_REF} 回到分支"
fi
echo "════════════════════════════════════════════════════════"
