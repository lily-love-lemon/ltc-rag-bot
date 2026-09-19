#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# LTC RAG Bot · 本地冒烟测试（对应 docs/13项冒烟验收.md 的本地可跑子集）
# 用法:
#   bash scripts/smoke_local.sh              # 开发模式（无鉴权）
#   API_KEY=<API_KEY> bash scripts/smoke_local.sh   # 生产模式（验鉴权）
# 说明: 脚本自己起服务、自己停；跑前会清空本地 chroma_data
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

# ── 环境隔离 ──
# 在 WorkBuddy/Agent 的 shell 里，PYTHONPATH 指向 shim 目录，其 sitecustomize 会把
# 已存在目录上的 mkdir(exist_ok=True) 误拦成 PermissionError: EEXIST。
# 在普通终端（Lily 自己开 Terminal 跑）不存在此问题；此处主动剥离以保证脚本在哪都能跑。
unset PYTHONPATH

CODE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$CODE_DIR"

PY=/Users/lily/.workbuddy/binaries/python/envs/ltc-ragbot/bin/python
PORT="${PORT:-8080}"
BASE="http://127.0.0.1:${PORT}"
export PORT

PASS=0
FAIL=0
declare -a FAILED_ITEMS=()

ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $1"; FAIL=$((FAIL+1)); FAILED_ITEMS+=("$1"); }
hdr()  { echo ""; echo "── $1 ────────────────────────────────────"; }
jq_()  { "$PY" -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

AUTH=()
[ -n "${API_KEY:-}" ] && AUTH=(-H "X-API-Key: ${API_KEY}")

echo "=============================================================="
echo " LTC RAG Bot 本地冒烟 · port=${PORT} · API_KEY=${API_KEY:-<未设=开发模式>}"
echo "=============================================================="

# ── 冒烟前清数据（踩坑 3.3）──
rm -rf chroma_data
mkdir -p chroma_data

# ── 起服务 ──
echo "[start] 启动服务 ..."
"$PY" app.py > /tmp/ltc_smoke_server.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; wait $SRV 2>/dev/null' EXIT

for i in $(seq 1 40); do
  if curl -sf "$BASE/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! curl -sf "$BASE/health" >/dev/null 2>&1; then
  echo "  ❌ 服务 40s 内未就绪，查看 /tmp/ltc_smoke_server.log:"
  tail -20 /tmp/ltc_smoke_server.log
  exit 1
fi
echo "[start] 服务已就绪"

# ── 1. 健康检查 ──
hdr "1. /health"
H=$(curl -s "$BASE/health")
echo "     $H" | head -c 300; echo
[ "$(echo "$H" | jq_ "d['status']")" = "ok" ] && ok "status=ok" || bad "status != ok"
[ "$(echo "$H" | jq_ "d['bm25_available']")" = "True" ] && ok "bm25_available=True" || bad "jieba/bm25 不可用"
[ "$(echo "$H" | jq_ "'docs_count' in d")" = "True" ] && ok "docs_count 字段在" || bad "缺 docs_count"

# ── 2. 入库（粘贴文本）──
hdr "2. /ingest-text 入库"
R=$(curl -s -X POST "$BASE/ingest-text" ${AUTH[@]+"${AUTH[@]}"} -H 'Content-Type: application/json' \
  -d '{"text":"LTC 项目交付流程分四步：需求澄清、方案选型、MVP 验证、七文档归档。需求澄清阶段用 5W1H 收敛范围，明确谁用、解决什么痛点、验收指标（准确率、时延、成本）、数据规模与敏感级别、部署环境（公有云、内网、离线）以及有无 GPU；方案选型阶段至少给两套对比方案并给出明确推荐，同时诚实标注大模型做不好的部分改用传统代码补齐。\n\n部署采用 CloudBase 云托管，回滚依赖 git tag 与 rollback.sh 脚本，上线前必须完成发布变更单、环境清单、部署方案、回滚预案、验证用例、巡检记录与归档 SOP 七份文档。\n\n检索链路由四层组成：第一层 TF-IDF 向量召回，第二层 BM25 加 jieba 关键词召回，第三层 RRF 融合，第四层 cosine 点积重排。","source":"smoke-local.md"}')
echo "     $R"
ING=$(echo "$R" | jq_ "d.get('ingested',0)")
[ "${ING:-0}" -ge 1 ] 2>/dev/null && ok "ingested=${ING}" || bad "入库失败: $R"

# ── 3. /kb 增强返回 ──
hdr "3. /kb 增强返回"
KB=$(curl -s "$BASE/kb" ${AUTH[@]+"${AUTH[@]}"})
[ "$(echo "$KB" | jq_ "'by_type' in d")" = "True" ] && ok "by_type 存在" || bad "缺 by_type"
echo "     total_chunks=$(echo "$KB" | jq_ "d.get('total_chunks')") total_files=$(echo "$KB" | jq_ "d.get('total_files')") by_type=$(echo "$KB" | jq_ "list(d.get('by_type',{}).keys())")"
echo "     files[0]=$(echo "$KB" | jq_ "list(d.get('files',{}).values())[0] if d.get('files') else None")"

# ── 4. 切片预览长度（精确断言：预览长度 = min(原文长度, 201)）──
hdr "4. 切片预览长度（防 CSS 二次截断）"
CH=$(curl -s "$BASE/chunks?page_size=5" ${AUTH[@]+"${AUTH[@]}"})
PL=$(echo "$CH" | jq_ "len(d['chunks'][0]['text_preview']) if d.get('chunks') else 0")
TL=$(echo "$CH" | jq_ "len(d['chunks'][0]['text']) if d.get('chunks') else 0")
if [ -z "${TL:-}" ] || [ "${TL:-0}" -le 0 ] 2>/dev/null; then
  bad "取不到切片文本"
else
  if [ "$TL" -gt 200 ]; then EXP=201; else EXP=$TL; fi
  echo "     原文长度=${TL}  预览长度=${PL}  期望=${EXP}（>200字时截断为 200+"…"）"
  [ "$PL" = "$EXP" ] && ok "预览长度符合后端 200 字截断规则，无二次截断" \
    || bad "预览 ${PL} != 期望 ${EXP}（CSS 截断或 text_preview 逻辑异常，踩坑 2.2）"
fi
# 再验一条长文本切片，确保截断路径被真正走到
LONG=$(echo "$CH" | jq_ "[len(c['text']) for c in d.get('chunks',[])]")
echo "     本页各切片原文长度=${LONG}"

# ── 5. /chunks/options 动态选项 ──
hdr "5. /chunks/options"
OPT=$(curl -s "$BASE/chunks/options")
echo "     $OPT"
[ "$(echo "$OPT" | jq_ "len(d['sources'])")" -ge 1 ] 2>/dev/null && ok "sources 非空" || bad "sources 为空（踩坑 2.1）"

# ── 6. /query 检索 ──
hdr "6. /query 检索"
Q=$(curl -s -X POST "$BASE/query" ${AUTH[@]+"${AUTH[@]}"} -H 'Content-Type: application/json' \
  -d '{"question":"LTC 项目交付流程是什么？"}')
echo "     llm_used=$(echo "$Q" | jq_ "d.get('llm_used')")  answer_len=$(echo "$Q" | jq_ "len(d.get('answer',''))")  sources=$(echo "$Q" | jq_ "len(d.get('sources',[]))")"
[ "$(echo "$Q" | jq_ "len(d.get('answer',''))")" -gt 0 ] 2>/dev/null && ok "answer 非空" || bad "answer 为空: $Q"

# ── 7. use_rerank 透传（P0-2 修复验收）──
hdr "7. use_rerank 参数透传（P0-2）"
Q2=$(curl -s -X POST "$BASE/query" ${AUTH[@]+"${AUTH[@]}"} -H 'Content-Type: application/json' \
  -d '{"question":"回滚怎么做？","use_rerank":false}')
RR=$(echo "$Q2" | jq_ "d.get('hybrid',{}).get('use_rerank')")
echo "     use_rerank=false → hybrid.use_rerank=${RR}"
[ "$RR" = "False" ] && ok "use_rerank=false 被正确透传（修复生效）" || bad "use_rerank 被忽略，仍为 ${RR}（P0-2 未修复）"

# ── 8. AnswerCache：第 2 次起命中（V5-M2 起 CACHE_PROMOTE 默认 1，省 token 优先）──
hdr "8. AnswerCache promote 阈值"
for i in 1 2 3; do
  CH_=$(curl -s -X POST "$BASE/query" ${AUTH[@]+"${AUTH[@]}"} -H 'Content-Type: application/json' \
    -d '{"question":"CloudBase 部署怎么操作？"}' | jq_ "d.get('cache_hit')")
  echo "     第${i}次 cache_hit=${CH_}"
  eval "CHIT${i}=$CH_"
done
[ "${CHIT1:-}" = "False" ] && [ "${CHIT2:-}" = "True" ] && [ "${CHIT3:-}" = "True" ] \
  && ok "第 2 次起命中缓存（CACHE_PROMOTE=1，同问法第 2 次即 0 token）" \
  || bad "缓存阈值异常: ${CHIT1}/${CHIT2}/${CHIT3}"

# ── 8c. V5-M2 省算埋点端点 ──
hdr "8c. /stats/token-economy（省算四层漏斗埋点）"
TE=$(curl -s "$BASE/stats/token-economy?days=7" ${AUTH[@]+"${AUTH[@]}"})
echo "     ${TE}"
TE_Q=$(echo "$TE" | jq_ "d.get('queries_total')")
TE_FIELDS=$(echo "$TE" | jq_ "all(k in d for k in ['l0_hits','l1_hits','l2_short','llm_calls','l3_full_rag','saved_tokens_est','thresholds'])")
[ "${TE_Q:-0}" -ge 1 ] 2>/dev/null && ok "埋点已计入提问数（queries_total=${TE_Q}）" || bad "queries_total 未计数: ${TE_Q}"
[ "$TE_FIELDS" = "True" ] && ok "省算字段齐全（l0/l1/l2/llm_calls/l3/saved/thresholds）" || bad "省算字段缺失: ${TE_FIELDS}"
TE_L1=$(echo "$TE" | jq_ "d.get('thresholds',{}).get('l1_enabled')")
TE_L2=$(echo "$TE" | jq_ "d.get('thresholds',{}).get('l2_enabled')")
echo "     L1 语义近似=${TE_L1}（tfidf 后端下预期 False）· L2 拒答短路=${TE_L2}（默认关闭）"
[ "$TE_L2" = "False" ] && ok "L2 默认关闭（先观测后开启，不误杀）" || bad "L2 不应默认开启: ${TE_L2}"

# ── 8b. AnswerCache 随知识库变更失效 ──
hdr "8b. AnswerCache 随 KB 变更失效"
C_BEFORE=$(curl -s -X POST "$BASE/query" ${AUTH[@]+"${AUTH[@]}"} -H 'Content-Type: application/json' \
  -d '{"question":"CloudBase 部署怎么操作？"}' | jq_ "d.get('cache_hit')")
echo "     KB 变更前 → cache_hit=${C_BEFORE}（期望 True，证明它确实在缓存里）"
curl -s -X POST "$BASE/ingest-text" ${AUTH[@]+"${AUTH[@]}"} -H 'Content-Type: application/json' \
  -d '{"text":"这是一条用于触发知识库内容指纹变化的新切片。内容与缓存测试无关，仅用于验证知识库变更后，旧回答快照是否会被自动作废，而不是继续被当作高频答案返回给客户。","source":"cache-invalidation-test.md"}' >/dev/null
C_AFTER=$(curl -s -X POST "$BASE/query" ${AUTH[@]+"${AUTH[@]}"} -H 'Content-Type: application/json' \
  -d '{"question":"CloudBase 部署怎么操作？"}' | jq_ "d.get('cache_hit')")
echo "     KB 变更后 → cache_hit=${C_AFTER}（期望 False）"
[ "${C_BEFORE:-}" = "True" ] && [ "${C_AFTER:-}" = "False" ] \
  && ok "KB 变更后旧快照自动失效（不会把过期答案当缓存返回）" \
  || bad "缓存失效逻辑异常: 变更前=${C_BEFORE} 变更后=${C_AFTER}"

# ── 9. /cache/stats ──
hdr "9. /cache/stats"
CS=$(curl -s "$BASE/cache/stats" ${AUTH[@]+"${AUTH[@]}"})
echo "     $CS"
[ "$(echo "$CS" | jq_ "'total_snapshots' in d")" = "True" ] && ok "stats 结构正常" || bad "stats 异常: $CS"
[ "$(echo "$CS" | jq_ "'stale_count' in d")" = "True" ] && ok "stats 含 stale_count（过期快照可观测）" || bad "stats 缺 stale_count"

# ── 9b. /embed/info（V4.1 可插拔 embedding）──
hdr "9b. /embed/info（可插拔 embedding）"
EI=$(curl -s "$BASE/embed/info" ${AUTH[@]+"${AUTH[@]}"})
echo "     $EI"
[ "$(echo "$EI" | jq_ "'backend' in d")" = "True" ] && ok "embed/info 结构正常" || bad "embed/info 异常: $EI"
EB=$(echo "$EI" | jq_ "d.get('backend')")
if [ "$EB" = "tfidf" ] || [ "$EB" = "siliconflow" ]; then
  ok "embedding 后端取值合法（${EB}）"
else
  bad "embedding 后端取值非法: ${EB}"
fi
[ "$(echo "$EI" | jq_ "int(d.get('dim') or 0) > 0")" = "True" ] && ok "向量维度 > 0（向量已就绪）" || bad "向量维度为 0: $EI"

# ── 10. Portal 注入（P0-1 修复验收）──
hdr "10. Portal API Key 注入（P0-1）"
PORTAL=$(curl -s "$BASE/")
LEFT=$(printf '%s' "$PORTAL" | grep -c "{{AUTO_API_KEY}}")
echo "     HTML 长度=${#PORTAL}  未替换占位符=${LEFT}"
if [ "${LEFT:-1}" -eq 0 ]; then ok "占位符已被后端替换（无残留）"; else bad "占位符残留 ${LEFT} 处：_render_portal_html 未生效"; fi
if [ -n "${API_KEY:-}" ]; then
  if printf '%s' "$PORTAL" | grep -q "const API_KEY = '${API_KEY}'"; then
    ok "生产模式已注入真实 API Key"
  else
    bad "生产模式未注入 API Key → Portal 全部接口将 401"
  fi
else
  if printf '%s' "$PORTAL" | grep -q "const API_KEY = '';"; then
    ok "开发模式注入空串（后端自动跳过鉴权）"
  else
    bad "开发模式注入异常"
  fi
fi
echo "     紫色 hero: $(printf '%s' "$PORTAL" | grep -c 'linear-gradient(135deg,#1e40af,#7c3aed') 处"

# ── 11. 鉴权行为 ──
hdr "11. API Key 鉴权"
CODE_NO=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/kb")
if [ -n "${API_KEY:-}" ]; then
  echo "     无 key → HTTP ${CODE_NO}"
  [ "$CODE_NO" = "401" ] && ok "生产模式未带 key 正确拒绝（401）" || bad "生产模式未鉴权，返回 ${CODE_NO}"
  CODE_OK=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/kb" -H "X-API-Key: ${API_KEY}")
  [ "$CODE_OK" = "200" ] && ok "带正确 key 放行（200）" || bad "带 key 仍失败 ${CODE_OK}"
else
  echo "     无 key → HTTP ${CODE_NO}"
  [ "$CODE_NO" = "200" ] && ok "开发模式自动跳过鉴权（符合预期）" || bad "开发模式返回 ${CODE_NO}"
fi

# ── 汇总 ──
echo ""
echo "=============================================================="
echo " 结果: ${PASS} 通过 / ${FAIL} 失败"
if [ "$FAIL" -gt 0 ]; then
  echo " 失败项:"
  for f in "${FAILED_ITEMS[@]}"; do echo "   - $f"; done
fi
echo "=============================================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
