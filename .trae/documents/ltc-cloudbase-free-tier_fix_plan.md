# LTC RAG Bot CloudBase 免费版（0.5核 128MB）内存修复计划

## 仓库调研结论

### 当前状态
- **依赖栈**：11 包，chromadb(~100MB) + scikit-learn(~50MB) + rank-bm25(~10MB) + numpy(~20MB) 合计 ~180MB 基础占用
- **隐藏硬 bug**：app.py 第43行使用 `SentenceTransformer` 但未 import，requirements.txt 也没有此包；chromadb 的 `DefaultEmbeddingFunction` 同样依赖 SentenceTransformer → 当前代码**无法启动**
- **配置不匹配**：cloudbaserc.json 配 `cpu:1, mem:1`（付费 1核1GB），用户要求用免费版 `0.5核 128MB`
- **持久化方案**：ChromDB SQLite 文件 → 需改为 numpy .npy + JSON

### CloudBase 免费版规格
| 项 | 值 |
|---|---|
| CPU | 0.5 核 |
| 内存 | 128 MB |
| 磁盘 | 512 MB（容器实例） |
| COS 挂载 | 免费 5GB 存储桶 |

### 内存目标拆解

| 组件 | 当前 | 目标 | 手段 |
|---|---|---|---|
| FastAPI + uvicorn | ~30MB | ~30MB | 保留 |
| numpy | ~20MB | ~20MB | 保留（向量计算基础） |
| jieba | ~10MB | ~10MB | 保留（中文分词） |
| chromadb | ~100MB | **0** | 移除，自实现 SimpleVectorStore |
| scikit-learn | ~50MB | **0** | 移除，自实现 simple_tfidf() |
| rank-bm25 | ~10MB | **0** | 移除，自实现 simple_bm25() |
| requests + pypdf | ~15MB | ~15MB | 保留 |
| **合计** | **~225MB** | **~75MB** | — |

---

## 要改的文件

| 文件 | 变更 |
|---|---|
| `ltc-rag-bot/requirements.txt` | 大砍依赖：移除 chromadb、scikit-learn、rank-bm25、scipy |
| `ltc-rag-bot/app.py` | 核心改造：自实现 SimpleVectorStore + TF-IDF + BM25，替换所有 chromadb/scikit-learn/rank-bm25 调用 |
| `ltc-rag-bot/cloudbaserc.json` | 改 cpu/mem 为免费版规格 |
| `ltc-rag-bot/Dockerfile` | 加 `--no-cache-dir` 清理 apt 缓存减小镜像 |

---

## 实现步骤

### Step 1: 改 requirements.txt（11 → 7 包）

**移除**：
- `chromadb>=1.5.0`
- `scikit-learn>=1.5.0`
- `rank-bm25>=0.2.0`
- `scipy`（sklearn 隐含依赖，连带砍）

**保留**：
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.12
requests>=2.32.0
pypdf>=5.0.0
python-dotenv>=1.0.0
jieba>=0.42.0
numpy>=1.26.0
```

### Step 2: app.py 大改造（核心工作）

#### 2.1 新增依赖导入替换

移除：
```python
import chromadb
from chromadb.utils import embedding_functions
import rank_bm25
from sklearn.feature_extraction.text import TfidfVectorizer
```

新增：
```python
import numpy as np
import jieba
import math
```

#### 2.2 自实现 `simple_tfidf()`（~25 行）

用 jieba 分词 + numpy 频率统计，实现 TF-IDF 向量化：
```python
def _tfidf_embed(text: str, vocab: dict) -> np.ndarray:
    """单文本 TF-IDF 嵌入 —— 输入词表 → 输出向量"""
    tokens = _chinese_tokenize(text)
    vec = np.zeros(len(vocab), dtype=np.float32)
    for t in tokens:
        idx = vocab.get(t)
        if idx is not None:
            vec[idx] += 1
    # IDF 加权（从 vocab 里读文档频率）
    for i, df in enumerate(vocab.get("_df", [])):
        if df > 0:
            vec[i] = vec[i] * math.log((vocab["_n_docs"] + 1) / (df + 1) + 1)
    # L2 归一化
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec
```

#### 2.3 自实现 `simple_bm25()`（~35 行）

numpy 实现 BM25Okapi：
```python
def _bm25_score(query_tokens, corpus_tokens, k1=1.5, b=0.75):
    """BM25 打分 —— 纯 numpy"""
    avg_len = np.mean([len(c) for c in corpus_tokens])
    n_docs = len(corpus_tokens)
    # 计算文档频率
    df = {}
    for c in corpus_tokens:
        for t in set(c):
            df[t] = df.get(t, 0) + 1
    # BM25 核心公式
    scores = []
    for doc in corpus_tokens:
        dl = len(doc)
        score = 0.0
        for qt in query_tokens:
            f = doc.count(qt)
            if f == 0:
                continue
            idf = math.log((n_docs - df.get(qt, 0) + 0.5) / (df.get(qt, 0) + 0.5) + 1)
            denom = f + k1 * (1 - b + b * dl / avg_len) if avg_len > 0 else f + k1
            score += idf * f * (k1 + 1) / denom
        scores.append(score)
    return scores
