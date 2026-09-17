"""
LTC 销售话术与招投标 RAG 问答机器人 · v2.0
==========================================
升级内容（相比 v1.0）：
  ① Embedding: bge-small-zh-v1.5（专为中文训练，512维）
  ② LLM 接入: DeepSeek / Ollama / 模板回退（多级 fallback）
  ③ FABE 话术生成: 真实 LLM 组织，不是裸拼接
  ④ API Key 鉴权: 开发模式自动跳过，生产模式强制
  ⑤ 知识库管理门户: GET / 返回 portal/index.html
  ⑥ 新增端点: GET /docs, DELETE /doc/{id}

架构: FastAPI → ChromaDB (SQLite) → bge-small-zh-v1.5 → LLM → 飞书
"""
import os, uuid, json, re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import chromadb
from chromadb.utils import embedding_functions
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI, UploadFile, HTTPException, Security, Depends
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse, FileResponse
from pypdf import PdfReader
import requests

# ═══════════════════════════════════════════════════════════════════
# ① Embedding 初始化 —— bge-small-zh-v1.5（中文专用）
#    优先用本地 ModelScope 缓存（沙箱环境），fallback 到 HF model name
# ═══════════════════════════════════════════════════════════════════
def _init_embedding():
    MODEL_PATH_HF = "BAAI/bge-small-zh-v1.5"
    MODEL_PATH_LOCAL = "/root/.cache/modelscope/models/BAAI--bge-small-zh-v1.5/snapshots/master"
    
    # 本地缓存存在就用本地，不存在就用 HF model name（首次自动下载）
    model_path = MODEL_PATH_LOCAL if os.path.isdir(MODEL_PATH_LOCAL) else MODEL_PATH_HF
    
    print(f"[init] 加载 BAAI/bge-small-zh-v1.5 ...")
    print(f"       模型路径: {model_path}")
    
    # 先直接加载 SentenceTransformer 验证
    st_model = SentenceTransformer(model_path, device="cpu")
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=model_path,
        device="cpu",
    )

try:
    SENTENCE = _init_embedding()
    EMBEDDING_NAME = "bge-small-zh-v1.5 (中文专用, 512维)"
except Exception as e:
    print(f"[init] ⚠️ bge-small-zh 加载失败: {e}")
    print(f"[init] 回退到 DefaultEmbeddingFunction (all-MiniLM-L6-v2)")
    SENTENCE = embedding_functions.DefaultEmbeddingFunction()
    EMBEDDING_NAME = "DefaultEmbeddingFunction (all-MiniLM-L6-v2, 英文为主)"

# ═══════════════════════════════════════════════════════════════════
# ② ChromaDB 初始化
# ═══════════════════════════════════════════════════════════════════
CHROMA_PATH = str(Path(__file__).parent / "chroma_data")
CHROMA = chromadb.PersistentClient(path=CHROMA_PATH)
COL = CHROMA.get_or_create_collection(
    name="ltc_knowledge",
    embedding_function=SENTENCE,
    metadata={"hnsw:space": "cosine"}
)
print(f"[init] ChromaDB ready · path={CHROMA_PATH} · docs={COL.count()}")

# ═══════════════════════════════════════════════════════════════════
# ③ FastAPI + API Key 鉴权
# ═══════════════════════════════════════════════════════════════════
app = FastAPI(title="LTC RAG Bot", version="2.0")

API_KEY = os.environ.get("API_KEY", "dev-only-key-change-in-prod")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)):
    """API Key 鉴权 —— 开发模式(默认值)自动跳过"""
    if API_KEY == "dev-only-key-change-in-prod":
        return True
    if not api_key or api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing API Key (X-API-Key header)")
    return True

# ═══════════════════════════════════════════════════════════════════
# ④ 飞书配置
# ═══════════════════════════════════════════════════════════════════
FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

def feishu_token():
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return None
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10,
    )
    data = r.json()
    if data.get("code") == 0:
        return data.get("tenant_access_token")
    print(f"[feishu] 获取 token 失败: {data}")
    return None

