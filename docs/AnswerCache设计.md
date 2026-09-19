# AnswerCache 设计

> 高频问题（≥2 次/天）直接返回快照 → 省 100% LLM token。
> 低频问题 → LLM 正常跑。
>
> ⚠️ **本文是设计稿，不是实现文档。** 正文里的类代码是设计阶段草案，与 `code/app.py` 的
> 实际实现有差异（方法名、字段名、淘汰策略均不同）。**以「文末《实现现状校准》为准」** ——
> 该节由 2026-09-18 接手时按代码逐行核对后补写，校准基线 commit `a6d90ad`。

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

---

# 实现现状校准（2026-09-18 · 按 `code/app.py` 逐行核对）

> 校准人：六六🎋 ｜ 基线 commit：`a6d90ad` ｜ 目的：让本文与代码一致，避免照设计稿改代码改错。
> **本节以下内容为准确事实；本节以上为设计稿，仅供理解设计意图。**

## 1. 设计稿 vs 实际实现（逐项差异）

| 项 | 设计稿（本文上半部分） | **实际实现**（`code/app.py`） |
|---|---|---|
| 类名 | `AnswerCache` | `AnswerCache` ✅ 一致 |
| 存放容器 | `OrderedDict`（`self.cache`） | **普通 `dict`**（`self.data`） |
| 写入方法 | `store(question, answer, sources, llm_used)` | **`set(question, answer, sources, llm_used, kb_fingerprint="")`** |
| 读取方法 | `get(question)` → `{"hit": True, **item}` | **`get(question, kb_fingerprint=None)` → 直接返回 `entry`（无 `hit` 键）** |
| 统计方法 | `stats()` → `{total, hot, threshold}` | **`stats(kb_fingerprint=None)` → `{total_snapshots, hot_count, max_entries[, stale_count]}`** |
| 淘汰策略 | `popitem(last=False)`（真 LRU，按插入序） | **按 `hit_count` 降序排序后删尾部**（保留最热的，非插入序 LRU） |
| 保存时机 | `SAVE_INTERVAL = 10`，每 10 次操作存一次 | **每次 `get`/`set`/`delete` 立即 `_save()`**（无批量节流） |
| 存储路径 | 构造参数默认 `/mnt/chroma/answer_cache.json` | 构造传目录，内部拼 `Path(path) / "answer_cache.json"` |
| 首次 `hit_count` | `1`（存入即算一次） | **`0`**（存入不算；下次访问才 `+1`，故第 3 次才达阈值 2） |
| 指纹字段 | 无 | **`kb_fingerprint`（2026-09-18 新增）** |
| `/cache/expire` 端点 | 设计稿称存在 | **不存在**（见第 3 节） |

> 注：设计稿的 `get` 里 `cache.move_to_end(k)` + `popitem(last=False)` 才是标准 LRU；
> 实际实现按 `hit_count` 淘汰，语义是「优先丢弃最冷门的」，与设计稿不同但更符合省 token 目标。

## 2. 实际快照结构（含 2026-09-18 新增字段）

```json
{
  "a1b2c3d4e5f6g7h8": {
    "question": "产品怎么部署？",
    "answer": "部署分 3 步...",
    "sources": [{"text": "...", "source": "product-manual.pdf", "score": 0.87}],
    "llm_used": "zhipu:glm-4-flash",
    "created_at": "2026-09-18T12:00:00Z",
    "hit_count": 0,
    "kb_fingerprint": "9f3c1a7e2b4d5c60"
  }
}
```

- **键** = `sha256(question.strip().lower())[:16]`（与设计稿一致）
- **`hit_count` 初值 0**：存入后第 1、2 次访问分别 `→1`、`→2`，**第 3 次起**（`hit_count ≥ PROMOTE_THRESHOLD=2`）才返回缓存
- **`kb_fingerprint`** = 知识库内容指纹，取自 `SimpleVectorStore.fingerprint`

## 3. `kb_fingerprint`：让缓存随知识库变更自动失效（2026-09-18 新增）

**为什么必须加**：原实现里快照不记知识库版本。改了源文档后，旧答案仍会被当作「高频缓存」返回
→ **等于给客户答错**。这比省 token 重要，属正确性问题。

**指纹怎么算**（`SimpleVectorStore.fingerprint`）：

```python
h = hashlib.sha256()
for md in self.metadata:            # 按 metadata 顺序
    h.update(str(md.get("id",   "")).encode("utf-8", "ignore")); h.update(b"\x00")
    h.update(str(md.get("text", "")).encode("utf-8", "ignore")); h.update(b"\x01")
return h.hexdigest()[:16]
```

**三个关键设计选择**：

1. **只摘要 `id + text`，不含 `created_at / file_type / file_size`** —— 否则
   `scripts/upgrade_metadata.py` 一跑（只补这三字段、不动 `id/text`），全部缓存会被误判失效。
   这是刻意的解耦。
2. **惰性计算 + 内存缓存**（`self._fp_cache`），`_rebuild_all()` 时置空重算。不额外持久化。
3. **不匹配即删除**，不是标记失效 —— 避免失效快照长期占额度（`MAX_ENTRIES=1000` 按 `hit_count` 淘汰，
   冷门失效条目本来也难被清掉）。

**运行时行为**：指纹不匹配时删除快照 + 打日志 + 当未命中（不累加 `hit_count`、不返回）：

```
[answer-cache] ♻️ 快照失效（KB 指纹变化）: 产品怎么部署？
```

**可观测**：`GET /cache/stats` 返回的 `stale_count` 即「已失效快照数」。
实测样例（本地冒烟 9 号项）：`{"total_snapshots":3,"hot_count":0,"max_entries":1000,"stale_count":2}`

**验收断言**：`code/scripts/smoke_local.sh` 第 8b 项 ——
KB 变更前 `cache_hit=True` → 变更后 `cache_hit=False`。

## 4. 实际缓存端点（全仓仅 3 个，无 `/cache/expire`）

| 端点 | 位置 | 功能 |
|---|---|---|
| `GET /cache` | `code/app.py:1091` | 列出所有快照 |
| `GET /cache/stats` | `code/app.py:1096` | 统计，**已传 `VECTOR_STORE.fingerprint` → 带 `stale_count`** |
| `DELETE /cache` | `code/app.py:1101` | 清空全部 |

`/query` 集成位置：`_do_rag()` 内，查缓存处传 `kb_fp`；写入处 `ANSWER_CACHE.set(..., kb_fp)`。

> 结论：设计稿里的 `POST /cache/expire`（按条件过期）**已不需要实现** ——
> 指纹机制让过期自动发生，无需人工触发。

## 5. 尚未做的相关项

- Portal 前端未可视化 `cache_hit` / `stale_count`（后端已返回，前端只用 `cache_hit` 做了基础提示）。
- 服务重启后内存 `_fp_cache` 重算一次，属正常（指纹本身不落盘）。

