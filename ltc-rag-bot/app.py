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
_default = str(Path(__file__).parent / "chroma_data")
# 优先级: 环境变量 > COS 挂载点 > 本地目录
CHROMA_PATH = os.environ.get("CHROMA_PATH") or ("/mnt/chroma" if os.path.isdir("/mnt/chroma") else _default)
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
async def ingest(
    file: UploadFile,
    _: bool = Depends(verify_api_key),
    strategy: str = "default",
):
    """上传 .pdf / .md / .txt 文件入库
    strategy: 切片策略名（见 GET /strategies），默认 default
    """
    content = ""
    if file.filename.lower().endswith(".pdf"):
        reader = PdfReader(file.file)
        content = "\n".join(p.extract_text() or "" for p in reader.pages)
    else:
        content = (await file.read()).decode("utf-8", errors="ignore")
    
    if not content.strip():
        raise HTTPException(400, "文件内容为空")
    
    chunks = _chunk_text(content, strategy=strategy)
    ids = [str(uuid.uuid4()) for _ in chunks]
    source = file.filename
    metadatas = [{"source": source, "strategy": strategy} for _ in chunks]
    
    COL.add(documents=chunks, ids=ids, metadatas=metadatas)
    return {"ingested": len(chunks), "source": source, "strategy": strategy, "total": COL.count()}

@app.post("/ingest-text")
async def ingest_text(
    body: dict,
    _: bool = Depends(verify_api_key),
):
    """直接录入一段文本（方便测试）
    body: {text, source, strategy}  strategy 默认 default
    """
    text = body.get("text", "").strip()
    source = body.get("source", "inline")
    strategy = body.get("strategy", "default")
    if not text:
        raise HTTPException(400, "text required")
    
    chunks = _chunk_text(text, strategy=strategy)
    ids = [str(uuid.uuid4()) for _ in chunks]
    metadatas = [{"source": source, "strategy": strategy} for _ in chunks]
    
    COL.add(documents=chunks, ids=ids, metadatas=metadatas)
    return {"ingested": len(chunks), "source": source, "strategy": strategy, "total": COL.count()}

# ═══════════════════════════════════════════════════════════════════
# Prompt 模板管理 —— 从 prompts/*.txt 读，可页面编辑
# ═══════════════════════════════════════════════════════════════════
PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPTS_DIR.mkdir(exist_ok=True)

# 内置兜底 prompt（文件不存在时用）
_BUILTIN_PROMPTS = {
    "fabe": """你是一个专业的 B2B 销售话术专家。请基于以下参考内容，按 FABE 法则组织一段完整的销售回答。

规则：
1. 如果参考片段里没有足够信息回答，诚实说"知识库暂未收录相关内容"，不要编造
2. Feature 客观描述产品/服务的特征
3. Advantage 说明这个特征带来的优势（和竞品/旧方案比）
4. Benefit 一定要从**客户视角**描述利益（省多少钱/多少时间/降低什么风险）
5. Evidence 引用认证/案例/数据作为佐证
6. 回答用中文，口语化，符合销售对客户说话的风格，200-400字

客户问题：{question}

参考内容：
{context}

请输出完整的 FABE 话术：""",
}

def _get_prompt(name: str, **kwargs) -> str:
    """读 prompt 模板文件 → .format(kwargs) 填充占位符"""
    p = PROMPTS_DIR / f"{name}.txt"
    template = p.read_text(encoding="utf-8") if p.exists() else _BUILTIN_PROMPTS.get(name, _BUILTIN_PROMPTS["fabe"])
    return template.format(**kwargs) if kwargs else template

@app.get("/prompts/{name}")
def get_prompt(name: str, _: bool = Depends(verify_api_key)):
    """读取 prompt 模板 —— portal 页面编辑前先 fetch"""
    p = PROMPTS_DIR / f"{name}.txt"
    content = p.read_text(encoding="utf-8") if p.exists() else _BUILTIN_PROMPTS.get(name, "")
    return {"name": name, "content": content, "builtin": not p.exists()}

@app.put("/prompts/{name}")
def put_prompt(name: str, body: dict, _: bool = Depends(verify_api_key)):
    """保存 prompt 模板 —— 写 .txt 文件，立即生效不用重启"""
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(400, "content required")
    # 安全检查：只允许字母数字下划线
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise HTTPException(400, "invalid prompt name")
    p = PROMPTS_DIR / f"{name}.txt"
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "name": name, "path": str(p)}