```

#### 2.4 新增 `SimpleVectorStore` 类（~120 行）

基于 numpy 数组 + JSON 文件的轻量向量存储，替代 ChromaDB：

**文件结构**（持久化到 `/mnt/chroma/`）：
```
vectors.npy          # numpy 矩阵 (n_docs × n_features, float32)
metadata.json        # [{id, source, text, strategy}, ...]
vocab.json           # {token: index, _df: [...], _n_docs: N}
```

**API 对齐 ChromaDB Collection**：
- `add(documents, ids, metadatas)` → 计算 TF-IDF → 追加到 vectors.npy → 更新 metadata.json
- `query(query_texts, n_results, include, where)` → 计算 query 向量 → 余弦相似度 → 可选 BM25 → RRF 融合
- `get(ids, limit, offset, where, include)` → 从 metadata.json 过滤
- `update(ids, documents, metadatas)` → 覆盖对应行
- `delete(ids)` → 删除对应行 + 重新保存
- `count()` → 返回 len(metadata)

#### 2.5 替换 ChromaDB 初始化

原来：
```python
CHROMA = chromadb.PersistentClient(path=CHROMA_PATH)
COL = CHROMA.get_or_create_collection(...)
```

现在：
```python
VECTOR_STORE = SimpleVectorStore(path=CHROMA_PATH)
print(f"[init] SimpleVectorStore ready · path={CHROMA_PATH} · docs={VECTOR_STORE.count()}")
```

#### 2.6 替换所有 COL 调用

| 原调用 | 替换为 |
|---|---|
| `COL.count()` | `VECTOR_STORE.count()` |
| `COL.add(documents, ids, metadatas)` | `VECTOR_STORE.add(documents, ids, metadatas)` |
| `COL.query(query_texts, n_results, include)` | `VECTOR_STORE.query(query_texts, n_results, include, where)` |
| `COL.get(ids/limit, where, include)` | `VECTOR_STORE.get(ids, limit, offset, where, include)` |
| `COL.update(ids, documents, metadatas)` | `VECTOR_STORE.update(ids, documents, metadatas)` |
| `COL.delete(ids)` | `VECTOR_STORE.delete(ids)` |

#### 2.7 移除 SentenceTransformer 相关代码

- 删掉 `_init_embedding()` 函数（第32-56行）
- 删掉 `EMBEDDING_NAME` 变量
- 在 health() 端点里把 `embedding` 字段改为 `"TF-IDF + Jieba (自实现, 零模型下载)"`

### Step 3: cloudbaserc.json 改免费版规格

```json
"cloudrun": {
  "cpu": 0.5,
  "mem": 128,
  ...
}
```

### Step 4: Dockerfile 瘦身

- 加 `apt-get clean && rm -rf /var/lib/apt/lists/*`（已有，但确保）
- 确认 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`（保留，防止意外触发模型下载）

---

## 依赖与注意事项

1. **向量检索精度**：TF-IDF 比 SentenceTransformer 语义理解弱，但对中文关键词匹配足够 + BM25 兜底 + RRF 融合，效果可接受
2. **向量维度**：TF-IDF 维度 = 词表大小（通常 300-2000 维），比 bge-small-zh-v1.5 的 512 维更可控
3. **元数据过滤**：从 ChromaDB 原生 where 过滤 → Python list comprehension 过滤（数据量小时无差别）
4. **持久化**：numpy .npy 二进制比 SQLite 更轻量，JSON 元数据可读可调试
5. **COS 挂载**：路径不变（/mnt/chroma），挂载方式不变，只是文件类型变了
6. **向下兼容**：门户前端（portal/index.html）不需要改，因为 API 接口保持不变

---

## 验证步骤

### 本地（开发机）
```bash
cd ltc-rag-bot
pip install -r requirements.txt   # 确认 7 个包能装完，无 chromadb/sklearn 残留
python -c "import app"            # 确认无 import 错误
python -c "from app import VECTOR_STORE; print(VECTOR_STORE.count())"  # 确认初始化成功
```

### CloudBase 部署后
```bash
# 健康检查
curl https://<cloudbase-url>/health
# 期望: {"status":"ok", "version":"2.0", "docs_count":0, "embedding":"TF-IDF + Jieba ...", ...}

# 测试写入
curl -X POST https://<cloudbase-url>/ingest-text \
  -H "Content-Type: application/json" \
  -d '{"text":"我们的产品支持7×24小时响应", "source":"test"}'

# 测试检索
curl -X POST https://<cloudbase-url>/query \
  -H "Content-Type: application/json" \
  -d '{"question":"你们的产品有什么服务"}'

# 检查启动日志是否出现:
#   [init] SimpleVectorStore ready · path=/mnt/chroma · docs=0
#   Uvicorn running on http://0.0.0.0:8080
```

### 内存验证
在 CloudBase 控制台 → 云托管 → 服务详情 → 监控图表，确认内存稳定在 80MB 以内（128MB 限制留 50MB 余量给系统）。

---

## 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| TF-IDF 语义不如 SentenceTransformer | 检索精度略降 | BM25 + RRF 融合补足关键词召回；后续可考虑接入云端 embedding API（如智谱 text-embedding-3） |
| numpy .npy 文件损坏 | 数据丢失 | SimpleVectorStore.save() 写临时文件再 rename（原子写入）；启动时自动检测 .npy 完整性 |
| 词表膨胀 | 向量维度增加 → 内存涨 | 限制 vocab 最大 20000 词，低频词截断 |
| COS 挂载延迟 | 启动时读 metadata.json 慢 | 启动时异步预加载 + 超时降级（本地 fallback 目录） |
| 前端 portal 依赖元数据过滤 | 分页慢 | 数据量 <1000 条时 Python list filter 完全够用；超量后考虑分页加载 |

---

## 执行顺序（有严格依赖）

1. **requirements.txt** — 先砍依赖，保证本地环境也干净
2. **app.py** — 大改造核心逻辑（~400 行重写）
3. **cloudbaserc.json** — 改规格
4. **Dockerfile** — 确认瘦身（已有基本到位）
5. **本地验证** — `pip install -r` + `python -c "import app"`
6. **推 GitHub + CloudBase 重建** — 用户操作
7. **部署后验证** — curl /health + /ingest-text + /query
8. **内存监控** — 确认 80MB 内稳定
