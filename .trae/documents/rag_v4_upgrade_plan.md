# LTC RAG Bot V4 知识库升级版 Implementation Plan（最终版）

> 回滚基线已固化：`v3-20260918-smoke-green` · 当前代码 100% 等于 baseline · 零改动已确认
> CloudBase 环境：`lily0625ai-d1gpwc1vw89141edd` · 服务 `ltc-rag-bot` · 0.5核 128MB · COS 挂载 `/mnt/chroma`

---

## 0. 一键回滚保障（先建立安全网）

```bash
# 回滚脚本：rollback.sh（本计划 Phase C 结束时创建）
cat > rollback.sh << 'EOF'
#!/bin/bash
set -e
echo "=== 一键回滚脚本 ==="
cd /workspace/ltc-rag-bot

# 1. Git 回滚代码
git checkout v3-20260918-smoke-green
echo "✓ 代码已回滚到 v3 baseline"

# 2. 重新部署到 CloudBase
tcb cloudrun deploy -s ltc-rag-bot -e lily0625ai-d1gpwc1vw89141edd
echo "✓ CloudBase 部署已触发"

# 3. 健康检查
sleep 30
curl -s http://localhost:8080/health || echo "请手动验证：tcb cloudrun log -s ltc-rag-bot"
echo "=== 回滚完成 ==="
EOF
chmod +x rollback.sh
```

**三重回滚保险**：
1. Git tag `v3-20260918-smoke-green`（代码级）
2. CloudBase 上一个稳定版本（服务级）
3. COS 上数据不动——回滚只动代码，metadata 向后兼容

---

## Repository Research（7 个问题精确定位）

| # | 问题 | 根因 | 代码位置 |
|---|---|---|---|
| 1 | UI 紫黑配色 | body `#0f172a` 深紫蓝，header 渐变 `#1e40af→#7c3aed` 蓝紫 | `portal/index.html` L9-L10 |
| 2 | 切片来源/策略没选项 | 后端**已支持** filter（`app.py` L905-907），但前端下拉框**硬编码**（HTML L191-192 只有空 option），没动态拉后端 | HTML L191-192 + JS L457-465 |
| 3 | 只有 BM25 无重排 | 检索链路：TF-IDF cosine → BM25 → **RRF 融合** → top_k。RRF 后没有二次重排 | `app.py` L380-387（query 方法内） |
| 4 | FABE 输出太长费 token | ① `_do_rag` 输出加三段 Markdown 包裹（L1062-1064）；② context_block 塞 top3 全量 text；③ FABE prompt 让 LLM 写 4 段 200-400 字 | `app.py` L1052-1064（answer 拼接）+ L713-728（fabe prompt） |
| 5 | 高频问题快照缓存 | 完全没实现 | `_do_rag` L1035-1083 无任何 cache 逻辑 |
| 6 | 切片只有 30 字 | 后端返回 `text_preview=text[:80]` ✅，但前端 `.chunk-text` 继承父容器宽度，640px 宽容器 + 20px padding → 实际显示 ~30 个汉字宽度 | HTML L465 渲染 + CSS L56 `.chunk-item` 宽度 |
| 7 | 来源概览→文件管理 | metadata 只有 `{id, source, text, strategy}` 4 字段，没有 `created_at`/`file_type`/`file_size`，前端没法做分类 | `SimpleVectorStore.add()` L248-253（metadata 只写 4 字段） |

---

## 整体设计理念

> "更省 token 又更完善准确高效"

