#!/bin/bash
# ─────────────────────────────────────────────────────────────
# FDE · 基线固化（Phase 0 必做）
# 改任何代码之前运行此脚本
# ─────────────────────────────────────────────────────────────
set -e

TAG="${1:-baseline-$(date +%Y%m%d)}"
SERVICE="${2:-ltc-rag-bot}"
ENV_ID="${3:-lily0625ai-d1gpwc1vw89141edd}"

cd "$(dirname "$0")/../.."

echo "════════════════════════════════════════════════"
echo " FDE · 基线固化"
echo " Git tag:  $TAG"
echo " Service:  $SERVICE"
echo " Env:      $ENV_ID"
echo "════════════════════════════════════════════════"

# 1. git config 检查
echo ""
echo "[1/3] Git config..."
if ! git config user.email > /dev/null 2>&1; then
  git config user.email "dev@local"
  echo "      ⚠️ user.email 未设，临时设为 dev@local"
fi
if ! git config user.name > /dev/null 2>&1; then
  git config user.name "Dev"
fi
echo "      ✅ $(git config user.email) / $(git config user.name)"

# 2. CloudBase 版本记录
echo ""
echo "[2/3] 记录 CloudBase 当前版本..."
RECORD=$(tcb cloudrun record list -s "$SERVICE" -e "$ENV_ID" 2>&1 | head -5)
echo "$RECORD"
RUN_ID=$(echo "$RECORD" | grep -oE 'multi_tenant_[A-Za-z0-9]+' | head -1)
DEPLOY_ID=$(echo "$RECORD" | grep -oE '[0-9]{3}' | head -1)
echo "      RunId: $RUN_ID"
echo "      DeployId: $DEPLOY_ID"

# 3. git tag
echo ""
echo "[3/3] Git tag..."
git add -A
git commit -m "baseline: $TAG (DeployId $DEPLOY_ID, RunId $RUN_ID)" 2>/dev/null || echo "      (无新变更)"
git tag "$TAG"
echo "      ✅ tagged $TAG"

# 4. 写入 rollback.sh
echo ""
echo "[4/4] 生成 rollback.sh..."
cat > rollback.sh << EOSCRIPT
#!/bin/bash
# ═══════════════════════════════════════════════════
# 一键回滚脚本
# 基线: $TAG  (DeployId $DEPLOY_ID, RunId $RUN_ID)
# ═══════════════════════════════════════════════════
set -e
echo "回滚到 $TAG (RunId: $RUN_ID)"
git checkout $TAG
tcb cloudrun rollback -s $SERVICE -e $ENV_ID --versionName $RUN_ID
echo "等待 30s..."
sleep 30
echo "✅ 回滚完成"
EOSCRIPT
chmod +x rollback.sh
echo "      ✅ rollback.sh 已生成"

echo ""
echo "════════════════════════════════════════════════"
echo " ✅ 基线固化完成！"
echo " 现在可以安全改代码了。"
echo "════════════════════════════════════════════════"
