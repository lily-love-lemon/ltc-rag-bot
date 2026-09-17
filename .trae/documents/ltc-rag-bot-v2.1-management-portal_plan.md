# LTC RAG Bot v2.1 — 企业知识库管理门户升级计划

**参考**：武哥聊编《AI Agentic RAG高级企业知识库平台》核心功能
**策略**：抄精华、砍重量 —— 用 ChromaDB + 单文件 HTML 做 ChromaDB 版本的管理台
**当前基线**：app.py 350 行、单 collection、单 HTML 门户（327 行）

---

## 一、参考课程 vs 我们的 v2.1 功能对照

| 课程功能 | 我们 v2.1 做法 | 技术代价 |
|---------|---------------|---------|
| 知识库管理（多库隔离） | ChromaDB 多 collection | 改路由 + 加 `get_or_create_collection` |
| 文档列表 + 解析状态 | 每文档一个 `source`，用 metadata 存状态 | POST 时加 metadata，GET 时按 source 分组 |
| 手动触发解析/向量化 | DELETE 旧 → POST 重新 ingest | 一个 `POST /kb/{name}/reparse` |
| 切分策略配置 | chunk_size / overlap / 模式 → 存 `.json` | `configs/kb_strategies.json` |
| 切片预览 + 手动编辑 | GET + PATCH `/kb/{name}/chunks/{id}` | 加 2 个端点 |
| Prompt 模板管理 | 页面读写 `prompts/fabe.txt` | GET/PUT `/prompts/{name}` |
| 检索测试台（多阶段日志） | query 返回每步耗时和分数 | 改 query 响应加 debug 字段 |
| SSE 流式回答 | StreamingResponse + call_llm streaming | 改 call_llm 支持 stream=True |
| BM25 + RRF 混合检索 | `pip install rank-bm25` + 纯 Python RRF | 加检索路由 |
| Reranker 重排 | **暂时不做**（加 400MB 模型 + 慢） | 留到 v2.2 |
| LangGraph Agent | **暂时不做** | 留到 v3 |
| PostgreSQL + PGVector | **用 ChromaDB** | 不换库 |
| Vue3 30 页前端 | **单 HTML 多 tab** | 重写 portal |

---

## 二、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `app.py` | 重写 → ~500 行 | 多知识库路由 + 混合检索 + Prompt 管理 + SSE |
| `portal/index.html` | 重写 → ~600 行 | 多 tab 管理台 |
| `configs/kb_strategies.json` | **新增** | 切分策略配置 |
| `configs/kb_mapping.json` | **新增** | 知识库→collection 绑定 |
| `prompts/fabe.txt` | **新增** | FABE Prompt 模板（文件化） |
| `requirements.txt` | 更新 | 加 rank-bm25 |
| `.env` | 小幅更新 | 可选 RERANKER_MODEL |

### 不创建的文件（保持轻量化）
- ❌ MySQL 数据库（0 张业务表）
- ❌ PostgreSQL + PGVector
- ❌ LangChain/LangGraph（原生 ChromaDB + 纯 Python）
- ❌ Vue/Vite 工程
- ❌ 独立的策略数据库表
- ❌ OCR 引擎（RapidOCR）

---

## 三、后端升级步骤（app.py）

### Step 1 · 多知识库路由骨架

**逻辑**：用 `get_or_create_collection(name=kb_name)` 做隔离。每个知识库有独立 collection、独立 embedding（可选）。

**新增路由结构**：
```
GET    /kb                           → 列出所有知识库
POST   /kb                           → 新建知识库（{name, description, strategy, embedding}）
GET    /kb/{name}                    → 知识库详情（文档数、切片数、配置）
DELETE /kb/{name}                    → 删除整个知识库（谨慎）
GET    /kb/{name}/docs               → 文档列表（按 source 分组，带切片数、上传时间、解析状态）
POST   /kb/{name}/ingest             → 上传文件入库（复用 ingest 逻辑）
POST   /kb/{name}/ingest-text        → 录入文本
POST   /kb/{name}/reparse            → 重新切片 + 重新向量化整个知识库
GET    /kb/{name}/chunks             → 所有切片预览
PATCH  /kb/{name}/chunks/{id}        → 手动编辑单个切片
DELETE /kb/{name}/chunks/{id}        → 删除单个切片
POST   /kb/{name}/query              → 问答
POST   /kb/{name}/query/stream       → SSE 流式问答
```