```
┌─────────────────────────────────────────────────────────────────┐
│                    知识库 V4 架构总览                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  新文件上传 ──▶ ingest() ──▶ SimpleVectorStore.add()             │
│                                 │                               │
│                                 ├─ 旧：{id, source, text, strategy}│
│                                 └─ V4：{                        │
│                                     id, source, text, strategy,  │
│                                     created_at, file_type,       │
│                                     file_size                    │
│                                   }                              │
│                                                                 │
│  问答请求 ──▶ _do_rag()                                         │
│                 │                                                │
│                 ├─① 查 answer_cache ──命中(hit_count≥2)──▶ 直接返回│
│                 │          │                               ↑     │
│                 │          └─未命中                         │     │
│                 ├─② _hybrid_search() ──▶ TF-IDF cosine          │
│                 │                        │                      │
│                 │                        ├─ BM25 (jieba)         │
│                 │                        │                      │
│                 │                        ├─ RRF 融合 (top 15)   │
│                 │                        │                      │
│                 │                        └─★ V4 新增 cosine rerank│
│                 │                              (top 3)          │
│                 │                                                │
│                 ├─③ context_block (top 2 × 300字 截断)           │
│                 │                                                │
│                 ├─④ call_llm(瘦身后的 FABE prompt) ──▶ 写入 cache│
│                 │                                                │
│                 └─⑤ 返回 answer (无 Markdown 包裹)               │
│                                                                 │
│  token 节省估算：                                                │
│  ├─ answer_cache：高频问题省 100% LLM token                     │
│  ├─ context 截断：top3→top2 + 300字/条 ≈ 省 50% input token    │
│  ├─ prompt 瘦身：去掉输出格式约束 ≈ 省 30% output token         │
│  └─ 合计：高频场景 100% 省，低频场景 ~50-60% 省                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Files and Modules

| 文件 | 改动类型 | 改动概要 |
|---|---|---|
| `app.py` (1239行) | **重构 7 处** | 见下方 Phase A，精确到行号 |
| `portal/index.html` (645行) | **整体重写** | 配色 + 筛选 + 文件管理 tab + 问答增强 + 空状态 |
| `prompts/fabe.txt` | **新增（瘦身版）** | 只让 LLM 输出核心话术文本 |

---

## Implementation Steps

### Phase A：后端增强（不改前端也能独立冒烟验证）

---

#### A1. SimpleVectorStore metadata 增强
**文件**: `app.py` L248-253 (`SimpleVectorStore.add` 方法)

**改动**: 在 metadata dict 里加 3 个新字段：

```python
# 旧代码 L248-253:
self.metadata.append({
    "id": doc_id,
    "text": documents[i],
    "source": metadatas[i].get("source", "unknown"),
    "strategy": metadatas[i].get("strategy", "default"),
})

# 改为:
created_at = metadatas[i].get("created_at") or datetime.utcnow().isoformat() + "Z"
file_type = metadatas[i].get("file_type") or (
    os.path.splitext(metadatas[i].get("source", ""))[1].lower() or ".inline"
)
self.metadata.append({
    "id": doc_id,
    "text": documents[i],
    "source": metadatas[i].get("source", "unknown"),
    "strategy": metadatas[i].get("strategy", "default"),
    "created_at": created_at,
    "file_type": file_type,
    "file_size": len(documents[i]),
})
```

**同时修改**：
- `SimpleVectorStore.get()` L318：include metadatas 时也返回新字段
- `SimpleVectorStore.update()` L274-275：支持更新 `created_at/file_type`（不强制更新，保留原值）
- `_load()` L83：**不需要改**——旧 metadata 没有新字段时，Python dict `.get()` 会返回 None，向后兼容
- `/kb` 端点 L821-836：按 `file_type` 分组，返回 `{file_type: {chunks, file_size_sum, files: [source1, source2]}}` + 按 `created_at` 排序

**冒烟验证**:
```bash
curl -s -X POST http://localhost:8080/ingest-text \
  -H "Content-Type: application/json" \
  -d '{"text": "测试文本ABC...", "source": "test.md", "strategy": "default"}' \
  -H "X-API-Key: dev-only-key-change-in-prod" | python3 -m json.tool

