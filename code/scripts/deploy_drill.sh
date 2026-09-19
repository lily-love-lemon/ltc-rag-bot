#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# deploy_drill.sh —— 部署/回滚脚本「离线演练台」
#
# 目的：不接 CloudBase、不装 tcb CLI、不动线上，
#       用 stub 版 tcb 把 deploy.sh / rollback.sh 的逻辑真跑一遍，
#       记录它们实际发出的命令，作为"脚本逻辑已验证"的证据。
#
# 原理：git clone 临时副本（带全 tag）→ 把工作区的 deploy.sh / rollback.sh
#       覆盖进去并提交 → PATH 前置 stub tcb → 按 6 个场景跑并断言。
#
# 用法：
#   bash scripts/deploy_drill.sh            # 演练当前工作区版本
#   bash scripts/deploy_drill.sh --keep     # 保留临时目录便于排查
#
# 退出码：0 = 全部断言通过；1 = 有断言失败
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # = code/
REPO_DIR="$(cd "${SRC_DIR}/.." && pwd)"         # = 项目根（含 .git）
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

DRILL="$(mktemp -d "${TMPDIR:-/tmp}/ltc-drill-XXXXXX")"
CLONE="${DRILL}/repo"
BIN="${DRILL}/bin"
LOGBOOK="${DRILL}/tcb_calls.log"
mkdir -p "${BIN}"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }
hdr() { echo; echo "── $1 ──"; }

cleanup() {
  if [ "${KEEP}" = "1" ]; then echo; echo "临时目录保留：${DRILL}"; else rm -rf "${DRILL}"; fi
}
trap cleanup EXIT

echo "══════════════════════════════════════════════════════"
echo " 部署/回滚 离线演练台"
echo "══════════════════════════════════════════════════════"
echo "源仓库 : ${REPO_DIR}"
echo "演练场 : ${DRILL}"

# ── 1. 造演练场：克隆真仓库（带 tag）──────────────────────────
hdr "1. 准备演练场"
git clone -q --no-hardlinks "${REPO_DIR}" "${CLONE}" 2>/dev/null \
  && ok "克隆仓库（含 $(git -C "${CLONE}" tag -l | wc -l | tr -d ' ') 个 tag）" \
  || { bad "克隆失败"; exit 1; }
git -C "${CLONE}" config user.email "drill@local"
git -C "${CLONE}" config user.name  "drill"
git -C "${CLONE}" checkout -q master 2>/dev/null || true

# 用工作区当前版本覆盖，并提交（否则 rollback.sh 的脏树检查会拒绝执行）
cp "${SRC_DIR}/deploy.sh"   "${CLONE}/code/deploy.sh"
cp "${SRC_DIR}/rollback.sh" "${CLONE}/code/rollback.sh"
git -C "${CLONE}" add -A >/dev/null 2>&1
git -C "${CLONE}" commit -q -m "drill: 覆盖为工作区版本" >/dev/null 2>&1

# 造一个可回滚目标 tag，钉在「交接基线」上
# —— 这样回滚成功时工作区文件会真的变（含 rollback.sh 自己）
git -C "${CLONE}" tag -f "v3-drill-baseline" handoff-20260918 >/dev/null 2>&1 \
  || git -C "${CLONE}" tag -f "v3-drill-baseline" >/dev/null 2>&1
cp "${SRC_DIR}/rollback.sh" "${DRILL}/rollback.worktree.sh"   # 留一份"新版本"用于比对
ok "演练 tag 就绪：$(git -C "${CLONE}" tag -l | tr '\n' ' ')"

# ── 2. 造 stub tcb ────────────────────────────────────────────
hdr "2. 安装 stub tcb（记录调用参数，不联网）"
cat > "${BIN}/tcb" <<'STUB'
#!/usr/bin/env bash
# stub tcb：把每次调用原样记录，并按需返回可预期的输出
printf '[tcb] %s\n' "$*" >> "${DRILL_LOGBOOK:-/dev/null}"
case "$*" in
  "version"*|"--version"*) echo "stub-tcb 0.0.1"; exit 0 ;;
  "login status")        exit 0 ;;
  "login"*)              echo "[stub] 模拟登录成功"; exit 0 ;;
  "env:list --json"*)    echo '[{"envId":"drill-env-0001","alias":"drill"}]'; exit 0 ;;
  "env:list"*)           echo "drill-env-0001    drill"; exit 0 ;;
  "cloudrun deploy"*)    echo "[stub] cloudrun deploy 已受理"; exit 0 ;;
  "cloudrun record"*)    echo "RunId           Status"; echo "drill-run-01    normal 100%"; exit 0 ;;
  *)                     echo "[stub] $*"; exit 0 ;;
