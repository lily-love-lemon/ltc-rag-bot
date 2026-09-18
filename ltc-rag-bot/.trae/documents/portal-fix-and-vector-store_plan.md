# Portal 404 修复 + 向量库持久化方案

## 一、Portal 404 问题

### 根因
后端只注册了 `@app.get("/")` 返回 portal HTML，用户访问 `/portal` → FastAPI 返回 `{"detail":"Not Found"}`。

```python
# 当前代码（第 768 行）
@app.get("/")
async def portal():
    portal_path = Path(__file__).parent / "portal" / "index.html"
    ...
```

### 修复（1 个文件，2 行）
在 `app.py` 的 `@app.get("/")` 下方**新增**：
```python
@app.get("/portal")
async def portal_alt():
    """Portal 备用路径 —— 307 redirect 到 /"""
    return RedirectResponse(url="/", status_code=307)
```
> 或直接复制同样的 FileResponse 逻辑。用 redirect 更干净，portal 里的 API 相对路径也不会出错。

### 验证
- `curl $URL/portal` → 返回 portal HTML 内容（不是 JSON）
- 浏览器打开 `$URL/portal` → 正常显示 Portal 界面

---

## 二、向量库持久化方案 · 深度对比

### 当前状态
- **存储引擎**：自实现 `SimpleVectorStore`（numpy `.npy` + `metadata.json` + `vocab.json`），零依赖
- **持久化路径**：`CHROMA_PATH`，当前指向容器本地磁盘 `/app/chroma`（因为 `/mnt/chroma` 不存在）
- **核心代码已支持 COS 挂载**（第 466 行）：
  ```python
  CHROMA_PATH = os.environ.get("CHROMA_PATH") or ("/mnt/chroma" if os.path.isdir("/mnt/chroma") else _default)
  ```
  → 如果 `/mnt/chroma` 目录存在，SimpleVectorStore 自动存那里
  → **COS 挂载成功 = 零代码改造 = 数据立即持久化**

### 方案对比（按零预算/低费用排序）

#### 🏆 方案 A：CloudBase COS 挂载（零改造，强烈推荐）

| 项目 | 详情 |
|---|---|
| **费用** | COS 标准存储 0.118 元/GB/月；我们几百条文档 ≈ 10MB → **每月约 0.001 元**，基本免费 |
| **写入/读取** | COS 标准下载 0.15 元/GB；几百条文档全加载 ≈ 读 10MB → 忽略不计 |
| **改造量** | **0 行代码** —— 当前代码已自动检测 `/mnt/chroma` |
| **操作** | CloudBase 控制台 → 云托管 → ltc-rag-bot → 服务设置 → 存储卷 → 添加 COS bucket 挂载到 `/mnt/chroma` |
| **优点** | 零代码零学习成本；数据跟着 COS bucket 走；CloudBase 和 COS 同区域延迟极低 |
| **缺点/坑** | COS FUSE 挂载的写入一致性：SimpleVectorStore 用原子 rename（写 .tmp 再 rename），COS 最终一致性可能导致 rename 短暂不可见 → 需要测试；CloudBase 免费版是否支持挂载？（需验证） |
| **容量上限** | COS 无限；几百条文档撑死几 MB，不用担心 |

> 💡 **关键**：当前代码的 `CHROMA_PATH` 检测逻辑已经写好了！只要 CloudBase 控制台挂载成功，容器重启后 SimpleVectorStore 就会自动把数据写到 `/mnt/chroma`。

#### 🥈 方案 B：Qdrant Cloud 免费版（永久免费，低改造）

| 项目 | 详情 |
|---|---|
| **费用** | **$0** 永久免费（无需信用卡） |
| **资源** | 0.5 vCPU / 1GB RAM / 4GB 磁盘 / 1 节点 |
| **容量** | ~1M 768 维向量（我们几百条文档 ≈ 几千向量，零压力） |
| **改造量** | 中 —— 需要把 `SimpleVectorStore` 抽象成 `VectorStore` 接口，增加 `QdrantVectorStore` 实现 |
| **坑** | 闲置 1 周自动暂停，4 周后删除数据 → 必须定期访问（或设个 cron 定时 ping）；免费集群只能选有限的区域；免费集群无备份/监控 |
| **API 稳定性** | 生产级 Rust 引擎；P99 延迟 <100ms；支持混合检索（BM25 + dense） |
| **海外网络** | Qdrant Cloud 服务器在海外（AWS/GCP/Azure），从 CloudBase 大陆容器访问可能有延迟 → 实测确认 |

#### 🥉 方案 C：Zilliz Cloud（Milvus 托管）免费版