curl -s http://localhost:8080/kb -H "X-API-Key: dev-only-key-change-in-prod" | python3 -m json.tool
# 应该看到 created_at / file_type / file_size
```

---

#### A2. hybrid_rerank 加入轻量重排层
**文件**: `app.py` L344-401 (`SimpleVectorStore.query` 方法内)

**改动点**: 在 RRF 融合 L380-387 之后，加 cosine 重排：

```python
# 旧代码 L380-387:
fused_ranks = {...}
final_idx = sorted(fused_ranks.keys(), key=lambda i: -fused_ranks[i])[:n]

# 改为 RRF → cosine rerank:
# RRF 得到 top 15（比原 top_k 多 3 倍，给 rerank 选）
rerank_pool_size = min(n * 3, len(self.metadata))
rerank_pool = sorted(fused_ranks.keys(), key=lambda i: -fused_ranks[i])[:rerank_pool_size]

# cosine 重排：用原始 query 向量 × pool 内 chunk 向量点积
rerank_scores = []
for doc_idx in rerank_pool:
    chunk_vec = self.vectors[doc_idx]
    norm = np.linalg.norm(chunk_vec)
    if norm > 0:
        score = float(self.vectors[doc_idx] @ q_vec / norm)
    else:
        score = 0.0
    rerank_scores.append((doc_idx, score))

# 取 top n
rerank_scores.sort(key=lambda x: -x[1])
final_idx = [i for i, _ in rerank_scores[:n]]
```

**新增参数**: `use_rerank: bool = True` 加进 `query()` 和 `_hybrid_search()` 签名

**冒烟验证**: 同一问题，对比 rerank 前后 top3 的来源命中率（应该更高或持平）

---

#### A3. AnswerCache 回答快照（V4 核心省 token 机制）
**新增类**（放在 `_hybrid_search` 之后、初始化之前，约 L460）:

```python
import hashlib

class AnswerCache:
    """基于 question 哈希的回答快照 · 持久化到 COS 挂载路径"""
    
    MAX_ENTRIES = 1000          # 上限，超过 LRU 淘汰
    PROMOTE_THRESHOLD = 2       # hit_count >= 2 才返回缓存（避免单次访问浪费新鲜度）
    
    def __init__(self, path: str):
        self.path = Path(path) / "answer_cache.json"
        self.data: dict[str, dict] = {}
        self._load()
    
    def _load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except Exception:
                self.data = {}
    
    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
    
    @staticmethod
    def _key(question: str) -> str:
        return hashlib.sha256(question.strip().lower().encode()).hexdigest()[:16]
    
    def get(self, question: str) -> dict | None:
        k = self._key(question)
        entry = self.data.get(k)
        if not entry:
            return None
        entry["hit_count"] = entry.get("hit_count", 0) + 1
        self._save()
        if entry["hit_count"] < self.PROMOTE_THRESHOLD:
            return None  # 还没高频到值得返回缓存
        return entry
    
    def set(self, question: str, answer: str, sources: list, llm_used: str):
        k = self._key(question)
        if k not in self.data:
            self.data[k] = {
                "question": question,
                "answer": answer,
                "sources": sources,
                "llm_used": llm_used,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "hit_count": 0,
            }
        # LRU 淘汰
        if len(self.data) > self.MAX_ENTRIES:
            sorted_keys = sorted(self.data.keys(), key=lambda h: -self.data[h].get("hit_count", 0))
            for old_k in sorted_keys[self.MAX_ENTRIES:]:
                del self.data[old_k]
        self._save()
    
    def stats(self) -> dict:
        total = len(self.data)
        hot = sum(1 for v in self.data.values() if v.get("hit_count", 0) >= self.PROMOTE_THRESHOLD)
        return {"total_snapshots": total, "hot_count": hot, "max_entries": self.MAX_ENTRIES}
    
    def delete(self, question: str) -> bool:
        k = self._key(question)
        if k in self.data:
            del self.data[k]
            self._save()
            return True
        return False
