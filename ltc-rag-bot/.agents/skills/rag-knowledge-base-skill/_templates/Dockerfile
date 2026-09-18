FROM python:3.11-slim

WORKDIR /app

# 最小依赖（CloudBase 免费版 128MB 内存优化）
COPY .deps/requirements.min.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY app.py .
COPY portal/ portal/

# COS 挂载路径（CloudBase 自动挂载）
ENV PERSIST_DIR=/mnt/chroma
ENV API_KEY=change-me-in-production
ENV LLM_PROVIDER=zhipu
ENV LLM_MODEL=glm-4-flash

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
