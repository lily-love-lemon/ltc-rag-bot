# LTC RAG Bot MVP 升级实施计划

**版本**：v2.0 · 2026-09-17
**定位**：从"原型验证"升级为"可日常使用"的 MVP
**前置对话**：FDE 导师已完整讲解 Embedding/LLM/FABE/鉴权 概念（见上文）
**核心原则**：每一步都要告知位置、路径、逻辑

---

## 一、当前基线（已完成 ✅）

| 模块 | 状态 | 位置 |
|------|------|------|
| FastAPI 后端 | ✅ 运行中 | `/workspace/ltc-rag-bot/app.py` · port 8001 |
| ChromaDB 向量库 | ✅ 有 35 条测试文档 | `/workspace/ltc-rag-bot/chroma_data/` |
| Embedding | ⚠️ all-MiniLM-L6-v2（英文弱） | `app.py` L18-20 |
| RAG 检索 | ✅ 功能通，中文命中率待提升 | `app.py` L95-132 |
| 飞书 Webhook | ✅ 框架通，challenge 验证过 | `app.py` L134-189 |
| LLM 回答 | ❌ 纯裸拼接，无 LLM | `app.py` L120-126 |
| 鉴权 | ❌ 无 | 缺失 |
| 知识库管理门户 | ❌ 无 | 缺失 |
| FABE 格式化 | ❌ 只有提示语，无真 LLM 生成 | `app.py` L124-125 |

---

## 二、本次升级目标

把 MVP 从"技术 demo"升级为可用于真实销售场景：

| 升级项 | 目标 | 对应问题 |
|--------|------|---------|
| **U1. Embedding 换 bge-small-zh-v1.5** | 中文命中率从 ~60% 提升到 ~85% | Q1 |
| **U2. 接入 Ollama / 云端 LLM** | 生成真正的 FABE 销售话术 | Q2 + Q3 |
| **U3. 添加 API Key 鉴权 middleware** | 公网部署安全 | Q4 |
| **U4. 添加知识库管理门户** | 非技术人员能上传/管理文档 | Q2 |
| **U5. 飞书凭证配置 + 验证 checklist** | 机器人真正在群里能用 | Q4 |

---

## 三、实施步骤（按依赖顺序）

### Step 0 · 环境快照 + 备份（保护动作）

**位置**：`/workspace/ltc-rag-bot/`
**逻辑**：升级前备份当前代码和数据，万一升级炸了能回滚

```bash
# 备份当前 app.py
cp app.py app.py.bak.v1

# 备份向量库（换 embedding 后必须清空，但先备份以防万一）
mv chroma_data chroma_data.bak.v1

# 导出当前依赖清单
pip freeze > requirements.freeze.txt
```

**验证**：`ls -la app.py.bak.v1 chroma_data.bak.v1/` 都存在 ✅

---

### Step 1 · Embedding 换 bge-small-zh-v1.5（U1）

**位置**：`app.py` 第 18-20 行 + requirements.txt
**逻辑**：把 ChromaDB 默认英文 embedding 换成专门为中文训练的 bge-small-zh

**1.1 修改 app.py L18-20**

```python
# ====== 改前（英文弱）======
SENTENCE = embedding_functions.DefaultEmbeddingFunction()

# ====== 改后（中文强）======
from sentence_transformers import SentenceTransformer
print("[init] 加载 BAAI/bge-small-zh-v1.5（~100MB，专为中文训练）...")
SENTENCE = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="BAAI/bge-small-zh-v1.5",
    device="cpu",
)
print(f"[init] Embedding 就绪 · 模型=bge-small-zh-v1.5 · 设备=cpu")
```

**1.2 沙箱需要镜像（因为 huggingface.co 连不上）**

当前沙箱无法直连 HuggingFace。两个解法：
- **方案 A（本地 Mac）**：Mac 能直接连，下载后会缓存在 `~/.cache/huggingface/`，再整个拷贝到沙箱
- **方案 B（沙箱内用 ModelScope 镜像）**：阿里云 ModelScope 有 bge-small-zh 的镜像