**关键代码位置**：替换当前单一的 `COL = CHROMA.get_or_create_collection(name="ltc_knowledge")`

### Step 2 · 切片策略参数化

**逻辑**：把硬编码的 `_chunk_text()` 升级成可配置的策略函数。

```python
# 新增：configs/kb_strategies.json
{
  "default": {
    "mode": "paragraph",           # paragraph / token / sentence
    "chunk_size": 500,            # token 模式下每块 token 数
    "chunk_overlap": 50,          # 相邻块重叠 token 数
    "min_chunk_len": 20,          # 最小切片字符数
    "max_chunk_len": 2000         # 超过就强制切
  },
  "sales-scripts": {
    "mode": "sentence",
    "min_chunk_len": 15,
    "max_chunk_len": 500
  }
}
```

**代码改动**：`_chunk_text(content, strategy="default")` → 从 JSON 读参数

### Step 3 · Prompt 模板文件化

**逻辑**：把 FABE prompt 从 app.py 硬编码 → `prompts/fabe.txt` 文件，页面能改，不用重启。

```python
# 新增两个端点
@app.get("/prompts/{name}")          → 读文件内容
@app.put("/prompts/{name}")          → 写文件内容
```

**默认 prompts/fabe.txt**（内容就是我们 query() 里硬编码的那段）

### Step 4 · BM25 + RRF 混合检索（可选开）

**逻辑**：向量检索和 BM25 并行跑，RRF 按名次融合。

```
用户问题 → 向量化 → 向量检索 top-k
        → jieba分词 → BM25检索 top-k
        → RRF 融合（取两边分数前 top-k，按名次分配 RRF 分）
        → [可选] reranker 重排
        → 返回结果
```

**新依赖**：`pip install rank-bm25 jieba`

**开关**：`.env` 里加 `ENABLE_BM25=true`，默认开。

### Step 5 · SSE 流式回答

**逻辑**：FastAPI StreamingResponse + 迭代器产出。call_llm 改 streaming=True。

