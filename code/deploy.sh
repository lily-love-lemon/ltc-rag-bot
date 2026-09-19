#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# LTC RAG Bot → CloudBase 云托管 部署脚本 · v2（2026-09-18）
#
# 用法:
#   bash deploy.sh                        # 交互式：列出环境让你选
#   bash deploy.sh -e <envId>             # 指定环境，可用于无人值守/CI
#   bash deploy.sh -e <envId> --yes       # 全自动，不询问
#   bash deploy.sh --install              # 缺 tcb 时自动 npm i -g
#   bash deploy.sh --preflight-only       # 只做前置检查，不部署
#
# v1 的问题（本次修复）：
#   1. 部署命令写成 `tcb cloudrun deploy --envId X --servicePath .`，
#      与 docs/CloudBase约束.md 实测正确形式 `-s <service> -e <envId> --force`
#      不一致 —— 缺 --force 会卡在「是否灰度」交互提示上（限制 5）。
#   2. 结尾文案称「环境变量已在 cloudbaserc.json 里配置」，但该文件
#      envParams 是 `{}`，且 .env 被 .dockerignore 排除 → 线上根本没注入密钥。
#   3. `read -p` 在 `set -e` 下遇到 stdin 关闭会直接退出，无法无人值守。
#
# 退出码: 0 成功 / 1 环境或依赖不满足 / 2 前置检查未通过
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")"

SERVICE="ltc-rag-bot"
ENV_ID=""
YES=0
AUTO_INSTALL=0
PREFLIGHT_ONLY=0
CONFIG="cloudbaserc.json"

# ── 参数 ────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    -e|--env-id)   ENV_ID="${2:-}"; shift 2 ;;
    -s|--service)  SERVICE="${2:-}"; shift 2 ;;
    -y|--yes)      YES=1; shift ;;
    --install)     AUTO_INSTALL=1; shift ;;
    --preflight-only) PREFLIGHT_ONLY=1; shift ;;
    -h|--help)     sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "未知参数: $1（--help 看用法）"; exit 1 ;;
  esac
done

echo "🚀 LTC RAG Bot → CloudBase 云托管"
echo "────────────────────────────────"

# ── 0. 前置检查 ─────────────────────────────────────────────────
echo "🔍 [0/4] 前置检查"
WARN=0
warn() { WARN=$((WARN + 1)); echo "      ⚠️  $1"; }
okp()  { echo "      ✅ $1"; }

# 0.1 必需环境变量（线上容器要靠它跑 LLM，缺失 = RAG 退化成模板拼接）
# 只解析所需的键，不 source .env —— 避免执行 .env 里的任意内容
envval() {
  grep -E "^${1}=" .env 2>/dev/null | head -1 | cut -d= -f2- \
    | sed -e 's/^[[:space:]]*//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" \
    | tr -d '\r' || true
}

if [ -f .env ]; then
  okp ".env 已加载（仅读取所需键）"
else
  warn "缺 code/.env —— 线上会没有 LLM 密钥（下面第 3 步会列出需配置的变量）"
fi

if [ -n "$(envval ZHIPUAI_API_KEY)$(envval ZHIPU_API_KEY)$(envval SILICONFLOW_API_KEY)$(envval SF_API_KEY)" ]; then
  okp "LLM 密钥存在（智谱/硅基流动）"
else
  warn "无任何 LLM 密钥：线上 call_llm 会一路跌到模板拼接（答不出自然语言）"
fi

LOCAL_API_KEY="$(envval API_KEY)"
if [ -n "${LOCAL_API_KEY}" ] && [ "${LOCAL_API_KEY}" != "dev-only-key-change-in-prod" ]; then
  okp "API_KEY 已设为非默认值"
else
  warn "API_KEY 未设或仍是默认值 dev-only-key-change-in-prod"
  warn "  → 线上将用公开的默认 Key 鉴权，任何人可调你的接口"
fi

# 0.2 代码状态
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
  DIRTY="$(git status --porcelain | wc -l | tr -d ' ')"
  if [ "${DIRTY}" = "0" ]; then
    okp "git 工作区干净（$(git rev-parse --short HEAD)）"
  else
    warn "git 工作区有 ${DIRTY} 处未提交改动 —— 会被原样构建上线"
  fi
else
  warn "不在 git 仓库内，无法核对部署版本"
fi

# 0.3 关键文件
for f in app.py Dockerfile requirements.txt portal/index.html prompts/fabe.txt; do
  [ -e "${f}" ] && okp "存在 ${f}" || { warn "缺失 ${f}"; }
done