**1.3 验证**

```bash
# 重启服务（自动触发新 embedding 加载）
# 健康检查应显示新文档数（因为 chroma_data 被备份清空了）
curl http://localhost:8001/health

# 重新灌测试数据（和之前一样）
curl -X POST http://localhost:8001/ingest-text -H "Content-Type: application/json" \
  -d '{"source":"产品手册-电池","text":"三重保温技术，零下40度正常工作24小时，2024冬奥会供应商"}'

# 问同一个问题，检查命中率是否提升
curl -X POST http://localhost:8001/query -H "Content-Type: application/json" \
  -d '{"question":"零下30度续航"}' | python3 -m json.tool
```

**成功标志**：相似度分数比之前（0.74）更高，至少 ≥ 0.80

**风险 & 兜底**：
- 如果沙箱始终连不上 HF → 暂时保留 DefaultEmbeddingFunction，等本地部署时再换
- 换 embedding 后必须清空 chroma_data → 已在 Step 0 备份，问题不大

---

### Step 2 · 接入 LLM（Ollama / 云端）+ FABE 生成（U2 核心）

**位置**：`app.py` 新增 `call_llm()` 函数 + 修改 `query()` 函数 L120-126
**逻辑**：把"裸拼接"换成真正 LLM 生成的 FABE 话术

**2.1 新增 `.env` 配置项**

```bash
# Ollama 本地模型
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# 云端 LLM（可选，优先级高于 Ollama）
DEEPSEEK_API_KEY=
KIMI_API_KEY=
```

**2.2 在 app.py 里新增 call_llm() 函数（放在 feishu_token() 后面）**

```python
def call_llm(prompt: str) -> str:
    """统一的 LLM 调用 —— 优先云端，fallback Ollama，再 fallback 模板"""
    # 优先级 1: DeepSeek（性价比最高）
    if os.environ.get("DEEPSEEK_API_KEY"):
        r = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=30,
        )
        return r.json()["choices"][0]["message"]["content"]
    
    # 优先级 2: Ollama 本地
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    try:
        r = requests.post(
            f"{ollama_url}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        return r.json().get("response", "")
    except Exception:
        pass
    
    # Fallback: 模板化回答（无 LLM 时）
    return _template_fallback(prompt)

def _template_fallback(prompt: str) -> str:
    """没有 LLM 时，用模板格式化检索结果"""
    # prompt 里已经带了检索到的片段，做基本排版
    return f"【RAG 检索结果 - 无 LLM 润色】\n\n{prompt}\n\n⚠️ 建议接入 Ollama qwen2.5:7b 获得更好效果"
```

**2.3 修改 query() 函数 L120-126 —— 构造 FABE prompt + 调 LLM**

```python
# ====== 改前 ======
answer = (
    f"**问题**：{q}\n\n"
    f"**参考片段**：\n{context_block}\n\n"
    f"**建议话术**：基于以上检索，按 FABE 法则组织回答 — \n"
    f"Feature（特征）→ Advantage（优势）→ Benefit（利益）→ Evidence（证据）。"
)

# ====== 改后 ======
fabe_prompt = f"""你是一个专业的 B2B 销售话术专家。请基于以下参考内容，按 FABE 法则组织一段完整的销售回答。

规则：
1. 如果参考片段里没有足够信息回答，诚实说"知识库暂未收录相关内容"，不要编造
2. Feature 客观描述产品/服务的特征
3. Advantage 说明这个特征带来的优势（和竞品/旧方案比）
4. Benefit 一定要从**客户视角**描述利益（省多少钱/多少时间/降低什么风险）
5. Evidence 引用认证/案例/数据作为佐证
6. 回答用中文，口语化，符合销售对客户说话的风格

客户问题：{q}

参考内容：
{context_block}

请输出完整的 FABE 话术："""

llm_reply = call_llm(fabe_prompt)

answer = (
    f"**问题**：{q}\n\n"
    f"**🤖 AI 话术（FABE）**：\n{llm_reply}\n\n"
    f"**📎 参考片段**：\n{context_block}"
)
```

