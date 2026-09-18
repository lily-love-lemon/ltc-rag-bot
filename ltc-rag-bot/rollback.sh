#!/bin/bash
# ═══════════════════════════════════════════════════════
# LTC RAG Bot V4 → V3 一键回滚脚本
# ═══════════════════════════════════════════════════════
# 回滚目标: git tag v3-20260918-smoke-green
# CloudBase 回滚版本: DeployId 015 (v3 稳定版)
# ═══════════════════════════════════════════════════════
set -e
echo "════════════════════════════════════════════════════"
echo " LTC RAG Bot 一键回滚"
echo " 回滚目标: v3-20260918-smoke-green (DeployId 015)"
echo "════════════════════════════════════════════════════"
cd "$(dirname "$0")"

echo ""
echo "[1/3] Git 回滚代码..."
git fetch origin --tags 2>/dev/null || true
git checkout v3-20260918-smoke-green
echo "      ✓ 代码已回滚到 v3 baseline"

echo ""
echo "[2/3] CloudBase 回滚到 DeployId 015..."
tcb cloudrun rollback \
  -s ltc-rag-bot \
  -e lily0625ai-d1gpwc1vw89141edd \
  --versionName multi_tenant_1x7PV6ZTf8QEDK
echo "      ✓ 已触发回滚"

echo ""
echo "[3/3] 等待服务就绪 (30s)..."
sleep 30

echo ""
echo "════════════════════════════════════════════════════"
echo " ✅ 回滚完成"
echo " 请手动验证: tcb cloudrun record list -s ltc-rag-bot"
echo "════════════════════════════════════════════════════"