esac
STUB
chmod +x "${BIN}/tcb"
export DRILL_LOGBOOK="${LOGBOOK}"
export PATH="${BIN}:${PATH}"
ok "stub tcb 就位：$(command -v tcb)"

reset_clone() {
  git -C "${CLONE}" checkout -q -f master 2>/dev/null || true
  git -C "${CLONE}" clean -qfd 2>/dev/null || true
}

# ── 3. rollback.sh 场景 ───────────────────────────────────────
hdr "3. 演练 rollback.sh（原默认目标 v3-20260918-smoke-green 已不存在）"
reset_clone
: > "${LOGBOOK}"
RB1="${DRILL}/rb_invalid.log"
(cd "${CLONE}/code" && POLL_INTERVAL=1 bash rollback.sh v3-20260918-smoke-green) > "${RB1}" 2>&1
RC1=$?
echo "     退出码=${RC1}"
grep -E "中止|跳过|cloudrun deploy|回滚流程完成" "${RB1}" | sed 's/^/       /' || true
# 断言 A：目标无效必须中止（exit 2），且绝不发起部署
if [ "${RC1}" -eq 2 ] && ! grep -q "cloudrun deploy" "${LOGBOOK}"; then
  ok "无效目标 → 中止(exit 2)，未发起任何部署（不再假回滚）"
else
  bad "无效目标未正确中止（exit=${RC1}，部署调用见下）: $(cat "${LOGBOOK}")"
fi

hdr "4. 演练 rollback.sh（有效 tag v3-drill-baseline）"
reset_clone
: > "${LOGBOOK}"
RB2="${DRILL}/rb_valid.log"
(cd "${CLONE}/code" && POLL_INTERVAL=1 bash rollback.sh v3-drill-baseline) > "${RB2}" 2>&1
RC2=$?
echo "     退出码=${RC2}"
grep -E "git 目标有效|detached 检出|部署就绪|回滚流程完成|无法创建自保护" "${RB2}" | sed 's/^/       /' || true
echo "     tcb 调用："; sed 's/^/       /' "${LOGBOOK}"
[ "${RC2}" -eq 0 ] && ok "有效 tag → 流程跑完（exit 0）" || bad "有效 tag 流程失败（exit=${RC2}）"

# 断言 B：回滚要落到文件上（app.py 回到旧版）
if diff -q "${CLONE}/code/app.py" <(git -C "${CLONE}" show master:code/app.py) >/dev/null 2>&1; then
  bad "app.py 仍与 master 一致 → 回滚没落到文件上"
else
  ok "app.py 已回到旧版（文件级验证）"
fi

# 断言 C：自查「自保护副本」是否生效 —— 运行中脚本文件被换掉，仍应正常跑完
if grep -q "无法创建自保护副本" "${RB2}"; then
  bad "自保护副本创建失败（git checkout 期间有执行错乱风险）"
elif diff -q "${CLONE}/code/rollback.sh" "${DRILL}/rollback.worktree.sh" >/dev/null 2>&1; then
  bad "rollback.sh 未被换回旧版 —— 未构成自覆盖场景，断言无效"
else
  ok "运行时 rollback.sh 自身已被 git 换掉，流程仍正常跑完 → 自保护生效"
fi

# 断言 D：--deploy-id-only 必须被拒（否则也是假回滚）
hdr "5. 演练 rollback.sh --deploy-id 015（应被拒）"
reset_clone
: > "${LOGBOOK}"
RB3="${DRILL}/rb_deployid.log"
(cd "${CLONE}/code" && POLL_INTERVAL=1 bash rollback.sh --deploy-id 015) > "${RB3}" 2>&1
RC3=$?
echo "     退出码=${RC3}"
grep -E "拒绝|中止|无法回滚代码" "${RB3}" | sed 's/^/       /' || true
if [ "${RC3}" -eq 2 ] && ! grep -q "cloudrun deploy" "${LOGBOOK}"; then
  ok "仅给 DeployId → 拒绝执行(exit 2)，未发起部署"
else
  bad "仅给 DeployId 未被拒（exit=${RC3}）"
fi

# ── 6. deploy.sh 场景 ─────────────────────────────────────────
hdr "6. 演练 deploy.sh"