@app.get("/prompts")
def list_prompts(_: bool = Depends(verify_api_key)):
    """列出所有 prompt 模板（文件 + 内置）"""
    builtin_names = set(_BUILTIN_PROMPTS.keys())
    file_names = {p.stem for p in PROMPTS_DIR.glob("*.txt")}
    all_names = builtin_names | file_names
    return [
        {"name": n, "has_file": n in file_names, "has_builtin": n in builtin_names}
        for n in sorted(all_names)
    ]

# ═══════════════════════════════════════════════════════════════════
# BM25 + RRF 混合检索（Step 3 新增）
# ═══════════════════════════════════════════════════════════════════
try:
    import rank_bm25
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False
    print("[bm25] rank-bm25 未安装，混合检索将跳过 BM25 路径")

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

def _chinese_tokenize(text: str) -> list:
    """中文分词 —— 优先 jieba，fallback 到简单字切分"""
    if JIEBA_AVAILABLE:
        return [t.strip() for t in jieba.cut(text) if t.strip()]
    # 简单 fallback：按标点切 + 每个字也加进去（应对专有名词）
    tokens = [c for c in re.split(r'[，。！？；：、\s]+', text) if c]
    for c in text:
        if c not in '，。！？；：、 \n':
            tokens.append(c)
    return tokens

def _bm25_retrieve(query: str, documents: list, top_k: int = 5) -> tuple:
    """BM25 检索 —— 返回 (chunks, scores)"""
    if not BM25_AVAILABLE or not documents:
        return [], []
    tokenized = [_chinese_tokenize(d) for d in documents]
    bm25 = rank_bm25.BM25Okapi(tokenized)
    q_tokens = _chinese_tokenize(query)
    scores = bm25.get_scores(q_tokens)
    # 取 top-k
    ranked = sorted(enumerate(scores), key=lambda x: -x[1])[:top_k]
    chunks = [documents[i] for i, _ in ranked]
    return chunks, [s for _, s in ranked]

def _rrf_fuse(vec_chunks: list, bm25_chunks: list, k: int = 60) -> list:
    """
    Reciprocal Rank Fusion —— 按名次融合，不看绝对分数
    RRF(d) = sum(1/(k + rank_i))  rank 从 1 开始
    """
    scores = {}
    for rank, doc in enumerate(vec_chunks, 1):
        scores[doc] = scores.get(doc, 0) + 1 / (k + rank)
    for rank, doc in enumerate(bm25_chunks, 1):
        scores[doc] = scores.get(doc, 0) + 1 / (k + rank)
    return sorted(scores.keys(), key=lambda d: -scores[d])

def _hybrid_search(query: str, top_k: int = 5, use_bm25: bool = True):
    """
    混合检索：向量 + BM25 + RRF 融合
    返回 (chunks, distances, sources) 格式和原来 COL.query 保持一致
    """
    # 1. 向量检索（先拉多一点给融合空间）
    vector_k = top_k * 3 if use_bm25 else top_k
    results = COL.query(query_texts=[query], n_results=vector_k, include=["documents", "metadatas", "distances"])
    vec_chunks = results["documents"][0] if results["documents"] else []
    vec_distances = results["distances"][0] if results["distances"] else []
    vec_metadatas = results["metadatas"][0] if results.get("metadatas") else []
    
    if not vec_chunks:
        return [], [], []
    
    if not use_bm25 or not BM25_AVAILABLE:
        # 纯向量模式
        return vec_chunks[:top_k], vec_distances[:top_k], vec_metadatas[:top_k] or [{}]*top_k
    
    # 2. BM25 检索（用完整 chunk 池当 corpus）
    bm25_chunks, _ = _bm25_retrieve(query, vec_chunks, top_k=vector_k)
    
    # 3. RRF 融合
    fused_chunks = _rrf_fuse(vec_chunks, bm25_chunks, k=60)[:top_k]
    
    # 4. 还原 distances 和 metadatas（融合后按 chunk 文本回填）
    dist_map = {c: d for c, d in zip(vec_chunks, vec_distances)}
    meta_map = {c: m for c, m in zip(vec_chunks, vec_metadatas or [{}]*len(vec_chunks))}
    fused_distances = [dist_map.get(c, 1.0) for c in fused_chunks]
    fused_metadatas = [meta_map.get(c, {}) for c in fused_chunks]
    
    return fused_chunks, fused_distances, fused_metadatas

# ═══════════════════════════════════════════════════════════════════
# Step 1 · 切片策略配置 —— 从 configs/kb_strategies.json 读
# ═══════════════════════════════════════════════════════════════════
import json as _json