**2.4 验证**

```bash
# Ollama 没启动时，会走 template_fallback（也能跑）
# Ollama 启动 + 有模型时，会调真实 LLM
curl -X POST http://localhost:8001/query -H "Content-Type: application/json" \
  -d '{"question":"零下30度续航"}' | python3 -m json.tool
```

**成功标志**：
- 有 LLM 时 → 返回流畅的 FABE 四段式话术
- 无 LLM 时 → 返回带 ⚠️ 标记的模板化结果（不报错）

**风险 & 兜底**：call_llm 里用 try/except + 多级 fallback，任何一级挂了都不会让整个 query 崩。

---

### Step 3 · FastAPI API Key 鉴权 Middleware（U3）

**位置**：`app.py` L33 (`app = FastAPI(...)`) 之后新增
**逻辑**：在所有非飞书 webhook 的端点上加 API Key 验证

**3.1 导入依赖 + 定义鉴权**

```python
from fastapi.security import APIKeyHeader
from fastapi import Security, Depends

# 从 .env 读，没配就用开发模式默认值
API_KEY = os.environ.get("API_KEY", "dev-only-key-change-in-prod")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)):
    """API Key 鉴权依赖 —— 用于 /ingest /ingest-text /query"""
    # 开发模式下不强制鉴权（API_KEY 等于默认值时跳过）
    if API_KEY == "dev-only-key-change-in-prod":
        return True
    if not api_key or api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing API Key (X-API-Key header)")
    return True
```

**3.2 在需要鉴权的端点上加 Depends**

```python
@app.post("/ingest")
async def ingest(file: UploadFile, _: bool = Depends(verify_api_key)):  # ← 加这里
    ...

@app.post("/ingest-text")
async def ingest_text(body: dict, _: bool = Depends(verify_api_key)):  # ← 加这里
    ...

@app.post("/query")
async def query(body: dict, _: bool = Depends(verify_api_key)):  # ← 加这里
    ...

# /webhook 不加 —— 飞书自己做了签名验证
# /health 不加 —— 健康检查应公开
```

**3.3 .env 里加一行**

```bash
# 生产环境必须改！不改就是开发模式（跳过鉴权）
API_KEY=my-super-secret-key-2026
```

**3.4 验证**

```bash
# 没有 API Key 时（生产模式应返回 401）
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/query \
  -X POST -H "Content-Type: application/json" -d '{"question":"test"}'
# → 200（因为当前是开发模式默认 key）

# 配了真实 API_KEY 后，带 key 才能访问
curl -s -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: my-super-secret-key-2026" \
  -d '{"question":"test"}'
```

**设计决策**：开发模式（默认 key）自动跳过鉴权 —— 避免你本地调试时被挡住。生产环境改了 `.env` 的 API_KEY 就自动开启。

---

### Step 4 · 知识库管理门户（U4）

**位置**：`/workspace/ltc-rag-bot/portal/`（新建目录）
**逻辑**：一个单文件 HTML + 内联 JS，打开浏览器就能管理知识库

**4.1 为什么做这个？**

| 使用者 | 之前 | 之后 |
|--------|------|------|
| 你（开发者） | curl 命令行 | 浏览器拖文件 |
| 销售主管 | 不会 curl | 打开页面拖文件 |
| 新人 | 教他 curl | 直接用 |

**4.2 新增两个文件**

| 文件 | 作用 |
|------|------|
| `portal/index.html` | 单文件管理 UI：上传 + 文档列表 + 查询测试 |
| `app.py` 新增 `GET /docs` + `DELETE /doc/{id}` | 列出已入库文档 + 删除指定文档 |

**4.3 portal/index.html 功能清单**

- 📁 **上传区**：拖拽或点击选择文件（支持 .pdf/.md/.txt）
- 📋 **文档列表**：显示所有已入库的文档，每个有切片数、来源、删除按钮
- 🔍 **查询测试**：直接在页面输入问题，看到 RAG 检索结果 + LLM 回答
- 📊 **统计面板**：总文档数、总切片数、Embedding 模型信息
- 🔐 **API Key 设置**：页面输入 API Key（如果生产环境开启了）

