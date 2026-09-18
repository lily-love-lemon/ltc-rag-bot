# 13 项冒烟验收清单

> 部署完成后必须 **全部通过** 才算 OK。
> 每项包含: 命令 + 预期输出 + 不通过时的排查方向。

---

## 后端 API（7 项）

### 1. 健康检查

```bash
curl -s "$URL/health"
```

**预期**: `{"status": "ok", "chunks": N, "cache_hit": M}`
**排查**: 500 → 看容器日志 `tcb cloudrun log -s ltc-rag-bot`

### 2. /chunks/options 动态选项

```bash
curl -s "$URL/chunks/options" -H "X-API-Key: <API_KEY>"
```

**预期**: `{"sources": ["product.pdf", "faq.md"], "strategies": ["default"]}`
**排查**: sources 为空 → metadata 里没有 source 字段，检查入库代码

### 3. /kb 增强返回

```bash
curl -s "$URL/kb" -H "X-API-Key: <API_KEY>" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'by_type' in d and d['total_chunks'] > 0"
```

**预期**: 无错误
**排查**: KeyError by_type → /kb 没改 V4 增强逻辑

### 4. 切片预览长度

```bash
curl -s "$URL/kb" -H "X-API-Key: <API_KEY>" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f['text_preview']) for f in d.get('chunks',[])[:1]]"
```

**预期**: 显示 ≥ 150 字的切片内容
**排查**: 只有 30 字 → 后端 text_preview 没改到 200

### 5. 检索 + 重排

```bash
curl -s -X POST "$URL/query" -H "Content-Type: application/json" -H "X-API-Key: <API_KEY>" -d '{"question": "部署流程"}'
```

**预期**: 返回 answer + sources，`cosine_score` 字段存在
**排查**: 无 cosine_score → rerank 层没跑，检查 `use_rerank=True` 默认值

### 6. AnswerCache 第 3 次命中

```bash
# 连续问 3 次同一个问题
Q='{"question": "产品怎么部署？"}'
for i in 1 2 3; do
  curl -s -X POST "$URL/query" -H "Content-Type: application/json" -d "$Q" | python3 -c "import sys,json; r=json.load(sys.stdin); print(f'第$i次: cache_hit={r.get(\"cache_hit\", False)}')"
done
```

**预期**: 第 1、2 次 `False`，第 3 次 `True`
**排查**: 全部 False → PROMOTE_THRESHOLD 逻辑没生效，检查 hit_count 递增

### 7. /cache/stats

```bash
curl -s "$URL/cache/stats" -H "X-API-Key: <API_KEY>"
```

**预期**: `{"total": N, "hot": M, "threshold": 2}`
**排查**: 500 → AnswerCache 文件权限或路径问题

---

## Portal 前端（4 项）

### 8. 旧紫配色零残留

```bash
curl -s "$URL/" | grep -cE "#7c3aed|#8b5cf6|rgba\(124.*purple"
```

**预期**: **0**
**排查**: > 0 → 配色替换漏了，grep 定位具体行

### 9. V4 Portal 关键元素

```bash
curl -s "$URL/" | grep -cE "file-management|cache-tag|created_at|rerank-toggle"
```

**预期**: ≥ 3（Portal V4 才有的元素）
**排查**: 0 → Portal 部署的是旧版本，检查 Dockerfile 里 portal 路径

### 10. 5 个 tab 全在

```bash
curl -s "$URL/" | grep -cE "问答|上传|切片|文件管理|Prompt"
```

**预期**: ≥ 4
**排查**: 缺 tab → 重写 index.html 时有丢失

### 11. 切片预览不截断

在 Portal 浏览器页面，看切片管理 tab 的切片预览内容。
**预期**: 看到 ≥ 200 字的完整切片文本。
**排查**: 只 ~30 字 → CSS truncate 没移除。

---

## 安全 & 运维（2 项）

### 12. API Key 鉴权生效

```bash
curl -s "$URL/kb" | head -1
```

**预期**: 401 / "API Key required"
**排查**: 200 + 数据 → 鉴权没开，设 `STRICT_AUTH=true`

### 13. rollback.sh 可执行

```bash
ls -la rollback.sh && head -3 rollback.sh
```

**预期**: 有可执行权限；内容含 DeployId 015 和 RunId `multi_tenant_1x7PV6ZTf8QEDK`
**排查**: RunId 为空 → 部署后没及时记录

---

## 冒烟全绿才算上线

以上 13 项 **必须全部通过**。任何一项失败 → 排查修复后重新部署。