# 0.4 环境变量配置现状（v1 的假陈述在此纠正）
echo
echo "      ── 线上环境变量现状 ──"
if command -v python3 >/dev/null 2>&1 && [ -f "${CONFIG}" ]; then
  ENVP="$(python3 -c "import json;d=json.load(open('${CONFIG}'));print(len(d.get('cloudrun',{}).get('envParams',{})))" 2>/dev/null || echo '?')"
  echo "      ${CONFIG} 的 cloudrun.envParams 键数 = ${ENVP}"
  if [ "${ENVP}" = "0" ]; then
    warn "envParams 为空 → 容器里没有 API_KEY / ZHIPUAI_API_KEY"
    echo "         ❌ 不要再往 ${CONFIG} 里写密钥：该文件**已入 git**，写了等于泄密。"
    echo "         ✅ 正确做法（二选一）："
    echo "            ① 控制台：云托管 → ${SERVICE} → 环境变量 → 逐条添加"
    echo "            ② CLI  : tcb cloudrun deploy -s ${SERVICE} -e <envId> --force，按提示配置"
    echo "         需配置的变量清单见本脚本结尾。"
  else
    okp "envParams 已配置 ${ENVP} 个键"
  fi
fi

echo
if [ "${WARN}" -gt 0 ]; then
  echo "      ⚠️  前置检查有 ${WARN} 项警告（不阻塞部署，但请知悉）"
fi

if [ "${PREFLIGHT_ONLY}" = "1" ]; then
  echo
  echo "✅ --preflight-only：检查完毕，未部署。"
  exit 0
fi

# ── 1. tcb CLI ──────────────────────────────────────────────────
echo "📦 [1/4] tcb CLI"
if ! command -v tcb >/dev/null 2>&1; then
  if [ "${AUTO_INSTALL}" = "1" ]; then
    echo "      自动安装 @cloudbase/cli ..."
    npm install -g @cloudbase/cli
  else
    echo "      ❌ 未找到 tcb CLI。请先执行："
    echo "           npm i -g @cloudbase/cli && tcb login"
    echo "         或加 --install 让本脚本代装。"
    exit 1
  fi
fi
okp "$(tcb --version 2>/dev/null | head -1 || echo 'tcb 就绪')"

# ── 2. 登录 + 选环境 ────────────────────────────────────────────
echo "🔐 [2/4] 登录与环境"
if ! tcb login status >/dev/null 2>&1; then
  echo "      请在浏览器中完成登录 ..."
  tcb login
fi
okp "已登录"

if [ -z "${ENV_ID}" ]; then
  echo "🌍 可用环境:"
  tcb env:list | sed 's/^/      /'
  if [ "${YES}" = "1" ] || [ ! -t 0 ]; then
    # 非交互（CI / 管道 / stdin 关闭）：不再用 read 卡死，直接取 envId
    ENV_ID="$(tcb env:list --json 2>/dev/null \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["envId"])' 2>/dev/null || true)"
    [ -n "${ENV_ID}" ] && echo "      ℹ️  非交互模式，自动取第一个环境：${ENV_ID}"
  else
    read -r -p "👉 输入环境 ID（回车自动选第一个）: " ENV_ID || true
    if [ -z "${ENV_ID}" ]; then
      ENV_ID="$(tcb env:list --json 2>/dev/null \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["envId"])' 2>/dev/null || true)"
    fi
  fi
fi

if [ -z "${ENV_ID}" ]; then
  echo "      ❌ 未能确定环境 ID。用 -e <envId> 显式指定，或先 tcb env:list 看环境列表。"
  exit 1
fi
okp "使用环境: ${ENV_ID}"

# ── 3. 部署 ─────────────────────────────────────────────────────
echo "📦 [3/4] 构建并部署"
echo "      命令: tcb cloudrun deploy -s ${SERVICE} -e ${ENV_ID} --force"
echo "      （--force 跳过确认 + 空输入自动选『非灰度』，见 docs/CloudBase约束.md 限制 5）"
printf '\n' | tcb cloudrun deploy -s "${SERVICE}" -e "${ENV_ID}" --force

# ── 4. 结果 ─────────────────────────────────────────────────────
echo
echo "✅ [4/4] 部署完成"
echo "👉 到 CloudBase 控制台为 ${SERVICE} 开启 COS 挂载（路径 /mnt/chroma）"
echo "👉 服务 URL 在控制台可见；拿到后验证："
echo "     curl -s <URL>/health"
echo "     curl -s -H \"X-API-Key: <生产KEY>\" <URL>/kb"
echo
echo "⚠️  必须在控制台为该服务配置以下环境变量（cloudbaserc.json 里刻意留空，避免密钥入库）："
echo "     API_KEY              生产鉴权 Key（不配则退化为公开默认值）"
echo "     ZHIPUAI_API_KEY       智谱密钥（LLM 主通道）"
echo "     ZHIPUAI_MODEL         建议 glm-4-flash"
echo "     FEISHU_APP_ID         飞书机器人（可选）"
echo "     FEISHU_APP_SECRET     飞书机器人（可选）"
echo "     API_KEY_IN_HTML       true 时把 Key 注入 Portal HTML（同源部署用；注意任何人可读）"
echo "     CHROMA_PATH           默认 /mnt/chroma，COS 挂载失败时自动回落本地"