# ═══════════════════════════════════════════════════════════════════
# ⑤ LLM 调用 —— 多级 fallback
#    优先级: DeepSeek 云端 > Ollama 本地 > 模板回退
# ═══════════════════════════════════════════════════════════════════
def call_llm(prompt: str) -> tuple:
    """统一的 LLM 调用 —— 多级 fallback 保证永不崩
    返回: (content, backend_name)  backend ∈ {"deepseek","ollama","template-fallback"}
    """
    
    # ---- 优先级 1: DeepSeek 云端 ----
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        try:
            r = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {deepseek_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
                timeout=30,
            )
            content = r.json()["choices"][0]["message"]["content"]
            print(f"[llm] DeepSeek 调用成功 · {len(content)} chars")
            return content, "deepseek"
        except Exception as e:
            print(f"[llm] DeepSeek 失败: {e} · 继续尝试下一个...")
    
    # ---- 优先级 2: Ollama 本地 ----
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    try:
        r = requests.post(
            f"{ollama_url}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        content = r.json().get("response", "")
        if content:
            print(f"[llm] Ollama 调用成功 · model={ollama_model} · {len(content)} chars")
            return content, "ollama"
    except Exception as e:
        print(f"[llm] Ollama 不可用: {e} · fallback 到模板")
    
    # ---- Fallback: 模板化回答 ----
    return _template_fallback(prompt), "template-fallback"

def _template_fallback(prompt: str) -> str:
    """没有 LLM 时的兜底 —— 把检索结果直接格式化"""
    # prompt 的后半段是参考内容（格式由调用方决定）
    return (
        "【⚠️ 当前无可用 LLM · 以下为 RAG 检索原始片段】\n\n"
        "建议接入以下任一 LLM 获得更好效果：\n"
        "  • DeepSeek: 在 .env 填 DEEPSEEK_API_KEY\n"
        "  • Ollama:   ollama pull qwen2.5:7b && .env 留空即可自动检测\n\n"
        "——— 原始检索结果 ———\n"
        f"{prompt}\n"
    )

# ═══════════════════════════════════════════════════════════════════
# ⑥ 端点实现
# ═══════════════════════════════════════════════════════════════════

@app.get("/")
async def portal():
    """知识库管理门户 —— 返回 portal/index.html"""
    portal_path = Path(__file__).parent / "portal" / "index.html"
    if portal_path.exists():
        return FileResponse(portal_path)
    return {
        "service": "LTC RAG Bot v2.0",
        "health": "ok",
        "hint": "把 portal/index.html 放到 portal/ 目录下即可启用管理门户",
    }

@app.get("/health")
def health():
    """健康检查（公开免鉴权）"""
    return {
        "status": "ok",
        "version": "2.0",
        "docs_count": COL.count(),
        "embedding": EMBEDDING_NAME,
        "api_key_mode": "开发模式(无强制鉴权)" if API_KEY == "dev-only-key-change-in-prod" else "生产模式(已开启鉴权)",
        "feishu_configured": bool(FEISHU_APP_ID and FEISHU_APP_SECRET),
    }

# 注意：FastAPI 默认把 /docs 留给 Swagger UI，知识库用 /kb 前缀
@app.get("/kb")
async def list_knowledge(_: bool = Depends(verify_api_key)):
    """列出 ChromaDB 里所有已入库文档（按 source 分组）"""
    result = COL.get(include=["metadatas"])
    sources = {}
    ids_by_source = {}
    for i, md in enumerate(result["metadatas"] or []):
        src = (md or {}).get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        ids_by_source.setdefault(src, []).append(result["ids"][i])
    
    return {
        "total_chunks": COL.count(),
        "sources": sources,
        "ids_by_source": ids_by_source,
    }

@app.delete("/kb/{chunk_id}")
async def delete_chunk(chunk_id: str, _: bool = Depends(verify_api_key)):
    """删除单个切片"""
    try:
        COL.delete(ids=[chunk_id])
        return {"ok": True, "deleted": chunk_id, "remaining": COL.count()}
    except Exception as e:
        raise HTTPException(400, f"删除失败: {e}")

@app.delete("/kb")
async def clear_all_knowledge(_: bool = Depends(verify_api_key)):
    """清空整个知识库（危险操作）"""
    COL.delete(ids=COL.get()["ids"])
    return {"ok": True, "cleared_all": True}

@app.post("/ingest")
async def ingest(file: UploadFile, _: bool = Depends(verify_api_key)):
    """上传 .pdf / .md / .txt 文件入库"""
    content = ""
    if file.filename.lower().endswith(".pdf"):
        reader = PdfReader(file.file)
        content = "\n".join(p.extract_text() or "" for p in reader.pages)
    else:
        content = (await file.read()).decode("utf-8", errors="ignore")
    
    if not content.strip():
        raise HTTPException(400, "文件内容为空")
    
    chunks = _chunk_text(content)
    ids = [str(uuid.uuid4()) for _ in chunks]
    source = file.filename
    metadatas = [{"source": source} for _ in chunks]
    
    COL.add(documents=chunks, ids=ids, metadatas=metadatas)
    return {"ingested": len(chunks), "source": source, "total": COL.count()}

@app.post("/ingest-text")
async def ingest_text(body: dict, _: bool = Depends(verify_api_key)):
    """直接录入一段文本（方便测试）"""
    text = body.get("text", "").strip()
    source = body.get("source", "inline")
    if not text:
        raise HTTPException(400, "text required")
    
    chunks = _chunk_text(text)
    ids = [str(uuid.uuid4()) for _ in chunks]
    metadatas = [{"source": source} for _ in chunks]
    
    COL.add(documents=chunks, ids=ids, metadatas=metadatas)
    return {"ingested": len(chunks), "source": source, "total": COL.count()}

def _chunk_text(text: str) -> list:
    """中文智能切片：先按段落（\n\n），太小了再按句号/分号"""
    chunks = [c.strip() for c in text.split("\n\n") if len(c.strip()) > 20]
    if len(chunks) < 3 and len(text) > 100:
        chunks = [c.strip() for c in re.split(r'[。；\n]', text) if len(c.strip()) > 15]
    if not chunks:
        chunks = [text]
    return chunks

@app.post("/query")
async def query(body: dict, _: bool = Depends(verify_api_key)):
    """RAG + LLM FABE 问答"""
    q = body.get("question", "").strip()
    if not q:
        raise HTTPException(400, "question required")
    
    results = COL.query(query_texts=[q], n_results=5)
    chunks = results["documents"][0] if results["documents"] else []
    distances = results["distances"][0] if results["distances"] else []
    metadatas_list = results["metadatas"][0] if results.get("metadatas") else []
    
    if not chunks:
        return JSONResponse({
            "answer": "⚠️ 知识库为空，请先上传产品文档。",
            "sources": [],
            "total_docs": 0,
        })
    
    # 组装 top-3 参考
    top3 = list(zip(chunks[:3], distances[:3], metadatas_list[:3] if metadatas_list else [{}]*3))
    context_block = "\n".join(
        f"[{i+1}] (相似度 {1-d:.3f}, 来源 {(m or {}).get('source','?')}) {c}"
        for i, (c, d, m) in enumerate(top3)
    )
    
    # 构造 FABE prompt 并调 LLM
    fabe_prompt = f"""你是一个专业的 B2B 销售话术专家。请基于以下参考内容，按 FABE 法则组织一段完整的销售回答。

规则：
1. 如果参考片段里没有足够信息回答，诚实说"知识库暂未收录相关内容"，不要编造
2. Feature 客观描述产品/服务的特征
3. Advantage 说明这个特征带来的优势（和竞品/旧方案比）
4. Benefit 一定要从**客户视角**描述利益（省多少钱/多少时间/降低什么风险）
5. Evidence 引用认证/案例/数据作为佐证
6. 回答用中文，口语化，符合销售对客户说话的风格，200-400字

客户问题：{q}

参考内容：
{context_block}

请输出完整的 FABE 话术："""
    
    llm_reply, llm_backend = call_llm(fabe_prompt)
    
    # 如果是模板回退，直接返回检索片段就好
    if llm_backend == "template-fallback":
        answer = f"**问题**：{q}\n\n**📎 参考片段**：\n{context_block}\n\n{llm_reply}"
    else:
        answer = f"**问题**：{q}\n\n**🤖 AI 话术（FABE · {llm_backend}）**：\n{llm_reply}\n\n**📎 参考片段**：\n{context_block}"
    
    return JSONResponse({
        "answer": answer,
        "sources": [
            {
                "text": c,
                "score": round(1 - d, 4),
                "source": (m or {}).get("source", "unknown"),
            }
            for c, d, m in top3
        ],
        "llm_used": llm_backend,
        "total_docs": COL.count(),
    })

@app.post("/webhook")
async def webhook(payload: dict):
    """飞书事件回调（飞书自鉴，不加 API Key）"""
    # 飞书 URL 校验 —— 必须原样返回 challenge
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    
    evt = payload.get("event", {})
    msg = evt.get("message", {})
    
    if msg.get("message_type") != "text":
        return {"ok": True}
    
    try:
        text = json.loads(msg.get("content", "{}")).get("text", "").strip()
    except Exception:
        text = ""
    
    if not text or text.startswith("/"):
        return {"ok": True}
    
    # 去掉 @机器人 的 <at> 标签
    text = re.sub(r'<at[^>]*>.*?</at>', '', text).strip()
    if not text:
        return {"ok": True}
    
    print(f"[webhook] 收到消息: '{text}'")
    
    # RAG 查询（复用 query 函数）
    result = await query({"question": text}, _=True)
    answer_text = result.body.decode() if hasattr(result, 'body') else json.dumps(result)
    try:
        answer_json = json.loads(answer_text)
        reply = answer_json.get("answer", str(answer_text))
    except Exception:
        reply = str(answer_text)
    
    # 回发飞书
    token = feishu_token()
    if token:
        try:
            r = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "receive_id": msg.get("chat_id"),
                    "msg_type": "text",
                    "content": json.dumps({"text": reply[:4000]}),  # 飞书单条消息上限
                },
                timeout=10,
            )
            print(f"[webhook] 飞书回复: {r.status_code} {r.json()}")
        except Exception as e:
            print(f"[webhook] 发送飞书消息失败: {e}")
    else:
        print(f"[webhook] ⚠️ 无飞书凭证，跳过发送。reply={reply[:100]}...")
    
    return {"ok": True, "reply_preview": reply[:150]}

# ═══════════════════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