**4.4 app.py 新增端点**

```python
# GET /docs — 列出已入库文档
@app.get("/docs")
async def list_docs(_: bool = Depends(verify_api_key)):
    """列出 ChromaDB 里所有文档（按 source 分组统计）"""
    result = COL.get(include=["metadatas"])
    # 按 source 聚合
    sources = {}
    for md in (result["metadatas"] or []):
        src = md.get("source", "unknown") if md else "unknown"
        sources[src] = sources.get(src, 0) + 1
    return {"total_chunks": COL.count(), "sources": sources}

# GET / — 返回 portal/index.html（方便访问）
from fastapi.responses import FileResponse
@app.get("/")
async def portal():
    portal_path = Path(__file__).parent / "portal" / "index.html"
    if portal_path.exists():
        return FileResponse(portal_path)
    return {"service": "LTC RAG Bot", "health": "ok"}
```

**4.5 验证**

```bash
# 打开门户
curl -s http://localhost:8001/ | head -20
# 应该返回 HTML 内容

# 浏览器打开
# http://localhost:8001/
# 上传文件 → 看到新文档 → 测查询
```

---

### Step 5 · 飞书配置 Checklist（U5）

**位置**：飞书开放平台 + `.env`
**逻辑**：把你"已有飞书机器人和 webhook"这个状态闭环

**5.1 必须完成的配置清单**

| # | 操作 | 在哪里 | 做什么 | 验证 |
|---|------|--------|--------|------|
| 1 | 填 App ID/Secret | `.env` | FEISHU_APP_ID + FEISHU_APP_SECRET | `echo $?` 空值？ |
| 2 | 配置事件订阅 URL | 飞书开放平台 → 事件订阅 | 填公网地址 + `/webhook` | 飞书发 challenge → 你的服务原样返回 ✅ |
| 3 | 加事件 | 飞书开放平台 → 事件 | `im.message.receive_v1` | 收到事件回调 |
| 4 | 配置消息接收权限 | 飞书开放平台 → 权限 | `im:message` / `im:message.send_as_bot` | 不配置发不出消息 |
| 5 | 暴露公网 URL | 本地 Mac 终端 | `cloudflared tunnel --url http://localhost:8001` | 拿到 `*.trycloudflare.com` |
| 6 | 发布应用版本 | 飞书开放平台 → 版本管理 | 创建新版本 → 申请审核 | 审核通过后才能在群里用 |
| 7 | 群里添加机器人 | 飞书群设置 → 群机器人 | 添加你创建的应用 | @机器人 有反应 |
| 8 | 端到端测试 | 飞书群 | @机器人 零下30度续航 | 收到自动回复 ✅ |

**5.2 飞书凭证检查（在沙箱或本地跑）**

```bash
source .env && curl -s -X POST \
  https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \
  -H "Content-Type: application/json" \
  -d "{\"app_id\":\"$FEISHU_APP_ID\",\"app_secret\":\"$FEISHU_APP_SECRET\"}"
# 应该返回 {"code":0,"tenant_access_token":"t-xxx",...}
# 如果返回 code != 0，说明 App ID/Secret 错了
```

**5.3 Webhook 本地验证（飞书还没配置公网时先测）**

```bash
# 模拟飞书事件（和之前一样）
curl -X POST http://localhost:8001/webhook -H "Content-Type: application/json" \
  -d '{"event":{"message":{"message_type":"text","content":"{\"text\":\"保修期多久\"}","chat_id":"oc_test"}}}'
# 应该返回 {"ok": true, "reply_preview": "..."}
```

---

## 四、实施后的架构总览