def _load_strategies() -> dict:
    """加载切片策略配置，失败回退到内置 default"""
    p = Path(__file__).parent / "configs" / "kb_strategies.json"
    if p.exists():
        try:
            raw = _json.loads(p.read_text(encoding="utf-8"))
            # 过滤掉 _comment 等元数据
            return {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception as e:
            print(f"[config] 策略配置加载失败: {e}")
    # 内置兜底
    return {
        "default": {
            "name": "默认",
            "mode": "paragraph",
            "chunk_size": 500,
            "chunk_overlap": 50,
            "min_chunk_len": 20,
            "max_chunk_len": 2000,
        }
    }

CHUNK_STRATEGIES = _load_strategies()
DEFAULT_STRATEGY = "default"

# 给 ingest 端点加上 strategy 参数接收（需要加 import）
from fastapi import Query

@app.get("/strategies")
def list_strategies():
    """列出所有可用切片策略 —— 给 portal 下拉选"""
    out = {}
    for key, s in CHUNK_STRATEGIES.items():
        out[key] = {
            "name": s.get("name", key),
            "mode": s.get("mode", "paragraph"),
            "chunk_size": s.get("chunk_size", 500),
            "chunk_overlap": s.get("chunk_overlap", 50),
            "min_chunk_len": s.get("min_chunk_len", 20),
            "max_chunk_len": s.get("max_chunk_len", 2000),
            "desc": s.get("desc", ""),
        }
    return out

def _chunk_text(text: str, strategy: str = "default") -> list:
    """
    中文智能切片 —— 按配置参数化
    strategy: configs/kb_strategies.json 里的 key
    """
    s = CHUNK_STRATEGIES.get(strategy, CHUNK_STRATEGIES[DEFAULT_STRATEGY])
    mode = s.get("mode", "paragraph")
    min_len = s.get("min_chunk_len", 20)
    max_len = s.get("max_chunk_len", 2000)
    chunk_size = s.get("chunk_size", 500)
    overlap = s.get("chunk_overlap", 50)
    
    # Step 1: 按 mode 切
    if mode == "paragraph":
        # 先按 \n\n 段落，段落太大再按句号切
        chunks = [c.strip() for c in text.split("\n\n") if len(c.strip()) > min_len]
        # 如果段落太大，强制按句号再切
        chunks = _split_by_sentence_if_big(chunks, min_len, max_len)
    elif mode == "sentence":
        # 直接按句号/分号切（销售话术短段落优先）
        chunks = [c.strip() for c in re.split(r'[。；\n]', text) if len(c.strip()) > min_len]
    else:
        chunks = [text]  # fallback: 不切
    
    # Step 2: 如果还是没切出来，fallback 到 sentence
    if not chunks:
        chunks = [c.strip() for c in re.split(r'[。；\n]', text) if len(c.strip()) > min_len]
    if not chunks:
        chunks = [text]
    
    return chunks

def _split_by_sentence_if_big(chunks: list, min_len: int, max_len: int) -> list:
    """段落太大 → 按句号/分号再切一次"""
    result = []
    for c in chunks:
        if len(c) <= max_len:
            result.append(c)
        else:
            sub = [x.strip() for x in re.split(r'[。；\n]', c) if len(x.strip()) > min_len]
            result.extend(sub if sub else [c])
    return result

# ========== Step 4: 切片预览 + 手动编辑 ==========

@app.get("/chunks")
def list_chunks(
    page: int = Query(1, ge=1, description="第几页"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    source: str | None = Query(None, description="按来源过滤"),
    strategy: str | None = Query(None, description="按切片策略过滤"),
    keyword: str | None = Query(None, description="按关键词全文搜索"),
):
    """分页列出所有切片 —— 支持按 source/strategy/keyword 过滤"""
    total = COL.count()
    
    # 构造 where 过滤器
    where = {}
    if source:
        where["source"] = source
    if strategy:
        where["strategy"] = strategy
    
    offset = (page - 1) * page_size
    
    if keyword:
        # 关键词搜索 → 用 query 而不是 get
        results = COL.query(
            query_texts=[keyword],
            n_results=min(total, page_size),
            where=where if where else None,
            include=["documents", "metadatas", "distances"],
        )
        ids = results["ids"][0] if results["ids"] else []
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        distances = results["distances"][0] if results["distances"] else []
        total = len(ids)
    else:
        results = COL.get(
            limit=page_size,
            offset=offset,
            where=where if where else None,
            include=["documents", "metadatas"],
        )
        ids = results["ids"]
        docs = results["documents"]
        metas = results["metadatas"]
        distances = [None] * len(ids)
    
    chunks = []
    for i, cid in enumerate(ids):
        chunks.append({
            "id": cid,
            "text": docs[i] if docs else "",
            "text_preview": (docs[i][:80] + "…") if docs and len(docs[i]) > 80 else (docs[i] if docs else ""),
            "length": len(docs[i]) if docs else 0,
            "source": (metas[i] or {}).get("source", "unknown") if metas else "unknown",
            "strategy": (metas[i] or {}).get("strategy", "unknown") if metas else "unknown",
            "score": round(1 - distances[i], 4) if distances and distances[i] is not None else None,
        })
    
    return JSONResponse({
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "chunks": chunks,
    })


@app.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: str):
    """单条切片详情"""
    results = COL.get(ids=[chunk_id], include=["documents", "metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "chunk not found")
    
    return JSONResponse({
        "id": chunk_id,
        "text": results["documents"][0],
        "length": len(results["documents"][0]),
        "metadata": results["metadatas"][0] or {},
    })


@app.patch("/chunks/{chunk_id}")
def update_chunk(chunk_id: str, body: dict):
    """
    手动修改切片内容 —— 自动重算 embedding 存入向量库
    可以改 text、也可以改 metadata 里的 source/strategy
    """
    results = COL.get(ids=[chunk_id], include=["documents", "metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "chunk not found")
    
    updates = {}
    
    # 改文本 → 自动重算 embedding（ChromaDB 用 collection 绑定的 embedding_function）
    new_text = body.get("text")
    if new_text is not None:
        if len(new_text.strip()) < 5:
            raise HTTPException(400, "text too short (min 5 chars)")
        updates["documents"] = [new_text]
    
    # 改 metadata
    new_source = body.get("source")
    new_strategy = body.get("strategy")
    if new_source or new_strategy:
        old_meta = results["metadatas"][0] or {}
        new_meta = dict(old_meta)
        if new_source:
            new_meta["source"] = new_source
        if new_strategy:
            new_meta["strategy"] = new_strategy
        updates["metadatas"] = [new_meta]
    
    if not updates:
        raise HTTPException(400, "nothing to update —— 需要 text 或 source 或 strategy 字段")
    
    COL.update(ids=[chunk_id], **updates)
    
    # 返回更新后的完整数据
    results2 = COL.get(ids=[chunk_id], include=["documents", "metadatas"])
    return JSONResponse({
        "updated": True,
        "id": chunk_id,
        "new_text": results2["documents"][0],
        "new_metadata": results2["metadatas"][0] or {},
    })


# ========== 原端点（query, webhook 等） ==========

@app.post("/query")
async def query(body: dict, _: bool = Depends(verify_api_key)):
    """RAG + LLM FABE 问答 —— 混合检索 + 文件化 Prompt"""
    q = body.get("question", "").strip()
    top_k = body.get("top_k", 5)
    use_bm25 = body.get("use_bm25", True)
    if not q:
        raise HTTPException(400, "question required")
    
    # ===== 混合检索（向量 + BM25 + RRF 融合）=====
    chunks, distances, metadatas_list = _hybrid_search(q, top_k=top_k, use_bm25=use_bm25)
    
    if not chunks:
        return JSONResponse({
            "answer": "⚠️ 知识库为空，请先上传产品文档。",
            "sources": [],
            "total_docs": 0,
            "hybrid": {"bm25_available": BM25_AVAILABLE, "use_bm25": False},
        })
    
    # 组装 top-3 参考
    top3 = list(zip(chunks[:3], distances[:3], metadatas_list[:3]))
    context_block = "\n".join(
        f"[{i+1}] (相似度 {1-d:.3f}, 来源 {(m or {}).get('source','?')}) {c}"
        for i, (c, d, m) in enumerate(top3)
    )
    
    # ===== 从文件读 FABE prompt 模板（可页面编辑，不用重启）=====
    fabe_prompt = _get_prompt("fabe", question=q, context=context_block)
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
        "hybrid": {
            "bm25_available": BM25_AVAILABLE,
            "jieba_available": JIEBA_AVAILABLE,
            "use_bm25": use_bm25 and BM25_AVAILABLE,
        },
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
    port = int(os.environ.get("PORT", 8001))
    print(f"🚀 LTC RAG Bot v2.0 启动 · port={port} · embedding={EMBEDDING_NAME}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