| 项目 | 详情 |
|---|---|
| **费用** | **$0** 永久免费 + 注册送 $100 credits（30 天有效） |
| **资源** | 5GB 存储 + 2.5M vCUs/月 |
| **改造量** | 中 —— 同 Qdrant，需换 SDK + adapter |
| **坑** | 闲置暂停策略同 Qdrant；Milvus 对小规模数据过重；免费集群无 HA；Serverless 集群是按量付费（虽然有免费额度） |
| **适合** | 未来扩到百万级向量时；当前阶段过重 |

#### 方案 D：Pinecone Starter 免费版

| 项目 | 详情 |
|---|---|
| **费用** | **$0** |
| **资源** | 2GB 存储 / 250K 向量；Serverless 按量（read $0.04/M，write $2/M，storage $0.33/GB/月） |
| **坑** | 免费版只支持 Starter 级别（受限的 QPS、有限索引数）；海外服务器；Builder 计划 $20/月 flat fee（超出就锁不是按量付）；有月费门槛才得 backup 功能 |
| **不选原因** | 相比 Qdrant/Zilliz 免费版，Pinecone 免费版容量更小（2GB vs 4GB/5GB）；付费起步 $20/月是硬性门槛 |

#### 方案 E：自托管 ChromaDB/Qdrant 到 CloudBase

| 项目 | 详情 |
|---|---|
| **费用** | 0（用 CloudBase 免费容器） |
| **坑** | CloudBase 免费版只有 0.5 核 128MB —— 跑 FastAPI 服务都紧张，再加 ChromaDB server 肯定 OOM；需要开第二个 CloudBase 服务 → 0.5 核不够用 ChromaDB 的 embedded 模式可以但不能并发访问 |
| **不选原因** | 128MB OOM；维护复杂度高 |

#### 方案 F：pgvector（Postgres 扩展）

| 项目 | 详情 |
|---|---|
| **费用** | CloudBase 免费 Postgres 500MB？或自建 |
| **坑** | CloudBase 免费 Postgres 配额有限；需要维护 Postgres 实例；向量搜索性能不如专用库；改造量大 |
| **不选原因** | CloudBase 免费版 Postgres 配额紧；对我们的规模是杀鸡用牛刀 |

### 最终推荐：**方案 A（COS 挂载）** —— 零改造零费用零学习成本

| 决策因素 | COS 挂载 | Qdrant | Pinecone | Zilliz |
|---|---|---|---|---|
| 改造量 | **0 行代码** | ~200 行 adapter | ~200 行 | ~200 行 |
| 月费 | **≈ ¥0.001** | **$0** | **$0（$20/月起付）** | **$0** |
| 闲置删除 | ❌ 永不会 | ✅ 1 周暂停 4 周删 | ？ | ✅ 类似 |
| 大陆访问 | **本地盘速度** | 海外有延迟 | 海外有延迟 | 海外有延迟 |
| 数据安全 | **自己控制** | 托管 | 托管 | 托管 |
| 复杂度 | **最低** | 中 | 中 | 中 |

### COS 挂载如果 CloudBase 免费版不支持怎么办？

**Plan B：退而求其次 —— Qdrant Cloud 免费版**，改造量可控：
1. CloudBase 控制台新建 Qdrant Cloud 账号 → 免费集群 → 拿到 API URL + Key
2. 代码加 `QdrantVectorStore` 类（实现现有 `SimpleVectorStore` 的 `upsert/query/delete` 接口）
3. 通过环境变量 `VECTOR_STORE_BACKEND=qdrant` 切换
4. 加一个 cron job 每周 ping CloudBase webhook 端点保持 Qdrant 免费集群活跃

---

## 三、实施步骤

### 代码变更（必须，2 处）
1. `app.py` — 新增 `/portal` 路由（redirect 到 `/`）
2. Portal 404 立即解决

### 用户操作（CloudBase 控制台）
1. **COS 挂载验证**（先试）：CloudBase 控制台 → 云托管 → ltc-rag-bot → 服务设置 → 存储卷 → 添加 COS 挂载到 `/mnt/chroma` → 部署新版本 → 验证 `/health` 里 chroma_path 变成 `/mnt/chroma`
2. 挂载成功 → 直接重新上传知识库（之前容器重启已丢失）
3. 挂载失败 → 告诉我，我改代码接 Qdrant Cloud

### 验证清单
- `curl $URL/portal` → 200 + HTML 内容
- Portal 浏览器打开 → 正常显示上传界面
- `curl $URL/health` → `chroma_path: /mnt/chroma`（如果 COS 挂载成功）
- 重启容器后知识库数据仍在（持久化验证）
- 飞书群 @机器人 → 收到基于知识库的回复