```

**初始化**（约 L468，跟 VECTOR_STORE 一起）:
```python
ANSWER_CACHE = AnswerCache(CHROMA_PATH)
print(f"[init] ✅ AnswerCache ready · snapshots={len(ANSWER_CACHE.data)}")
```

**在 `_do_rag` L1037 开头插入缓存检查**:
```python
def _do_rag(question: str, top_k: int = 5, use_bm25: bool = True, use_rerank: bool = True) -> dict:
    q = question.strip()
    if not q:
        raise ValueError("question required")
    
    # ★ V4 新增：先查 answer_cache
    cached = ANSWER_CACHE.get(q)
    if cached:
        return {
            "answer": cached["answer"],
            "sources": cached["sources"],
            "llm_used": "cache",
            "total_docs": VECTOR_STORE.count(),
            "hybrid": {"bm25_available": JIEBA_AVAILABLE, "use_bm25": use_bm25, "use_rerank": use_rerank},
            "cache_hit": True,
            "cache_stats": ANSWER_CACHE.stats(),
        }
    
    # ... 原有逻辑往下 ...
```

**LLM 调用成功后写入缓存**（L1059 之后）:
```python
ANSWER_CACHE.set(q, llm_reply, [{"text": c[:200], "source": m.get("source","?")} for c, d, m in top3], llm_backend)
```

**新增端点**:
- `GET /cache` → `list(ANSWER_CACHE.data.values())`
- `GET /cache/stats` → `ANSWER_CACHE.stats()`
- `DELETE /cache/{question}` → `ANSWER_CACHE.delete(question)`

**冒烟验证**:
```bash
# 第一次查询
curl -s -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"question": "产品怎么部署？"}' \
  -H "X-API-Key: dev-only-key-change-in-prod" | python3 -c "import sys,json; r=json.load(sys.stdin); print('llm_used:', r.get('llm_used'), 'cache_hit:', r.get('cache_hit'))"

# 第二次查询（同一个问题）→ 应该 cache_hit: True, llm_used: cache
curl -s -X POST http://localhost:8080/query ... 同上 | python3 -c "..."

# 查看缓存状态
curl -s http://localhost:8080/cache/stats -H "X-API-Key: dev-only-key-change-in-prod" | python3 -m json.tool
```

---

#### A4. FABE 输出格式瘦身（~60% token 节省）

**4a. 瘦身后的 prompt**（覆盖 `prompts/fabe.txt` 或修改 L713-728 的 `_BUILTIN_PROMPTS`）:

```
你是专业的 B2B 销售话术专家。基于参考内容，按 FABE 法则回答客户问题。

规则：
1. 参考内容不足 → 直接说「知识库暂未收录相关内容」，不要编造
2. Feature→Advantage→Benefit→Evidence 按 FABE 结构组织，但不要显式标注段落名
3. Benefit 必须从客户视角（省多少钱/多少时间/降低什么风险）
4. 回答 150-250 字，口语化

客户问题：{question}

参考内容：
{context}

回答：
```

**4b. `_do_rag` answer 拼接去冗余**（L1062-1064 替换）:

```python
# 旧代码:
answer = f"**问题**：{q}\n\n**🤖 AI 话术（FABE · {llm_backend}）**：\n{llm_reply}\n\n**📎 参考片段**：\n{context_block}"

# 改为：answer 就是纯文本，sources 结构化返回给前端
answer = llm_reply  # 去掉所有 Markdown 包裹
```

**4c. context_block 截断**（L1052-1056 替换）:

```python
# 旧：top 3，全量 text
top3 = list(zip(chunks[:3], distances[:3], metadatas_list[:3]))
context_block = "\n".join(
    f"[{i+1}] (相似度 {1-d:.3f}, 来源 {(m or {}).get('source','?')}) {c}"
    for i, (c, d, m) in enumerate(top3)
)

