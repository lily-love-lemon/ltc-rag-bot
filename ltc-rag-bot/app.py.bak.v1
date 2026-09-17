"""LTC 销售话术与招投标 RAG 问答机器人 · MVP（沙箱版）
Embedding: ChromaDB DefaultEmbeddingFunction (all-MiniLM-L6-v2 ONNX)
后端: FastAPI + ChromaDB PersistentClient
"""
import os, uuid, json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from pypdf import PdfReader
import requests

# ── 初始化 Embedding（沙箱无 HuggingFace，用内置 ONNX 模型）───
print("[init] 加载 DefaultEmbeddingFunction (all-MiniLM-L6-v2 ONNX)...")
SENTENCE = embedding_functions.DefaultEmbeddingFunction()

# ── 初始化 ChromaDB（SQLite 本地持久化）───
CHROMA_PATH = str(Path(__file__).parent / "chroma_data")
CHROMA = chromadb.PersistentClient(path=CHROMA_PATH)
COL = CHROMA.get_or_create_collection(
    name="ltc_knowledge",
    embedding_function=SENTENCE,
    metadata={"hnsw:space": "cosine"}
)

print(f"[init] ChromaDB ready · path={CHROMA_PATH} · docs={COL.count()}")

app = FastAPI(title="LTC RAG Bot", version="1.0-sandbox")

# ── 飞书配置 ────────────────────────────────
FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

def feishu_token():
    """获取飞书 tenant_access_token"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return None
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10,
    )
    return r.json().get("tenant_access_token")

@app.get("/health")
def health():
    return {"status": "ok", "docs_count": COL.count(), "embedding": "DefaultEmbeddingFunction"}

@app.post("/ingest")
async def ingest(file: UploadFile):
    """上传 .md / .txt / .pdf 到向量库"""
    content = ""
    if file.filename.endswith(".pdf"):
        reader = PdfReader(file.file)
        content = "\n".join(p.extract_text() or "" for p in reader.pages)
    else:
        content = (await file.read()).decode("utf-8", errors="ignore")

    # 简单按段落切片（中文 \n\n 或 。）
    chunks = [c.strip() for c in content.split("\n\n") if len(c.strip()) > 20]
    # 如果段落切片太少，再按句号/分号切
    if len(chunks) < 3 and len(content) > 100:
        import re
        chunks = [c.strip() for c in re.split(r'[。；\n]', content) if len(c.strip()) > 15]
    
    if not chunks:
        raise HTTPException(400, "文档内容太少，无法切片")
    
    ids = [str(uuid.uuid4()) for _ in chunks]
    COL.add(documents=chunks, ids=ids)
    return {"ingested": len(chunks), "source": file.filename, "total": COL.count()}

@app.post("/ingest-text")
async def ingest_text(body: dict):
    """直接 ingest 一段文本（方便测试）"""
    text = body.get("text", "").strip()
    source = body.get("source", "inline")
    if not text:
        raise HTTPException(400, "text required")
    
    import re
    chunks = [c.strip() for c in re.split(r'[。；\n\n]', text) if len(c.strip()) > 15]
    if not chunks:
        chunks = [text]
    
    ids = [str(uuid.uuid4()) for _ in chunks]
    COL.add(documents=chunks, ids=ids)
    return {"ingested": len(chunks), "source": source, "total": COL.count()}

@app.post("/query")
async def query(body: dict):
    """RAG 查询 — 返回 top-3 片段 + FABE 格式化回答"""
    q = body.get("question", "").strip()
    if not q:
        raise HTTPException(400, "question required")

    results = COL.query(query_texts=[q], n_results=5)
    chunks = results["documents"][0] if results["documents"] else []
    distances = results["distances"][0] if results["distances"] else []

    if not chunks:
        return JSONResponse({
            "answer": "⚠️ 知识库为空，请先通过 /ingest 上传产品文档或 /ingest-text 录入话术。",
            "sources": [],
            "debug": "empty_knowledge_base"
        })

    # 组装 top-3 参考
    top3 = list(zip(chunks[:3], distances[:3]))
    context_block = "\n".join(
        f"[{i+1}] (相似度 {1-d:.3f}) {c}"
        for i, (c, d) in enumerate(top3)
    )

    # MVP 回答（后续接入 LLM 做 FABE 生成）
    answer = (
        f"**问题**：{q}\n\n"
        f"**参考片段**：\n{context_block}\n\n"
        f"**建议话术**：基于以上检索，按 FABE 法则组织回答 — \n"
        f"Feature（特征）→ Advantage（优势）→ Benefit（利益）→ Evidence（证据）。"
    )

    return JSONResponse({
        "answer": answer,
        "sources": [{"text": c, "score": round(1 - d, 4)} for c, d in top3],
        "total_docs": COL.count()
    })

@app.post("/webhook")
async def webhook(payload: dict):
    """飞书事件回调 — @机器人 自动问答"""
    # 飞书 URL 校验必须原样返回 challenge
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

    # 去掉 @机器人 的前缀（飞书消息里 @ 会带 <at ...>...</at>）
    import re
    text = re.sub(r'<at[^>]*>.*?</at>', '', text).strip()
    if not text:
        return {"ok": True}

    # RAG 查询
    result = await query({"question": text})
    answer_text = result.body.decode() if hasattr(result, 'body') else json.dumps(result)
    try:
        answer_json = json.loads(answer_text)
        reply = answer_json.get("answer", str(answer_text))
    except Exception:
        reply = str(answer_text)

    # 回发飞书（需要 App ID/Secret）
    token = feishu_token()
    if token:
        try:
            requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "receive_id": msg.get("chat_id"),
                    "msg_type": "text",
                    "content": json.dumps({"text": reply}),
                },
                timeout=10,
            )
        except Exception as e:
            print(f"[webhook] 发送飞书消息失败: {e}")
    else:
        print(f"[webhook] 无飞书凭证，跳过发送。reply={reply[:100]}...")

    return {"ok": True, "reply_preview": reply[:100]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
