#!/bin/bash
# ─────────────────────────────────────────────────────────────
# FDE · 本地冒烟 9 步
# ─────────────────────────────────────────────────────────────
set -e

APP_URL="${1:-http://localhost:8080}"
API_KEY="${API_KEY:-dev-only-key-change-in-prod}"

echo "════════════════════════════════════════════════"
echo " FDE · 冒烟测试"
echo " App URL: $APP_URL"
echo "════════════════════════════════════════════════"

PASS=0
FAIL=0

check() {
  local desc="$1"; local cmd="$2"; local expect="$3"
  local result
  result=$(eval "$cmd" 2>/dev/null || echo "ERROR")
  if echo "$result" | grep -q "$expect"; then
    echo "  ✅ $desc"
    PASS=$((PASS+1))
  else
    echo "  ❌ $desc  →  期望 '$expect', 得到 '${result:0:60}'"
    FAIL=$((FAIL+1))
  fi
}

# ── Step 0: 清理旧数据 ──
rm -rf chroma_data/* 2>/dev/null || true
rm -f /mnt/chroma/answer_cache.json 2>/dev/null || true

# ── Step 1: health ──
check "1. /health 正常" \
  "curl -s $APP_URL/health" \
  '"status".*"ok"'

# ── Step 2: /kb 返回 total_chunks ──
check "2. /kb 返回 total_chunks" \
  "curl -s $APP_URL/kb -H 'X-API-Key: $API_KEY'" \
  'total_chunks'

# ── Step 3: /chunks/options 返回非空数组 ──
check "3. /chunks/options 有选项" \
  "curl -s $APP_URL/chunks/options -H 'X-API-Key: $API_KEY'" \
  '"sources"'

# ── Step 4: 入库测试 ──
check "4. ingest-text 入库成功" \
  "curl -s -X POST $APP_URL/ingest-text -H 'Content-Type: application/json' -H 'X-API-Key: $API_KEY' -d '{\"text\":\"这是一段测试文本用于验证入库流程\",\"source\":\"test.md\"}'" \
  '"chunks".*1'

# ── Step 5: 入库后 chunk+1 ──
BEFORE=$(curl -s $APP_URL/kb -H "X-API-Key: $API_KEY" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_chunks'])")
check "5. 入库后 total_chunks 增加" \
  "echo $BEFORE" \
  '1'

# ── Step 6: /query 正常返回 ──
check "6. /query 返回 answer" \
  "curl -s -X POST $APP_URL/query -H 'Content-Type: application/json' -d '{\"question\":\"测试\"}'" \
  '"answer"'

# ── Step 7: 缓存第 3 次命中 ──
for i in 1 2 3; do
  curl -s -X POST $APP_URL/query -H 'Content-Type: application/json' -d '{"question":"缓存测试"}' > /dev/null
done
check "7. AnswerCache 第 3 次命中" \
  "curl -s -X POST $APP_URL/query -H 'Content-Type: application/json' -d '{\"question\":\"缓存测试\"}' | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"cache_hit\",\"MISS\"))'" \
  'True'

# ── Step 8: /cache/stats ──
check "8. /cache/stats 正常" \
  "curl -s $APP_URL/cache/stats -H 'X-API-Key: $API_KEY'" \
  '"threshold".*2'

# ── Step 9: Portal 无旧紫配色残留 ──
check "9. Portal 无 #7c3aed 旧紫残留" \
  "curl -s $APP_URL/ | grep -c '#7c3aed'" \
  '0'

echo ""
echo "════════════════════════════════════════════════"
echo " 结果: ✅ $PASS 通过  ❌ $FAIL 失败"
echo "════════════════════════════════════════════════"
[ $FAIL -eq 0 ] && echo "🎉 冒烟全绿，可以部署！" || echo "⚠️ 有失败项，先修复再部署"