# 新：top 2（rerank 后更精准，2 条足够），每条截断到 300 字
top2 = list(zip(chunks[:2], distances[:2], metadatas_list[:2]))
context_block = "\n".join(
    f"[{i+1}] 来源 {(m or {}).get('source','?')}: {(c[:300] + '...') if len(c) > 300 else c}"
    for i, (c, d, m) in enumerate(top2)
)
```

**冒烟验证**: 同一问题，对比前后两次 answer 长度（目标缩短 ~60%）

---

#### A5. /chunks/options 动态筛选选项 API
**新增端点**（放在 `/chunks` 之前，约 L898）:

```python
@app.get("/chunks/options")
def get_chunk_options():
    """返回当前 metadata 中所有 source 和 strategy 的去重集合 —— 供前端筛选下拉框使用"""
    sources = sorted(set(md.get("source", "unknown") for md in VECTOR_STORE.metadata))
    strategies = sorted(set(md.get("strategy", "default") for md in VECTOR_STORE.metadata))
    return JSONResponse({"sources": sources, "strategies": strategies})
```

---

#### A6. 同步修改 list_chunks 返回结构
**文件**: `app.py` L933-964

让 `list_chunks` 的 chunks_data 返回包含**完整 200 字 text_preview** + 新增的 metadata 字段：

```python
# 旧: "text_preview": (c[:80] + "…") if len(c) > 80 else c
# 新:
"text_preview": (text[:200] + "…") if len(text) > 200 else text,
"created_at": m.get("created_at"),
"file_type": m.get("file_type"),
"file_size": m.get("file_size"),
```

---

### Phase B：Portal 前端重写（`portal/index.html` 整体替换）

> 单 HTML 文件（645行 → 预计 700-750 行），零新依赖

#### B1. 配色重设计（Linear/Vercel 风格 · 商务深蓝灰）

| 元素 | 旧值 | 新值 | 说明 |
|---|---|---|---|
| body background | `#0f172a`（深紫蓝） | `#0b0f19`（近黑 + 极微蓝） | 商务感基础 |
| header background | `linear-gradient(135deg,#1e40af,#7c3aed)`（蓝紫渐变） | `#ffffff` 白色底 + 底部 2px `linear-gradient(90deg,#6366f1,#06b6d4)` 细线 | 白色 header 更干净 |
| header 文字 | `#fff` | `#0f172a` | 深色字配白底 |
| card/panel | `#1e293b`（蓝灰） | `#1f2937`（中性灰） | 去掉蓝调 |
| 输入框背景 | `#0f172a` | `#111827` | 略亮于 body |
| 边框 | `#334155` | `#374151` | 中性灰细边 |
| 主强调色 | `#7c3aed`（紫） | `#6366f1`（靛蓝） | 降低饱和度更商务 |
| active tab | 紫底 | 靛蓝 `#4f46e5` | 替代 |
| hover | 紫 | `#6366f1` 淡底 `rgba(99,102,241,0.1)` | 柔和 |
| 状态 | 绿 `#22c55e` 红 `#ef4444` | 不变 | 足够清晰 |

#### B2. 切片管理 tab 重设计

```
[🔪 切片管理]
┌─────────────────────────────────────────────────────────┐
│ 🔍 全文搜索 _________  [来源 ▾ 全部] [策略 ▾ 全部]    │
│                                                         │
│  ☑  [来源标签] [策略标签]  1283字  2026-09-18  [编辑][删]│
│  ☑  [来源标签] [策略标签]  876字   2026-09-17  [编辑][删]│
│       这里显示完整 200 字预览，不再截断到 30 字          │
│                                                         │
│  [批量删除选中]  共 123 条 · 第 1/7 页                   │
└─────────────────────────────────────────────────────────┘
```

- 筛选下拉选项从 `GET /chunks/options` 动态加载（页面 load 时 fetch）
- 预览显示完整 200 字 + 字符数 + created_at
- 支持批量删除（checkbox）

#### B3. 文件管理 tab（替换「来源概览」）

