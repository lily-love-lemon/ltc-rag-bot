#!/bin/bash
# ================================================================
# LTC 销售话术 RAG 机器人 · M1 Mac 一键安装脚本
# 时间预算：2 小时内（依赖下载 + 模型首次加载）
# 内存峰值：~500 MB（bge-small-zh 推理时）
# 磁盘占用：< 1 GB（含 Python venv + 模型）
# ================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}→${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "══════════════════════════════════════════════════"
echo "  LTC RAG Bot · M1 Mac MVP Installer"
echo "  预计耗时：2 小时 | 磁盘 < 1GB | 内存 < 1GB"
echo "══════════════════════════════════════════════════"
echo ""

# ── 0. 基础检查 ──────────────────────────────────────────────
info "Step 0/7 · 环境检查..."

[[ $(uname -m) == "arm64" ]] || { fail "本脚本针对 M1/M2 Mac (arm64)，当前架构: $(uname -m)"; }
python3 --version 2>/dev/null || fail "未找到 python3，请先 brew install python@3.12"
python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" || fail "Python < 3.10，需要 3.10+"
df -h / | tail -1 | awk '{print $4}' | grep -qE '[0-9]+[G]' || fail "系统盘空间不足 1GB，清理一下"
free_mem=$(sysctl -n hw.memsize | awk '{print int($1/1024/1024/1024)}')
[[ $free_mem -ge 8 ]] || warn "内存只有 ${free_mem}GB，bge-small-zh 可能偏紧（建议 8GB+）"

ok "系统检查通过：$(uname -m) · Python $(python3 --version | cut -d' ' -f2) · 内存 ${free_mem}GB"

# ── 1. 创建项目目录 ──────────────────────────────────────────
info "Step 1/7 · 创建项目目录..."
PROJECT_DIR=~/ltc-rag-bot
mkdir -p "$PROJECT_DIR"/{chroma_data,knowledge,logs}
cd "$PROJECT_DIR"
ok "目录：$PROJECT_DIR"

# ── 2. Python 虚拟环境 ───────────────────────────────────────
info "Step 2/7 · Python venv..."
[[ -d venv ]] || python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip -q
ok "venv ready · $(python --version)"

# ── 3. pip 依赖（先装小的，出错能立刻发现）──────────────────
info "Step 3/7 · 安装 pip 依赖（首次可能 5–10 分钟）..."
cat > requirements.txt << 'REQ'
chromadb>=1.5.0
sentence-transformers>=3.3.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.12
requests>=2.32.0
pypdf>=5.0.0
REQ

pip install -r requirements.txt -q 2>&1 | tail -3
ok "pip 依赖安装完成"

# ── 4. 首次跑模型触发下载（bge-small-zh ~100MB）──────────────
info "Step 4/7 · 首次下载 Embedding 模型 bge-small-zh-v1.5（~100MB）..."
python -c "
from sentence_transformers import SentenceTransformer
print('正在加载 bge-small-zh-v1.5（首次会自动下载）...')
m = SentenceTransformer('BAAI/bge-small-zh-v1.5')
v = m.encode('测试')
print(f'✓ 加载成功 · 输出维度 {v.shape[1]}')
" 2>&1 | tail -5
ok "bge-small-zh-v1.5 就绪"

# ── 5. 写入 FastAPI 后端代码（app.py）─────────────────────────
info "Step 5/7 · 生成 app.py..."
cat > app.py << 'APPY'
"""LTC 销售话术与招投标 RAG 问答机器人 · MVP"""
import os, uuid, json
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from pypdf import PdfReader
import requests

# ── 初始化 Embedding（MPS 自动加速 M1）───
SENTENCE = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="BAAI/bge-small-zh-v1.5", device="mps"
)

# ── 初始化 ChromaDB（SQLite 本地）───
CHROMA = chromadb.PersistentClient(path=str(Path(__file__).parent / "chroma_data"))
COL = CHROMA.get_or_create_collection(
    name="ltc_knowledge",
    embedding_function=SENTENCE,
    metadata={"hnsw:space": "cosine"}
)

app = FastAPI(title="LTC RAG Bot", version="1.0")

# ── 飞书配置 ────────────────────────────────
FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

def feishu_token():
    """获取飞书 tenant_access_token"""
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=5,
    )
    return r.json().get("tenant_access_token")

