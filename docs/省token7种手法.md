# Token 省法大全 · 7 种手法

> LTC RAG Bot V4 实测效果：高频问题省 100%，低频问题省 60-70%。

---

## 手法 1: AnswerCache（省 100%）

**见**: `answer-cache-design.md`

- 第 3 次访问起直接返回快照
- 高频问题（每天 ≥2 次）零 LLM token 消耗

---

## 手法 2: Context Top3 → Top2 × 300 字

| 配置 | context 字数 | LLM input tokens |
|---|---|---|
| top3 × 800 字（V3） | 2400 | ~3200 |
| **top2 × 300 字（V4）** | **600** | **~800** |
| 省 | 75% | ~75% |

**代码**:
```python
# /query 端点
results = vector_store.query(question, top_k=2)
# 每条截断到 300 字
context_chunks = [r["text"][:300] for r in results]
context = "\n---\n".join(context_chunks)
```

**为什么 300 字够**: 中文 300 字 ≈ 关键信息密度上限。太长反而稀释关键内容。

---

## 手法 3: Prompt 瘦身（省 30% output）

### V3 prompt（臃肿）

```
你是 LTC 公司的 AI 产品专家，请基于以下知识库内容回答用户问题。
请按照 FABE 格式组织回答：
**Feature:** 描述产品功能
**Advantage:** 说明产品优势
**Benefit:** 描述客户价值
**Evidence:** 引用知识库证据

知识库内容：
---
{context}
---

用户问题：{question}
```

### V4 prompt（瘦身后）

```
基于以下知识库内容，用简洁语言回答用户问题。
先给结论，再给关键细节。控制在 150-250 字。

知识：
{context}

问：{question}
```

**差异**:
- 去掉 FABE 4 段标题 → LLM 不会输出 `**Feature:**` 这种冗余
- 去掉开场白（"你是 LTC 公司的..."）
- 加字数上限（150-250）→ 限制 LLM 啰嗦

---

## 手法 4: 回答纯文本（去 Markdown）

### V3 输出（带 Markdown 包裹）

```
**产品功能**：CloudBase 部署支持零代码一键发布。

**优势**：相比自建 K8s，省去运维成本。

**客户价值**：工程师专注业务，不用管基础设施。

> 来源：product-manual.pdf P3
```

### V4 输出（纯文本）

```
CloudBase 部署支持零代码一键发布，相比自建 K8s 省去运维成本，
工程师可专注业务开发，不用管基础设施维护。
来源: product-manual.pdf P3
```

**省多少**: ~20%。飞书展示也更干净（不会出现加粗标题）。

---

## 手法 5: 问题预处理（去停用词）

```python
def preprocess_question(q: str) -> str:
    """去掉无效词，但用于缓存的 key 已经 strip().lower()"""
    stopwords = ["请问", "怎么", "如何", "的", "呢", "啊", "？", "?"]
    for w in stopwords:
        q = q.replace(w, "")
    return q.strip()
```

**效果**: 减少问题长度 → TF-IDF 向量更准 + LLM input 更短。

---

## 手法 6: 向量预计算（零 LLM token）

启动时预计算所有向量存 `.npy`，**不要每次 query 时重新算 TF-IDF**。

```python
# _load() 启动时一次性加载
self._tfidf_matrix = np.load(os.path.join(self.persist_dir, "vectors.npy"))
# query 时只算问题向量 + 矩阵乘法
```

---

## 手法 7: COS 挂载缓存元数据

metadata.json 存 COS，启动时加载到内存 dict。
**不要每次 query 时读文件**。

```python
self._metadata = self._load_metadata()  # 启动时一次性
# 后续所有 query 都从内存 dict 取
```

---

## 综合效果

| 场景 | 手法组合 | 省 LLM token |
|---|---|---|
| 高频问题（≥3 次） | #1 AnswerCache | **100%** |
| 中低频问题 | #2 + #3 + #4 | **60-70%** |
| 冷启动首问 | #2 + #3 + #4 | **50%** |