```
[📁 文件管理]
┌─────────────────────────────────────────────────────────┐
│ [类型 ▾ 全部]  [日期 ▾ 全部]  [🔍 文件名搜索 ________] │
│                                                         │
│ 📄 产品手册-v2.md          ┌─ 5 切片 · 12.3KB · 9/18   │
│    ┌─────────────────┐    │                              │
│    │ 📄 产品手册-v1.md │    │                              │
│    │ 3 切片 · 8.1KB    │    │                              │
│    │ 9/17              │    │                              │
│    └─────────────────┘    └─ 点击看所有切片              │
│                                                         │
│ [删除整个来源]  [编辑来源名]                              │
└─────────────────────────────────────────────────────────┘
```

- 按 `file_type` 分组（.md/.pdf/.txt/.inline 等）
- 按 `created_at` 倒序
- 类型图标：📄 md/txt · 📊 pdf · 📝 inline · 🔧 其他
- 支持「删除整个来源」（调 `DELETE /kb` 带 ?source=xxx）
- 支持点击展开该来源的所有切片预览

#### B4. 问答 tab 增强

```
[💬 知识库问答]
┌─────────────────────────────────────────────────────────┐
│ 问题：_________________________________________ [问]   │
│                                                         │
│ ── 回答 ──                                              │
│ [⚡ 缓存命中]  [zhipu:glm-4-flash]  检索 12ms · LLM 342ms│
│                                                         │
│ FABE 话术文本（不再有 Markdown 包裹，纯输出）           │
│                                                         │
│ ── 参考来源 ──                                          │
│ [产品手册-v2.md · 相似度 0.89]                          │
│   这里显示来源的前 300 字（可展开）                     │
│ [部署指南.md · 相似度 0.76]                             │
└─────────────────────────────────────────────────────────┘
```

#### B5. 所有 tab 加空状态引导

- 切片管理空 → 引导「去 📤 上传文档」
- 文件管理空 → 引导「去 📤 上传」
- 问答无结果 → 提示「知识库为空，请先上传产品文档」
- Prompt 模板空 → 引导「去 ⚙️ 设置创建」

---

### Phase C：部署 + 冒烟测试

**C1. 部署前锁定 CloudBase 版本号**
```bash
tcb cloudrun record list -s ltc-rag-bot -e lily0625ai-d1gpwc1vw89141edd
# 记下当前 VersionName → 写进 rollback.sh 备用
```

**C2. 部署代码**
```bash
cd /workspace/ltc-rag-bot
git add -A && git commit -m "v4: metadata+rerank+answer_cache+瘦身后prompt+Portal商务化"
git tag v4-pre-deploy
tcb cloudrun deploy -s ltc-rag-bot -e lily0625ai-d1gpwc1vw89141edd
# 等服务就绪（~30s）
```

**C3. 12 项冒烟测试**（必须全过才通知用户）

| # | 测试 | 命令/方法 | 通过标准 |
|---|---|---|---|
| 1 | metadata 增强 | curl ingest-text → /kb | 返回含 created_at/file_type/file_size |
| 2 | rerank 效果 | curl /query 2 次不同问题 | sources 相似度更高或来源更相关 |
| 3 | answer_cache 首次 | 第一次问「产品怎么部署？」 | llm_used=zhipu:glm-4-flash, cache_hit 不存在 |
| 4 | answer_cache 命中 | 第二次问同问题 | llm_used=cache, cache_hit=true, 响应 <100ms |
| 5 | cache stats | curl /cache/stats | hot_count ≥ 1, total_snapshots ≥ 1 |
| 6 | FABE 瘦身 | 对比 v3 vs v4 同一问题 answer 长度 | 缩短 ≥ 50% |
| 7 | /chunks/options | curl /chunks/options | sources/strategies 返回非空数组 |
| 8 | Portal 配色 | 浏览器打开 | 白底 header, 深灰 body, 无紫黑 |
| 9 | 切片筛选 | 下拉框选择「某来源」 | 列表只显示该来源切片 |
| 10 | 文件管理 tab | 点击"📁 文件管理" | 有卡片列表 + 类型分组 |
| 11 | COS 持久化 | 重启后 curl /health + /cache/stats | docs_count 和 snapshots 都保留 |
| 12 | 飞书端到端 | @机器人问「xxx」 | 正常返回 + 第二次有 cache |

