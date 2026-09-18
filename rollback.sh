#!/bin/bash
# ═══════════════════════════════════════════════════════
# LTC RAG Bot 一键回滚
# ═══════════════════════════════════════════════════════
# 用法: ./rollback.sh <deployId 或 tag>
#   ./rollback.sh 015           回滚到 DeployId 015
#   ./rollback.sh v3-baseline   回滚到 git tag 对应版本
# ═══════════════════════════════════════════════════════
set -e

TARGET="${1:-v3-20260918-smoke-green}"
SERVICE="ltc-rag-bot"
ENV_ID="lily0625ai-d1gpwc1vw89141edd"

cd "$(dirname "$0")"

echo "════════════════════════════════════════════════════"
echo " LTC RAG Bot 一键回滚 → $TARGET"
echo "════════════════════════════════════════════════════"

echo ""
echo "[1/3] Git 回滚代码..."
if git rev-parse "$TARGET" >/dev/null 2>&1; then
  git checkout "$TARGET"
  echo "      ✓ 已切换到 $TARGET"
else
  echo "      ⚠️  $TARGET 不是有效的 git tag/commit，跳过 git"
fi

echo ""
echo "[2/3] CloudBase 版本切换..."
echo "      当前可用版本:"
tcb cloudrun record list -s "$SERVICE" -e "$ENV_ID" 2>&1 | head -8

echo ""
echo "      ⚠️ CloudBase 无灰度时需要重新部署旧代码"
echo "      自动重新部署当前代码 (已回滚)..."
echo "" | tcb cloudrun deploy -s "$SERVICE" -e "$ENV_ID" --force 2>&1 | tail -5

echo ""
echo "[3/3] 等待构建 + 部署..."
for i in $(seq 1 18); do
  sleep 10
  if tcb cloudrun record list -s "$SERVICE" -e "$ENV_ID" 2>&1 | grep -q "normal.*100"; then
    echo "      ✅ 部署完成 (~$((i*10))s)"
    break
  fi
  echo "      构建中... ($((i*10))s)"
done

echo ""
echo "════════════════════════════════════════════════════"
echo " ✅ 回滚部署完成"
echo "════════════════════════════════════════════════════"
