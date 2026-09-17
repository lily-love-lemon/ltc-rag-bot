# ═══════════════════════════════════════════════════════════════════
# LTC RAG Bot v2.0 · CloudBase 云托管版
# 国内部署首选，COS 挂载持久化 ChromaDB 数据
# Embedding: TF-IDF + Jieba（零模型下载，镜像小、构建快）
# LLM: 外部云端（智谱 / DeepSeek / Ollama）
# ═══════════════════════════════════════════════════════════════════

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PORT=8080 \
    CHROMA_PATH=/mnt/chroma

WORKDIR /app

# ── 系统依赖 ──
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python 依赖（分层缓存） ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 应用代码 ──
COPY app.py .
COPY portal/ portal/
COPY configs/ configs/
COPY prompts/ prompts/

# ── 数据目录（COS 挂载点 + 本地 fallback） ──
RUN mkdir -p /mnt/chroma /data/chroma && chmod 777 /mnt/chroma /data/chroma

# ── 健康检查 ──
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:${PORT}/health || exit 1

EXPOSE 8080

CMD ["python", "app.py"]
