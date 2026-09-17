# ═══════════════════════════════════════════════════════════════════
# LTC RAG Bot v2.0 · 多阶段构建
# Embedding: TF-IDF+Jieba（零模型下载，镜像小、构建快）
# LLM: 外部云端（智谱/DeepSeek/Ollama）
# ═══════════════════════════════════════════════════════════════════

FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

WORKDIR /app

# ── 系统依赖 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python 依赖（分层缓存） ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 应用代码 ──
COPY app.py .
COPY portal/ portal/
COPY configs/ configs/
COPY prompts/ prompts/

# ── ChromaDB 数据目录（可持久化挂载） ──
RUN mkdir -p /data/chroma && chmod 777 /data/chroma
ENV CHROMA_PATH=/data/chroma

# ── 健康检查 ──
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:${PORT:-8001}/health || exit 1

# ── 启动 ──
EXPOSE 8001
CMD python app.py
