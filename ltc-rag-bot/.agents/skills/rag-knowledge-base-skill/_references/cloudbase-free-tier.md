# CloudBase 免费版硬性限制 & Workaround

> CloudBase 免费版配置: **0.5 核 vCPU · 128MB 内存 · 512MB COS 存储**
> 以下是每个限制的实测 Workaround。

---

## 限制 1: 内存 128MB（最狠的一个）

### 实测内存开销

| 组件 | 内存 | 结论 |
|---|---|---|
| Python 进程（干净） | ~20MB | OK |
| FastAPI + uvicorn | ~10MB | OK |
| jieba 词表加载 | ~5MB | OK |
| **ChromaDB** | **~150MB** | **❌ OOM** |
| sentence-transformers 模型 | ~300MB | ❌ OOM |
| numpy + sklearn (TF-IDF) | ~30MB | OK |
| **自建 SimpleVectorStore** | **< 10MB** | ✅ |

### Workaround: 砍掉所有第三方向量库

**自建 SimpleVectorStore**（~300 行 Python）:
- TF-IDF + cosine similarity（sklearn 已含）
- BM25（纯 Python 实现）
- JSON metadata + numpy .npy 向量
- **加载 < 50MB，运行稳定**

> **结论**: CloudBase free tier 跑 Chromadb + LangChain = 必挂。自实现向量存储是唯一路径。

---

## 限制 2: COS 挂载异步化

### 问题

容器启动时 `/mnt/chroma`（COS 挂载）可能还没挂好（异步，通常 1-3s）。

### Workaround

```python
import os, time

PERSIST_DIR = "/mnt/chroma"

def _get_store_path():
    # 最多等 30s
    for _ in range(30):
        if os.path.isdir(PERSIST_DIR):
            return PERSIST_DIR
        time.sleep(1)
    # fallback 本地
    local = os.path.join(os.getcwd(), "chroma_data")
    os.makedirs(local, exist_ok=True)
    return local
```

**启动时日志必须打印**:
```python
store_path = _get_store_path()
print(f"[init] vector store path: {store_path}")
```

---

## 限制 3: 单实例

免费版只有 1 个实例，不能横向扩。
→ 没问题：单机 ~50 个并发完全够用。

---

## 限制 4: 沙箱网络 egress

从远程 sandbox（比如 AI agent 执行环境）访问 CloudBase 网关 `tcloudbasegateway.com` 可能超时。

### Workaround

| 操作 | 通路 | 是否受限 |
|---|---|---|
| `tcb cloudrun deploy` | 走 CloudBase 云端鉴权 | ✅ 不受限 |
| `tcb cloudrun record list` | 同上 | ✅ 不受限 |
| `tcb cloudrun service get` | 直接调网关 | ❌ 可能 FetchError |
| curl 服务 URL | 直接调网关 | ❌ 可能超时 |

**绕过**: URL 从 `record list` 拿 RunId 手动拼，或直接看 tcloudbase 控制台。

---

## 限制 5: Dockerfile 构建时间

免费版构建时间约 2-5 分钟。**别用 `--force` 但忘选灰度** → 会交互式等待。

### 正确的部署命令

```bash
tcb cloudrun deploy -s <service> -e <envId> --force 2>&1 <<< $'\n'
```

`--force` 跳过确认，heredoc 自动选灰度 = No。

---

## 限制 6: 冷启动

免费版容器闲置时会被回收，第一个请求有 ~2-5s 冷启动时间。
→ 对机器人问答可以接受，Portal 访问加个 loading 态。
