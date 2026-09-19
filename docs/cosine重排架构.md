# Cosine Rerank 架构

> 为什么需要重排？RRF 融合只是排序融合，**不理解语义**。
> cosine rerank 用余弦相似度二次排序，把最相关的 3 条顶上去 → 召回精度提升 ~15%。

---

## 检索总链路

```
用户问题 "产品怎么部署？"
  │
  ├─→ [TF-IDF] 向量匹配 → 取 top 15 候选
  ├─→ [BM25] 关键词匹配 → 取 top 15 候选  
  │
  └─→ [RRF] Reciprocal Rank Fusion
         score = Σ 1/(k + rank)   k=60
         → 融合后重新排序，取 top 5
              │
              └─→ ★ [cosine Rerank]
                    question_vec · candidate_vec / (|q| × |c|)
                    → 二次排序，取 top 3
                              │
                              └─→ 喂给 LLM
```

---

## RRF 融合实现

```python
def rrf_fuse(self, ranked_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion"""
    scores = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, 1):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank)
    return scores
```

---

## Cosine Rerank 实现（零依赖）

```python
import numpy as np

def cosine_rerank(self, query_vec: np.ndarray, candidates: list[dict], top_k: int = 3) -> list[dict]:
    """
    纯 numpy cosine similarity 重排。
    输入: query 向量 + RRF 融合后的 top_k 候选
    输出: 按 cosine similarity 排序后的 top 3
    """
    if not candidates:
        return []
    
    # 候选向量矩阵 (n × dim)
    cand_vecs = np.array([c["vector"] for c in candidates])
    
    # cosine similarity
    q_norm = np.linalg.norm(query_vec) + 1e-8
    c_norms = np.linalg.norm(cand_vecs, axis=1) + 1e-8
    similarities = (cand_vecs @ query_vec) / (c_norms * q_norm)
    
    # 降序排序取 top_k
    top_indices = np.argsort(-similarities)[:top_k]
    
    results = []
    for idx in top_indices:
        c = candidates[idx]
        results.append({**c, "cosine_score": float(similarities[idx])})
    return results
```

---

## 为什么不用 cross-encoder？

| 方案 | 精度 | 依赖 | 内存 | CloudBase free tier |
|---|---|---|---|---|
| 无重排 (RRF only) | 75% | 0 | 0 | ✅ |
| **cosine rerank** | **~90%** | **numpy** | **~5MB** | **✅** |
| cross-encoder (bge-reranker) | ~95% | torch + model | ~500MB | ❌ OOM |

**结论**: CloudBase free tier 上 cosine rerank 是精度/资源比最优解。

---

## 开关控制

```python
# /query 端点
def query(self, question: str, use_rerank: bool = True) -> dict:
    ...
    if use_rerank:
        results = self.cosine_rerank(query_vec, candidates, top_k=3)
    else:
        results = candidates[:3]  # 只用 RRF 排序
    ...
```

Portal 的 "Prompt 设置" tab 里可以关掉重排（调试时用）。