**C4. 创建一键回滚脚本**
```bash
# 部署成功 + 冒烟全过之后
cat > rollback.sh << 'SCRIPT'
#!/bin/bash
set -e
echo "=== LTC RAG Bot 一键回滚 ==="
cd /workspace/ltc-rag-bot

echo "1/3 Git 回滚..."
git checkout v3-20260918-smoke-green

echo "2/3 重新部署 CloudBase..."
tcb cloudrun deploy -s ltc-rag-bot -e lily0625ai-d1gpwc1vw89141edd

echo "3/3 健康检查..."
sleep 30
echo "请手动验证: curl http://localhost:8080/health"
echo "=== 回滚完成 ==="
SCRIPT
chmod +x rollback.sh
git add rollback.sh && git commit -m "v4: add rollback script"
```

---

## Dependencies and Considerations

| 方面 | 细节 |
|---|---|
| **零新 pip 依赖** | rerank 用 numpy 已内置，answer_cache 用 hashlib+json 标准库 |
| **metadata 向后兼容** | 旧数据没有新字段 → `.get()` 返回 None，前端显示"未知"不 crash |
| **COS 持久化** | answer_cache.json 存 `/mnt/chroma/` → 自动进 COS 挂载，重启不丢 |
| **answer_cache 自动触发** | hit_count ≥ 2 才返回缓存（首次存但不返回），平衡新鲜度和省 token |
| **Portal 重写** | 单 HTML 文件，加 `?v=4` cache-busting 防止浏览器缓存旧版 |
| **CloudBase 免费版兼容** | 0.5核 128MB 够用——rerank 是 numpy 纯 CPU，answer_cache 是小 JSON 文件 |
| **部署体积** | Dockerfile 不变，pip install 不变 |

---

## Risks & Mitigations

| 风险 | 概率 | 影响 | 处理 |
|---|---|---|---|
| rerank 反而降低效果 | 低 | 中 | 加 `use_rerank` 可关参数；冒烟测试对比 top3 命中率 |
| answer_cache 膨胀 | 低 | 低 | MAX_ENTRIES=1000 + LRU 淘汰 hit_count 最低的 |
| Portal JS bug | 中 | 低 | 本地用 Python 模拟 fetch 跑一遍 + 浏览器 DevTools Console 检查 |
| COS 挂载后 metadata 全丢 | 极低 | 高 | 先 curl /health 看 docs_count，0 则重新 ingest（有备份文本） |
| 飞书 webhook 缓存旧 prompt | 极低 | 低 | answer_cache 键含问题文本，旧快照自然被新 LLM 结果覆盖 |

---

## 验证检查清单

部署后逐项打勾，**全过才通知用户**：

- [ ] Phase A1：ingest → metadata 有 created_at/file_type/file_size
- [ ] Phase A2：rerank 后 top3 来源更精准
- [ ] Phase A3：第二次问同问题 → cache_hit=true
- [ ] Phase A4：answer 长度缩短 ≥ 50%
- [ ] Phase A5：/chunks/options 返回真实 source/strategy 列表
- [ ] Phase B1：Portal 白底 header + 深灰 body + 靛蓝强调
- [ ] Phase B2：切片筛选动态加载 + 200 字预览
- [ ] Phase B3：文件管理 tab 正常显示
- [ ] Phase B4：问答 tab 显示缓存/LLM 状态 + 耗时
- [ ] Phase B5：各 tab 空状态有引导
- [ ] COS 持久化：重启后数据 + 缓存都保留
- [ ] 飞书端到端：@机器人 → 首次 LLM / 二次 cache
- [ ] 回滚脚本 rollback.sh 可执行