```python
@app.post("/kb/{name}/query/stream")
async def query_stream(request):
    async def generate():
        # 先产出检索结果
        yield json.dumps({"type": "sources", "data": sources}) + "\n"
        # 再产出 LLM token
        for token in call_llm_stream(...):
            yield json.dumps({"type": "token", "data": token}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

---

## 四、前端升级步骤（portal/index.html）

从 4 tab 升级到 **7 tab**：

```
┌─────────────────────────────────────────────────────────────┐
│ 🧠 LTC RAG Bot · 企业知识库管理门户 v2.1                        │
├─────────────────────────────────────────────────────────────┤
│ 📊 总览  📚 知识库  📄 文档  ✂️ 切片  🔍 检索测试  📝 Prompt  ⚙️ 设置  │
└─────────────────────────────────────────────────────────────┘
```

### Tab 1: 📊 总览（Dashboard）
- 知识库总数、文档总数、切片总数
- 每个知识库的状态小卡片（文档数、切片数、embedding）
- 最近上传 5 个文档

### Tab 2: 📚 知识库（对应课程"知识库管理"页）
- 知识库列表（名称、描述、文档数、切片数、创建时间）
- 新增知识库弹窗（选 embedding、选切分策略）
- 每个知识库 → 点进去跳到"文档" tab，筛选当前库

### Tab 3: 📄 文档（对应课程"文档管理"页）
- 文档列表（所属知识库、类型、大小、切片数、解析状态、上传时间）
- 上传按钮（选知识库 + 选文件）
- 每行操作：📄 预览 / 🔄 重新解析 / 🔢 重新向量化 / 🗑 删除

### Tab 4: ✂️ 切片（对应课程"片段管理"页）
- 切好的片段列表（按知识库筛选、按文档筛选）
- **手动编辑**：点某片 → 行内编辑文字
- **删除**：点垃圾桶
- 显示向量模型、相似度阈值

### Tab 5: 🔍 检索测试台（对应课程"检索测试"页）
- 输入问题
- 选择知识库
- **显示每阶段日志**：
  ```
  ✅ 向量化: 2ms
  ✅ 向量检索 top-5: 3ms → [片段A, 片段B, 片段C...]
  ✅ BM25 检索 top-5: 1ms → [片段D, 片段A, 片段E...]
  ✅ RRF 融合: 1ms
  ✅ LLM 生成 (FABE): 2.1s → 流式显示
  ```
- **SSE 流式**：LLM 回答逐字出来

### Tab 6: 📝 Prompt（对应课程"Prompt模板"页）
- 列出所有 Prompt 文件
- 点进去编辑（textarea）
- 保存即生效（写入 prompts/fabe.txt）

### Tab 7: ⚙️ 设置
- API Key 管理
- LLM 后端切换（DeepSeek / Ollama / 模板）
- BM25 开关
- Embedding 模型切换（bge-small-zh / DefaultEmbeddingFunction）
- 当前后端信息

---

## 五、配置文件

### configs/kb_strategies.json
切分策略配置。默认 3 套：default（段落）、sales（句子+短片段）、tender（token+overlap）。

### configs/kb_mapping.json
知识库元信息。记录每个知识库绑定的 embedding 和切分策略。

### prompts/fabe.txt
FABE Prompt 模板。可通过 Tab 6 编辑。

---

## 六、实施顺序

```
Step 0 · 备份当前 app.py + portal
Step 1 · 写配置文件（configs/ + prompts/）
Step 2 · app.py 多知识库路由（核心：COL → _get_col(kb_name)）
Step 3 · 切片策略参数化
Step 4 · BM25 + RRF 混合检索
Step 5 · Prompt 文件化 + 管理端点
Step 6 · SSE 流式回答
Step 7 · portal/index.html 重写 7 tab 管理台
Step 8 · 端到端验证（health → 多知识库 → ingest → query → stream → 切片编辑）
Step 9 · 飞书端到端不回归
```

---

## 七、验证矩阵

| # | 测试 | 预期 |
|---|------|------|
| 1 | `curl /kb` 列出所有 | 返回至少 1 个知识库 |
| 2 | `POST /kb` 新建知识库 | 创建成功，文档数 0 |
| 3 | 往 A 库 ingest → 往 B 库 ingest | 各自隔离，不串数据 |
| 4 | `PATCH /kb/X/chunks/id` 改切片 | 数据库里更新 |
| 5 | 检索测试台选 BM25 开关 | 关掉时只有向量，开时两路 |
| 6 | 编辑 prompts/fabe.txt → 新 query | 用新 prompt 回答 |
| 7 | SSE 问答 | 逐字输出，最终有 done 事件 |
| 8 | 飞书 @机器人 → 走新 /kb query | 不回归，照样回 |

---

## 八、风险与处理

| 风险 | 处理 |
|------|------|
| ChromaDB 多 collection 性能 | MVP 级别（<10 个库，每个 <5000 条）ChromaDB 完全能扛 |
| BM25 在 ChromaDB 的 chunks 上分词不准 | 先用简单 jieba 分词，不够精确但够用 |
| SSE 在飞书 webhook 里不需要（飞书用同步 HTTP）| SSE 只给 Web 门户 / 独立用户用 |
| Prompt 文件被意外删 → query 崩 | 读 prompt 时 try/except，崩了就用硬编码默认 |
| Python 3.14 + rank-bm25 兼容性 | 装了试，不行就换 BM25FromScratch |

---

## 九、执行量估算

- app.py 重写：**1 次 Write**（约 500 行）
- portal/index.html 重写：**1 次 Write**（约 600 行）
- 配置文件：**3 个 Write**（小）
- 新依赖：**1 个 pip install**（rank-bm25 + jieba）
- 验证：**curl 一轮**

**核心优势**：我们不需要数据库迁移、不需要前端构建、不需要 LangChain 重装、不需要 Postgres。**这全部能在 30 分钟内完成** —— 因为 ChromaDB 的多 collection 天然就是隔离的。
