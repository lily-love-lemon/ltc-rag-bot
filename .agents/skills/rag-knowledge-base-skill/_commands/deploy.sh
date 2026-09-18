#!/bin/bash
# ─────────────────────────────────────────────────────────────
# FDE · CloudBase 部署（必须 --force）
# 用法: ./deploy.sh <service_name> <envId>
# ─────────────────────────────────────────────────────────────
set -e

SERVICE="${1:-ltc-rag-bot}"
ENV_ID="${2:?用法: $0 <service_name> <envId>}"

echo "════════════════════════════════════════════════"
echo " FDE · CloudBase 部署"
echo " Service: $SERVICE"
echo " Env:     $ENV_ID"
echo "════════════════════════════════════════════════"

# 1. 记录当前 DeployId（部署前的基线，用于回滚）
echo ""
echo "[1/4] 记录当前基线..."
BEFORE=$(tcb cloudrun record list -s "$SERVICE" -e "$ENV_ID" 2>&1 | head -5)
echo "      $BEFORE"
BEFORE_RUN=$(echo "$BEFORE" | grep -oE 'multi_tenant_[A-Za-z0-9]+' | head -1)
echo "      回滚目标 RunId: $BEFORE_RUN"

# 2. 执行部署（--force 跳过所有确认）
echo ""
echo "[2/4] 开始部署..."
tcb cloudrun deploy -s "$SERVICE" -e "$ENV_ID" --force 2>&1 <<< $'\n'

# 3. 等待构建完成（轮询直到 normal + 100%）
echo ""
echo "[3/4] 等待构建 (每 10s 检查一次)..."
for i in $(seq 1 18); do
  sleep 10
  STATUS=$(tcb cloudrun record list -s "$SERVICE" -e "$ENV_ID" 2>&1 | head -5)
  if echo "$STATUS" | grep -q "normal.*100"; then
    echo "      ✅ 部署完成 (用时 ~$((i*10))s)"
    break
  fi
  echo "      构建中... ($((i*10))s)"
done

# 4. 验证
echo ""
echo "[4/4] 验证新部署..."
AFTER=$(tcb cloudrun record list -s "$SERVICE" -e "$ENV_ID" 2>&1 | head -5)
echo "$AFTER"
AFTER_RUN=$(echo "$AFTER" | grep -oE 'multi_tenant_[A-Za-z0-9]+' | head -1)

echo ""
echo "════════════════════════════════════════════════"
echo " 部署完成！"
echo " 新 RunId: $AFTER_RUN"
echo " 回滚到:   $BEFORE_RUN"
echo "════════════════════════════════════════════════"
echo " 下一步: ./smoke-test.sh <URL> 验证功能"