@app.post("/ingest")
async def ingest(file: UploadFile):
    """上传 .md / .txt / .pdf 到向量库"""
    content = ""
    if file.filename.endswith(".pdf"):
        reader = PdfReader(file.file)
        content = "\n".join(p.extract_text() or "" for p in reader.pages)
    else:
        content = (await file.read()).decode("utf-8", errors="ignore")

    # 简单按段落切片
    chunks = [c.strip() for c in content.split("\n\n") if len(c.strip()) > 20]
    ids = [str(uuid.uuid4()) for _ in chunks]
    COL.add(documents=chunks, ids=ids)
    return {"ingested": len(chunks), "source": file.filename}

@app.post("/query")
async def query(body: dict):
    """RAG 查询 + FABE 格式化"""
    q = body.get("question", "").strip()
    if not q: raise HTTPException(400, "question required")

    results = COL.query(query_texts=[q], n_results=5)
    chunks = results["documents"][0] if results["documents"] else []

    # MVP：直接返回 top-3 片段（真实环境这里调 LLM 做 FABE 格式化）
    context = "\n".join(f"[{i+1}] {c}" for i, c in enumerate(chunks[:3]))
    response = (
        f"**问题**：{q}\n\n"
        f"**参考内容**：\n{context}\n\n"
        f"**回答**：参考以上片段，结合产品 FABE 法则组织话术。"
    )
    return JSONResponse({"answer": response, "sources": chunks})

@app.post("/webhook")
async def webhook(payload: dict):
    """飞书事件回调"""
    # 飞书 URL 校验
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    # 接收消息
    evt = payload.get("event", {})
    msg = evt.get("message", {})
    if msg.get("message_type") == "text":
        text = json.loads(msg.get("content", "{}")).get("text", "").strip()
        if text and not text.startswith("/"):
            r = await query({"question": text})
            token = feishu_token()
            if token:
                requests.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={
                        "receive_id": msg.get("chat_id"),
                        "msg_type": "text",
                        "content": json.dumps({"text": r["answer"]}),
                    },
                    timeout=5,
                )
    return {"ok": True}

@app.get("/health")
def health():
    return {"status": "ok", "docs_count": COL.count()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
APPY
ok "app.py 已生成"

# ── 6. 写入 .env 模板 ───────────────────────────────────────
info "Step 6/7 · 生成 .env 配置模板..."
cat > .env.example << 'ENV'
# 飞书开放平台 → 你的应用 → 凭证与基础信息
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ENV

if [ ! -f .env ]; then
    cp .env.example .env
    warn "⚠️  请编辑 $PROJECT_DIR/.env，填入飞书 App ID 和 App Secret"
fi

ok "配置模板已就绪"

# ── 7. 启动服务 + 自测 ───────────────────────────────────────
info "Step 7/7 · 启动 FastAPI 并自测..."
uvicorn app:app --host 0.0.0.0 --port 8001 > logs/app.log 2>&1 &
APP_PID=$!
sleep 3

# 健康检查
HEALTH=$(curl -s http://localhost:8001/health 2>&1 || "")
if echo "$HEALTH" | grep -q '"status":"ok"'; then
    ok "服务启动成功 · PID=$APP_PID · http://localhost:8001"
    echo "   向量库已有文档: $(echo $HEALTH | python3 -c 'import sys,json;print(json.load(sys.stdin).get("docs_count","?"))')"
else
    fail "服务启动失败，查看日志：$PROJECT_DIR/logs/app.log"
fi

# 自测：向量化一段测试文本
curl -s -X POST http://localhost:8001/ingest \
  -F "file=@<(echo '我们的电池采用三重保温技术，在零下40摄氏度环境下可正常工作24小时，是2024年冬奥会官方供应商。')" \
  -o /dev/null 2>&1

QUERY=$(curl -s -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{"question":"零下30度怎么保证续航"}')

if echo "$QUERY" | grep -q "电池"; then
    ok "RAG 自测通过 ✨ 已检索到三重保温话术"
else
    warn "RAG 自测未命中（可能测试文本太短），上传真实产品文档再试"
fi

echo ""
echo "══════════════════════════════════════════════════"
echo "  ✨ MVP 部署完成！"
echo ""
echo "  服务地址：  http://localhost:8001"
echo "  项目目录：  $PROJECT_DIR"
echo "  查看日志：  tail -f $PROJECT_DIR/logs/app.log"
echo "  停止服务：  kill $APP_PID"
echo ""
echo "  下一步（手动）："
echo "  1. 在飞书创建企业机器人，填好 .env 里的 App ID/Secret"
echo "  2. 飞书开放平台 → 事件订阅 → 填入公网 Webhook URL"
echo "  3. 本地测试公网：cloudflared tunnel --url http://localhost:8001"
echo "  4. 飞书群里 @机器人 提问！"
echo "══════════════════════════════════════════════════"