```
┌─────────────────────────────────────────────────┐
│                  升级后的 app.py                  │
│                                                   │
│  ┌─ Embedding ─────────────────────────────┐    │
│  │ BAAI/bge-small-zh-v1.5 (中文强)         │    │
│  └──────────────────┬─────────────────────┘    │
│                     │ 转向量                      │
│  ┌─ ChromaDB ───────▼──────────────────────┐    │
│  │ ltc_knowledge collection + 新向量        │    │
│  └──────────────────┬─────────────────────┘    │
│                     │ top-3 片段                  │
│  ┌─ LLM (可选) ─────▼──────────────────────┐    │
│  │ DeepSeek / Ollama qwen2.5:7b / 模板回退  │    │
│  │ → FABE 四段式话术                        │    │
│  └──────────────────┬─────────────────────┘    │
│                     │                              │
│  ┌─ API Key Middleware + .env ──────────────┐    │
│  │ /ingest /query 需 X-API-Key              │    │
│  │ /webhook 免鉴权（飞书自签）                │    │
│  │ /health / (portal) 免鉴权                 │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  端点:                                            │
│  GET  /              → 知识库管理门户 (portal/)   │
│  GET  /health        → 健康检查                   │
│  GET  /docs          → 已入库文档列表 + 统计      │
│  POST /ingest        → 上传文件入库（需 Key）      │
│  POST /ingest-text   → 录入文本入库（需 Key）      │
│  POST /query         → RAG + LLM 问答（需 Key）   │
│  POST /webhook       → 飞书事件（飞书自鉴）        │
└─────────────────────────────────────────────────┘
```

---

## 五、验证矩阵（每一步做什么测试）

| Step | 测试命令/动作 | 预期结果 | 失败说明什么 |
|------|--------------|---------|-------------|
| 1 (Embedding) | `curl /query` 问"零下30度" | 相似度 ≥ 0.80 | HF 模型下载失败，或切片有问题 |
| 2 (LLM) | `curl /query` 看回答内容 | 有 FABE 四段式，不是裸拼接 | Ollama 没启动 / API Key 错 |
| 3 (鉴权) | 不带 key 调 /ingest → 401 | 带 key → 200 | verify_api_key 没加对 |
| 4 (门户) | 浏览器打开 `http://localhost:8001/` | 看到上传 UI | portal/index.html 路径错 |
| 5 (飞书) | 飞书群 @机器人 | 收到自动回复 | 事件订阅 URL 没配对 |

---

## 六、已知限制 & 后续迭代

| 限制 | 影响 | 什么时候解 | 解法 |
|------|------|----------|------|
| 沙箱连不上 HF | 换 bge-small-zh 需本地镜像 | 本地 Mac 部署时自然解决 | 或用 ModelScope 镜像 |
| LLM 只有 Qwen 2B | 生成质量有限 | 接云端 API 或 Ollama 换 7B | DeepSeek ¥0.5/百万 token |
| 切片逻辑简单 | 大文档可能切得不好 | 积累真实文档后优化 | 按 token + overlap 切 |
| 无多轮记忆 | 每次提问独立 | 加 session ID + 上下文窗口 | Redis 存会话 |
| 飞书消息异步 | webhook 不阻塞回复 | 已用非阻塞，但 LLM 太慢可能超时 | 飞书卡片消息 + 异步更新 |

---

## 七、执行顺序总结（用户操作路径）

```
┌────────────────────────────────────────────────────────────────────┐
│                    你（FDE 学员）的操作路径                          │
│                                                                     │
│  ① 读这份计划，理解每一步在做什么                                    │
│  ② 批准计划 → 我开始改代码                                          │
│  ③ Step 0-1：换 Embedding，验证中文命中率                            │
│  ④ Step 2：接 LLM（Ollama 或云端），验证 FABE 话术                  │
│  ⑤ Step 3：加鉴权，验证安全                                         │
│  ⑥ Step 4：做知识库门户，你自己拖文件测                              │
│  ⑦ Step 5：填飞书 App ID/Secret，配事件订阅                        │
│  ⑧ 端到端：飞书群 @机器人 → 收到 FABE 话术回复 ✨                   │
│                                                                     │
│  每一步我都会告诉你：改哪个文件的哪几行、为什么这么改、怎么验证        │
└────────────────────────────────────────────────────────────────────┘
```