reset_clone
: > "${LOGBOOK}"
DP1="${DRILL}/deploy_noninteractive.log"
bash "${CLONE}/code/deploy.sh" --preflight-only > "${DP1}" 2>&1
echo "     --preflight-only 输出（前 24 行）："
head -24 "${DP1}" | sed 's/^/       /'
grep -q "cloudrun deploy" "${LOGBOOK}" && bad "preflight-only 竟然发起了部署" || ok "--preflight-only 只检查、不部署"

: > "${LOGBOOK}"
DP2="${DRILL}/deploy_noninteractive2.log"
# 非交互（CI / 无人值守）：stdin 关闭
bash "${CLONE}/code/deploy.sh" < /dev/null > "${DP2}" 2>&1
DP_RC=$?
echo
echo "     非交互退出码=${DP_RC}"
echo "     实际发出的 tcb 命令："; sed 's/^/       /' "${LOGBOOK}"
if [ "${DP_RC}" -eq 0 ]; then
  ok "非交互（stdin 关闭）跑通 → 可无人值守"
else
  bad "非交互退出码 ${DP_RC} ≠ 0 → 无法无人值守"
fi

# 断言 E：部署命令必须与 docs/CloudBase约束.md 实测形式一致
if grep -q "cloudrun deploy -s ltc-rag-bot -e drill-env-0001 --force" "${LOGBOOK}"; then
  ok "部署命令符合实测形式（-s <service> -e <env> --force）"
else
  bad "部署命令与 docs/CloudBase约束.md 实测形式不符（缺 --force 会卡灰度交互）"
fi

# 断言 F：不得再出现 --servicePath 与 -s 混用
if grep -q -- "--servicePath" "${LOGBOOK}"; then
  bad "仍在用 --servicePath（与 -s <serviceName> 语义混用）"
else
  ok "无 --servicePath 混用"
fi

# 断言 G：结尾必须给出线上环境变量配置清单（纠正 v1 的假陈述）
if grep -q "envParams 键数 = 0" "${DP1}" && grep -q "API_KEY              生产鉴权 Key" "${DP2}"; then
  ok "指出了 envParams 为空，并给出环境变量配置清单"
else
  bad "未正确提示线上环境变量缺失"
fi

# 断言 H：rollback.sh 里硬编码的 ENV_ID 必须与 cloudbaserc.json 逐字一致
# （手写少一个字符就会指向不存在的环境；stub 不校验参数合法性，只能静态比）
CFG_ENV="$(grep -o '"envId"[[:space:]]*:[[:space:]]*"[^"]*"' "${SRC_DIR}/cloudbaserc.json" 2>/dev/null \
  | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
RB_ENV="$(grep -o '^ENV_ID="[^"]*"' "${SRC_DIR}/rollback.sh" 2>/dev/null | head -1 | cut -d'"' -f2)"
if [ -n "${CFG_ENV}" ] && [ "${CFG_ENV}" = "${RB_ENV}" ]; then
  ok "rollback.sh 的 ENV_ID 与 cloudbaserc.json 一致（${CFG_ENV}）"
else
  bad "ENV_ID 不一致：cloudbaserc.json='${CFG_ENV}' vs rollback.sh='${RB_ENV}' → 回滚会指向不存在的环境"
fi

# 断言 I：deploy.sh 不得硬编码另一个 envId（与 cloudbaserc.json 冲突）
DP_ENV="$(grep -o 'lily0625ai-[a-z0-9]*' "${SRC_DIR}/deploy.sh" 2>/dev/null | head -1)"
if [ -z "${DP_ENV}" ]; then
  ok "deploy.sh 未硬编码 envId（强制 -e 或从 tcb env:list 取，无隐性冲突）"
elif [ "${DP_ENV}" = "${CFG_ENV}" ]; then
  ok "deploy.sh 引用的 envId 与 cloudbaserc.json 一致（${DP_ENV}）"
else
  bad "deploy.sh 硬编码了不一致的 envId：'${DP_ENV}' vs '${CFG_ENV}'"
fi

# ── 7. 汇总 ───────────────────────────────────────────────────
hdr "7. 结果"
echo "  断言通过 ${PASS} / 失败 ${FAIL}"
echo
if [ "${FAIL}" -eq 0 ]; then
  echo "✅ 演练全部通过"
  exit 0
else
  echo "❌ 演练有 ${FAIL} 项失败（详见上方）"
  [ "${KEEP}" = "1" ] || echo "   提示：加 --keep 可保留日志目录"
  exit 1
fi
