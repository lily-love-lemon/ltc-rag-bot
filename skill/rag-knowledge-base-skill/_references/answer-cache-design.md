# AnswerCache 设计

> 高频问题（≥2 次/天）直接返回快照 → 省 100% LLM token。
> 低频问题 → LLM 正常跑。

---

## 核心设计决策

### 1. PROMOTE_THRESHOLD = 2（关键！）

| 阈值 | 行为 | 问题 |
|---|---|---|
| 0（一存就返回） | 用户第一次问就收到快照 | ❌ 快照可能已过时 |
| **2（第三次起返回）** | 第一次存快照不回；第二次 hit_count++；第三次起返回缓存 | **✅ 平衡新鲜度和省 token** |
| 3 | 第四次起返回 | 太保守，省 token 效果打折 |

### 2. 缓存键

```python
import hashlib
cache_key = hashlib.sha256(question.strip().lower().encode()).hexdigest()[:16]
```

- 16 字符 hex = 2^64 种可能性，碰撞率可忽略
- `strip().lower()` 归一化（"部署" == "部署 " == " 部署"）

### 3. 缓存值结构

```json
{
  "a1b2c3d4e5f6g7h8": {
    "question": "产品怎么部署？",
    "answer": "部署分 3 步...",
    "sources": [{"text": "...", "source": "product-manual.pdf"}],
    "llm_used": "zhipu:glm-4-flash",
    "created_at": "2026-09-18T12:00:00Z",
    "hit_count": 3
  }
}
```

### 4. 持久化

- 路径: `/mnt/chroma/answer_cache.json`（和 vectors.npy 同目录，COS 挂载）
- 自动保存: 每次 `_save()` 写入（单文件，JSON）
- MAX_ENTRIES = 1000: 超过时 LRU 淘汰 hit_count 最低的

---

## 完整 AnswerCache 类

```python
import hashlib, json, os, time
from collections import OrderedDict

class AnswerCache:
    PROMOTE_THRESHOLD = 2   # 第 3 次起返回缓存
    MAX_ENTRIES = 1000
    SAVE_INTERVAL = 10       # 每 10 次操作存一次

    def __init__(self, path: str = "/mnt/chroma/answer_cache.json"):
        self.path = path
        self.cache: OrderedDict = self._load()
        self._ops = 0

    def _load(self) -> OrderedDict:
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    return OrderedDict(json.load(f))
            except Exception:
                pass
        return OrderedDict()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)

    def _key(self, question: str) -> str:
        return hashlib.sha256(question.strip().lower().encode()).hexdigest()[:16]

    def get(self, question: str) -> dict | None:
        k = self._key(question)
        item = self.cache.get(k)
        if item is None:
            return None
        # 低于阈值 → 只计数，不返回缓存
        if item["hit_count"] < self.PROMOTE_THRESHOLD:
            return None
        # 命中 → 提到队首（LRU）
        self.cache.move_to_end(k)
        return {"hit": True, **item}

    def store(self, question: str, answer: str, sources: list, llm_used: str):
        k = self._key(question)
        if k in self.cache:
            self.cache[k]["hit_count"] += 1
        else:
            self.cache[k] = {
                "question": question,
                "answer": answer,
                "sources": sources,
                "llm_used": llm_used,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "hit_count": 1,
            }
        # LRU 淘汰
        while len(self.cache) > self.MAX_ENTRIES:
            self.cache.popitem(last=False)
        # 定期保存
        self._ops += 1
        if self._ops % self.SAVE_INTERVAL == 0:
            self._save()

    def stats(self) -> dict:
        hot = sum(1 for v in self.cache.values() if v["hit_count"] >= self.PROMOTE_THRESHOLD)
        return {"total": len(self.cache), "hot": hot, "threshold": self.PROMOTE_THRESHOLD}

    def clear(self):
        self.cache.clear()
        self._save()
```

---

## /query 端点集成

```python
@app.post("/query")
async def query(req: QueryRequest):
    # 1. 检查缓存
    cached = answer_cache.get(req.question)
    if cached:
        return {"answer": cached["answer"], "sources": cached["sources"], "cache_hit": True}
    
    # 2. 正常 RAG 流程
    results = vector_store.query(req.question, top_k=3, use_rerank=True)
    answer = llm_generate(req.question, results)
    
    # 3. 存缓存（但首次不返回）
    answer_cache.store(req.question, answer, results, llm_model)
    
    return {"answer": answer, "sources": results, "cache_hit": False}
```

---

## 缓存相关 API

| 端点 | 功能 |
|---|---|
| `GET /cache` | 列出所有缓存条目 |
| `GET /cache/stats` | `{total, hot, threshold}` |
| `DELETE /cache` | 清空全部 |
| `POST /cache/expire` | 按条件过期（如 sources 文件变更时清理相关缓存） |
