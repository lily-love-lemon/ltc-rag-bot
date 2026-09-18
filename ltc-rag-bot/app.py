"""
LTC 销售话术与招投标 RAG 问答机器人 · v3.0（CloudBase 免费版优化）
================================================================
与 v2.0 的核心差异：
  ✅ 砍掉 chromadb / scikit-learn / rank-bm25 —— 省 ~160MB 内存
  ✅ 自实现 SimpleVectorStore（numpy + JSON，零依赖）
  ✅ 自实现 TF-IDF 嵌入 + BM25 检索（纯 numpy + jieba）
  ✅ 内存从 ~225MB 压到 ~75MB，可跑 CloudBase 免费版 0.5核128MB

架构: FastAPI → SimpleVectorStore (numpy .npy + JSON) → TF-IDF + BM25 + RRF → LLM → 飞书
"""
import os, uuid, json, re, math, tempfile, hashlib, base64
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import numpy as np
try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

from fastapi import FastAPI, UploadFile, HTTPException, Security, Depends, Query, Request
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from pypdf import PdfReader
import requests

# ═══════════════════════════════════════════════════════════════════
# ① 中文分词（优先 jieba，fallback 到简单切分）
# ═══════════════════════════════════════════════════════════════════
def _chinese_tokenize(text: str) -> list:
    """中文分词 —— jieba 优先，简单 fallback"""
    if JIEBA_AVAILABLE:
        return [t.strip() for t in jieba.cut(text) if t.strip()]
    # 简单 fallback：按标点 + 每个字
    tokens = [c for c in re.split(r'[，。！？；：、\s]+', text) if c]
    for c in text:
        if c not in '，。！？；：、 \n\r\t':
            tokens.append(c)
    return tokens

# ═══════════════════════════════════════════════════════════════════
# ② 自实现 SimpleVectorStore —— 替换 ChromaDB
#    持久化: vectors.npy + metadata.json + vocab.json
# ═══════════════════════════════════════════════════════════════════
class SimpleVectorStore:
    """
    轻量向量存储 —— 基于 numpy + JSON，API 对齐 ChromaDB Collection
    文件结构:
      vectors.npy    # (n_docs × n_features, float32)
      metadata.json  # [{id, source, text, strategy}, ...]
      vocab.json     # {token: index, _df: [...], _n_docs: N}
    """

    MAX_VOCAB_SIZE = 20000  # 词表上限，防止膨胀

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

        self.vectors: np.ndarray | None = None      # (N, D) float32
        self.metadata: list[dict] = []              # [{id, source, text, strategy}, ...]
        self.vocab: dict = {}                       # {token: index} + _df + _n_docs

        self._load()

    # ── 持久化读写 ──
    @property
    def _vec_file(self):
        return self.path / "vectors.npy"

    @property
    def _meta_file(self):
        return self.path / "metadata.json"

    @property
    def _vocab_file(self):
        return self.path / "vocab.json"

    def _load(self):
        """从磁盘加载数据"""
        try:
            if self._meta_file.exists():
                with open(self._meta_file, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
            if self._vocab_file.exists():
                with open(self._vocab_file, "r", encoding="utf-8") as f:
                    self.vocab = json.load(f)
            if self._vec_file.exists():
                self.vectors = np.load(self._vec_file)

            # 校验一致性
            if self.vectors is not None and len(self.metadata) != len(self.vectors):
                print(f"[vector-store] ⚠️ 数据不一致: vectors={len(self.vectors)} vs metadata={len(self.metadata)}，以 metadata 为准")
                if self.metadata:
                    dim = self.vectors.shape[1] if len(self.vectors) > 0 else 0
                    self.vectors = self.vectors[:len(self.metadata)]
                else:
                    self.vectors = None

            print(f"[vector-store] 加载完成 · {len(self.metadata)} docs · vocab={len(self.vocab) - 3 if self.vocab else 0} tokens")
        except Exception as e:
            print(f"[vector-store] ⚠️ 加载失败，初始化空存储: {e}")
            self.metadata = []
            self.vocab = {}
            self.vectors = None

    def _save(self):
        """原子写入 —— 先写临时文件再 rename"""
        def _atomic_write(filepath: Path, data, dump_fn):
            tmp = filepath.with_suffix(filepath.suffix + ".tmp")
            try:
                dump_fn(tmp, data)
                tmp.replace(filepath)
            except Exception as e:
                print(f"[vector-store] ⚠️ 保存 {filepath} 失败: {e}")
                if tmp.exists():
                    tmp.unlink()

        # metadata.json
        _atomic_write(self._meta_file, self.metadata,
                      lambda f, d: f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"))
        # vocab.json
        _atomic_write(self._vocab_file, self.vocab,
                      lambda f, d: f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8"))
        # vectors.npy —— np.save 会自动给无 .npy 后缀的路径加后缀
        if self.vectors is not None and len(self.vectors) > 0:
            # 用固定 tmp 文件名，确保 np.save 不会再加额外后缀
            tmp = self.path / ".vectors_tmp"
            try:
                np.save(str(tmp), self.vectors, allow_pickle=False)
                # np.save 会生成 .vectors_tmp.npy → 改名成目标
                saved = self.path / ".vectors_tmp.npy"
                if saved.exists():
                    if self._vec_file.exists():
                        self._vec_file.unlink()
                    saved.rename(self._vec_file)
                else:
                    # 某些版本 numpy 可能直接用 tmp 名
                    tmp_path = self.path / ".vectors_tmp"
                    if tmp_path.exists():
                        if self._vec_file.exists():
                            self._vec_file.unlink()
                        tmp_path.rename(self._vec_file)
                    else:
                        raise RuntimeError(f"np.save 未生成文件: 尝试了 {saved} 和 {tmp_path}")
            except Exception as e:
                print(f"[vector-store] ⚠️ 保存 vectors.npy 失败: {e}")
                for p in [self.path / ".vectors_tmp", self.path / ".vectors_tmp.npy"]:
                    if p.exists():
                        p.unlink()
        elif self._vec_file.exists():
            self._vec_file.unlink()

    # ── 词表构建 ──
    def _build_vocab_from_scratch(self) -> dict:
        """从所有 metadata 重建词表 + 计算 DF"""
        vocab = {}
        df = []
        n_docs = len(self.metadata)

        # 第一遍：收集所有 token
        tokenized_docs = []
        for md in self.metadata:
            tokens = _chinese_tokenize(md.get("text", ""))
            tokenized_docs.append(tokens)
            for t in tokens:
                if t not in vocab:
                    if len(vocab) >= self.MAX_VOCAB_SIZE:
                        break
                    vocab[t] = len(vocab)
            if len(vocab) >= self.MAX_VOCAB_SIZE:
                break

        # 第二遍：计算 DF（每个 token 出现在多少篇文档里）
        df = [0] * len(vocab)
        for tokens in tokenized_docs:
            seen = set()
            for t in tokens:
                if t in vocab and t not in seen:
                    df[vocab[t]] += 1
                    seen.add(t)

        vocab["_df"] = df
        vocab["_n_docs"] = n_docs
        vocab["_dim"] = len(vocab) - 3  # 减去 _df, _n_docs, _dim 元数据
        return vocab

    def _rebuild_all(self):
        """大规模变更后重建词表 + 重算所有向量"""
        if not self.metadata:
            self.vocab = {}
            self.vectors = None
            return
        print(f"[vector-store] 重建词表 + 重算向量 · {len(self.metadata)} docs")
        self.vocab = self._build_vocab_from_scratch()
        self.vectors = self._compute_vectors([md.get("text", "") for md in self.metadata])
        self._save()

    def _compute_vectors(self, texts: list[str]) -> np.ndarray:
        """批量 TF-IDF 向量化"""
        dim = self.vocab.get("_dim", 0)
        if dim == 0:
            return np.zeros((len(texts), 0), dtype=np.float32)

        df_list = self.vocab.get("_df", [])
        n_docs_total = self.vocab.get("_n_docs", len(texts))

        vectors = np.zeros((len(texts), dim), dtype=np.float32)
        for i, text in enumerate(texts):
            tokens = _chinese_tokenize(text)
            for t in tokens:
                idx = self.vocab.get(t)
                if idx is not None and idx < dim:
                    vectors[i, idx] += 1

        # IDF 加权
        if df_list:
            for j in range(min(dim, len(df_list))):
                df = df_list[j]
                if df > 0:
                    idf = math.log((n_docs_total + 1) / (df + 1) + 1)
                    vectors[:, j] *= idf

        # L2 归一化（每行）
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        vectors = vectors / norms

        return vectors

    # ── API 方法（对齐 ChromaDB Collection）──
    def count(self) -> int:
        return len(self.metadata)

    def add(self, documents: list[str], ids: list[str], metadatas: list[dict] | None = None):
        """添加文档"""
        if not documents:
            return
        if metadatas is None:
            metadatas = [{} for _ in documents]

        # 追加 metadata
        for i, doc_id in enumerate(ids):
            self.metadata.append({
                "id": doc_id,
                "text": documents[i],
                "source": metadatas[i].get("source", "unknown"),
                "strategy": metadatas[i].get("strategy", "default"),
            })

        # 重建（新文档可能引入新词 → 词表变化 → 全量重算）
        self._rebuild_all()

    def update(self, ids: list[str], documents: list[str] | None = None, metadatas: list[dict] | None = None):
        """更新文档"""
        for i, doc_id in enumerate(ids):
            # 找到对应 metadata 的索引
            idx = None
            for j, md in enumerate(self.metadata):
                if md["id"] == doc_id:
                    idx = j
                    break
            if idx is None:
                print(f"[vector-store] ⚠️ update: id {doc_id} 不存在")
                continue

            if documents is not None and i < len(documents):
                self.metadata[idx]["text"] = documents[i]
            if metadatas is not None and i < len(metadatas):
                self.metadata[idx]["source"] = metadatas[i].get("source", self.metadata[idx]["source"])
                self.metadata[idx]["strategy"] = metadatas[i].get("strategy", self.metadata[idx]["strategy"])

        # 重算（text 可能变了 → 词表可能变了）
        self._rebuild_all()

    def delete(self, ids: list[str]):
        """删除文档"""
        ids_set = set(ids)
        new_meta = [md for md in self.metadata if md["id"] not in ids_set]
        removed = len(self.metadata) - len(new_meta)
        self.metadata = new_meta

        if removed > 0:
            self._rebuild_all()

    def get(
        self,
        ids: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        where: dict | None = None,
        include: list[str] | None = None,
    ) -> dict:
        """按条件获取文档 —— 对齐 ChromaDB Collection.get() 返回格式"""
        results = self.metadata[:]

        # where 过滤
        if where:
            results = [md for md in results if all(md.get(k) == v for k, v in where.items())]

        # ids 过滤
        if ids:
            ids_set = set(ids)
            results = [md for md in results if md["id"] in ids_set]

        # 分页
        total = len(results)
        if limit is not None:
            results = results[offset:offset + limit]

        # 构造返回格式
        ret = {"ids": [md["id"] for md in results]}
        if include is None or "metadatas" in include:
            ret["metadatas"] = [{"source": md["source"], "strategy": md["strategy"]} for md in results]
        if include is None or "documents" in include:
            ret["documents"] = [md["text"] for md in results]
        # ChromaDB get 不返回 distances
        return ret

    def query(
        self,
        query_texts: list[str],
        n_results: int = 5,
        where: dict | None = None,
        include: list[str] | None = None,
        use_bm25: bool = True,
    ) -> dict:
        """混合检索 —— 向量 + BM25 + RRF 融合，返回格式对齐 ChromaDB Collection.query()"""
        if not self.metadata or self.vectors is None or len(self.vectors) == 0:
            empty = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
            return empty

        query = query_texts[0] if query_texts else ""
        results_per_query = []

        for q_text in query_texts:
            q_text = q_text or ""
            n = min(n_results, len(self.metadata))

            # ── 1. 向量检索（余弦相似度）──
            q_vec = self._compute_vectors([q_text])[0]  # (dim,)
            if np.linalg.norm(q_vec) == 0:
                # query 无有效 token → 用第一个文档兜底
                vec_sims = np.zeros(len(self.metadata))
            else:
                # 余弦相似度
                norms = np.linalg.norm(self.vectors, axis=1)
                norms = np.where(norms == 0, 1, norms)
                vec_sims = self.vectors @ q_vec / norms

            # where 过滤（把不匹配的相似度置为 -inf）
            if where:
                for i, md in enumerate(self.metadata):
                    if not all(md.get(k) == v for k, v in where.items()):
                        vec_sims[i] = -np.inf

            # 取 top-k*3 给融合空间
            pool_size = min(n * 3, len(self.metadata)) if use_bm25 else n
            vec_top_idx = np.argsort(-vec_sims)[:pool_size]
            vec_top = [(int(i), float(vec_sims[i])) for i in vec_top_idx if vec_sims[i] > -np.inf]

            if not vec_top:
                results_per_query.append({"ids": [], "documents": [], "metadatas": [], "distances": []})
                continue

            # ── 2. BM25 检索 ──
            bm25_top = []
            if use_bm25 and JIEBA_AVAILABLE:
                # 只在向量召回的池子里跑 BM25
                pool_tokens = [_chinese_tokenize(self.metadata[i]["text"]) for i, _ in vec_top]
                query_tokens = _chinese_tokenize(q_text)
                bm25_scores = _bm25_score(query_tokens, pool_tokens)
                bm25_ranked = sorted(enumerate(bm25_scores), key=lambda x: -x[1])[:pool_size]
                bm25_top = [(vec_top[i][0], s) for i, s in bm25_ranked]

            # ── 3. RRF 融合 ──
            fused_ranks = {}
            for rank, (doc_idx, _) in enumerate(vec_top):
                fused_ranks[doc_idx] = fused_ranks.get(doc_idx, 0) + 1 / (60 + rank)
            for rank, (doc_idx, _) in enumerate(bm25_top):
                fused_ranks[doc_idx] = fused_ranks.get(doc_idx, 0) + 1 / (60 + rank)

            final_idx = sorted(fused_ranks.keys(), key=lambda i: -fused_ranks[i])[:n]

            # 构造返回
            ids = [self.metadata[i]["id"] for i in final_idx]
            docs = [self.metadata[i]["text"] for i in final_idx]
            metas = [{"source": self.metadata[i]["source"], "strategy": self.metadata[i]["strategy"]} for i in final_idx]
            # distance = 1 - cosine_similarity（ChromaDB 习惯：小=更相似）
            distances = [float(1 - vec_sims[i]) if vec_sims[i] > -np.inf else 1.0 for i in final_idx]

            results_per_query.append({
                "ids": ids,
                "documents": docs,
                "metadatas": metas,
                "distances": distances,
            })

        # ChromaDB query 返回的是 list of lists
        ret = {
            "ids": [r["ids"] for r in results_per_query],
            "documents": [r["documents"] for r in results_per_query],
            "distances": [r["distances"] for r in results_per_query],
        }
        if include is None or "metadatas" in include:
            ret["metadatas"] = [r["metadatas"] for r in results_per_query]
        return ret


# ── BM25 打分（纯 numpy，无依赖）──
def _bm25_score(query_tokens: list, corpus_tokens: list, k1: float = 1.5, b: float = 0.75) -> list:
    """BM25Okapi 实现 —— 纯 numpy"""
    if not corpus_tokens or not query_tokens:
        return [0.0] * len(corpus_tokens)

    avg_len = np.mean([len(c) for c in corpus_tokens]) if corpus_tokens else 0
    n_docs = len(corpus_tokens)

    # DF：每个 token 出现在多少篇文档
    df = {}
    for c in corpus_tokens:
        for t in set(c):
            df[t] = df.get(t, 0) + 1

    # BM25 核心公式
    scores = []
    for doc in corpus_tokens:
        dl = len(doc)
        score = 0.0
        for qt in query_tokens:
            f = doc.count(qt)
            if f == 0:
                continue
            idf = math.log((n_docs - df.get(qt, 0) + 0.5) / (df.get(qt, 0) + 0.5) + 1)
            denom = f + k1 * (1 - b + b * dl / avg_len) if avg_len > 0 else f + k1
            score += idf * f * (k1 + 1) / denom
        scores.append(score)
    return scores


# ── 混合检索 + RRF（供 /query 端点使用）──
def _hybrid_search(query: str, vector_store: SimpleVectorStore, top_k: int = 5, use_bm25: bool = True):
    """混合检索 —— 返回 (chunks, distances, metadatas)"""
    vector_k = top_k * 3 if use_bm25 else top_k
    results = vector_store.query(
        query_texts=[query],
        n_results=vector_k,
        include=["documents", "metadatas", "distances"],
        use_bm25=use_bm25,
    )
    chunks = results["documents"][0] if results["documents"] else []
    distances = results["distances"][0] if results["distances"] else []
    metadatas = results["metadatas"][0] if results.get("metadatas") else []

    return chunks[:top_k], distances[:top_k], metadatas[:top_k] or [{}] * top_k


# ═══════════════════════════════════════════════════════════════════
# ③ SimpleVectorStore 初始化
# ═══════════════════════════════════════════════════════════════════
_default = str(Path(__file__).parent / "chroma_data")
CHROMA_PATH = os.environ.get("CHROMA_PATH") or ("/mnt/chroma" if os.path.isdir("/mnt/chroma") else _default)
VECTOR_STORE = SimpleVectorStore(path=CHROMA_PATH)
print(f"[init] ✅ SimpleVectorStore ready · path={CHROMA_PATH} · docs={VECTOR_STORE.count()}")


# ═══════════════════════════════════════════════════════════════════
# ④ FastAPI + API Key 鉴权
# ═══════════════════════════════════════════════════════════════════
app = FastAPI(title="LTC RAG Bot", version="3.0")

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
# ⑤ 飞书配置
# ═══════════════════════════════════════════════════════════════════
FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "").strip()
FEISHU_VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "").strip()

# 事件去重内存 set（进程级，重启清空）
_EVENT_IDS = set()

def _feishu_decrypt(encrypt_str: str) -> dict:
    """飞书 AES-256-CBC 加密解密（pycryptodome）"""
    if not FEISHU_ENCRYPT_KEY:
        raise ValueError("FEISHU_ENCRYPT_KEY not configured")
    from Crypto.Cipher import AES
    key = hashlib.sha256(FEISHU_ENCRYPT_KEY.encode("utf-8")).digest()
    raw = base64.b64decode(encrypt_str)
    iv = raw[:16]
    ciphertext = raw[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(ciphertext)
    pad_len = decrypted[-1]
    if 1 <= pad_len <= 16 and decrypted[-pad_len:] == bytes([pad_len]) * pad_len:
        decrypted = decrypted[:-pad_len]
    return json.loads(decrypted.decode("utf-8"))


def _is_duplicate_event(event_id: str) -> bool:
    if not event_id:
        return False
    if event_id in _EVENT_IDS:
        return True
    _EVENT_IDS.add(event_id)
    if len(_EVENT_IDS) > 10000:
        _EVENT_IDS.clear()
    return False

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
# ⑥ LLM 调用 —— 多级 fallback
# ═══════════════════════════════════════════════════════════════════
def call_llm(prompt: str) -> tuple:
    """统一 LLM 调用 —— 多级 fallback 永不崩

    优先级: 智谱(1) → 硅基流动(2) → Ollama(3) → 模板(4)
    """

    # ── 优先级 1: 智谱 AI (glm-4-flash / glm-4-air) ──
    zhipu_key = (os.environ.get("ZHIPU_API_KEY") or os.environ.get("ZHIPUAI_API_KEY") or "").strip()
    zhipu_model = (os.environ.get("ZHIPU_MODEL") or os.environ.get("ZHIPUAI_MODEL") or "glm-4-flash").strip()
    if zhipu_key:
        try:
            r = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={
                    "Authorization": f"Bearer {zhipu_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": zhipu_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
                timeout=30,
            )
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print(f"[llm] ✅ 智谱 {zhipu_model} · {len(content)} chars")
            return content, f"zhipu:{zhipu_model}"
        except Exception as e:
            print(f"[llm] ❌ 智谱失败: {e} · 继续尝试...")

    # ── 优先级 2: 硅基流动 (SiliconFlow) ──
    sf_key = (os.environ.get("SILICONFLOW_API_KEY") or os.environ.get("SF_API_KEY") or "").strip()
    sf_model = (os.environ.get("SILICONFLOW_MODEL") or os.environ.get("SF_MODEL") or "deepseek-ai/DeepSeek-V3").strip()
    if sf_key:
        try:
            r = requests.post(
                "https://api.siliconflow.cn/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {sf_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": sf_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
                timeout=30,
            )
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print(f"[llm] ✅ 硅基流动 {sf_model} · {len(content)} chars")
            return content, f"siliconflow:{sf_model}"
        except Exception as e:
            print(f"[llm] ❌ 硅基流动失败: {e} · 继续尝试...")

    # ── 优先级 3: Ollama 本地 ──
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
            print(f"[llm] ✅ Ollama model={ollama_model} · {len(content)} chars")
            return content, f"ollama:{ollama_model}"
    except Exception as e:
        print(f"[llm] Ollama 不可用: {e} · fallback")

    # ── 优先级 4: 模板化回答 ──
    return _template_fallback(prompt), "template-fallback"


def _template_fallback(prompt: str) -> str:
    return (
        "【⚠️ 当前无可用 LLM · 以下为 RAG 检索原始片段】\n\n"
        "建议在 CloudBase 环境变量中配置以下任一 API Key：\n"
        "  • 智谱:         ZHIPU_API_KEY\n"
        "  • 硅基流动:     SILICONFLOW_API_KEY\n"
        "  • Ollama:       本地部署（CloudBase 不适用）\n\n"
        "——— 原始检索结果 ———\n"
        f"{prompt}\n"
    )


# ═══════════════════════════════════════════════════════════════════
# ⑦ 切片策略配置 —— configs/kb_strategies.json
# ═══════════════════════════════════════════════════════════════════
def _load_strategies() -> dict:
    p = Path(__file__).parent / "configs" / "kb_strategies.json"
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception as e:
            print(f"[config] 策略加载失败: {e}")
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

@app.get("/strategies")
def list_strategies():
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
    s = CHUNK_STRATEGIES.get(strategy, CHUNK_STRATEGIES[DEFAULT_STRATEGY])
    mode = s.get("mode", "paragraph")
    min_len = s.get("min_chunk_len", 20)
    max_len = s.get("max_chunk_len", 2000)

    if mode == "paragraph":
        chunks = [c.strip() for c in text.split("\n\n") if len(c.strip()) > min_len]
        chunks = _split_by_sentence_if_big(chunks, min_len, max_len)
    elif mode == "sentence":
        chunks = [c.strip() for c in re.split(r'[。；\n]', text) if len(c.strip()) > min_len]
    else:
        chunks = [text]

    if not chunks:
        chunks = [c.strip() for c in re.split(r'[。；\n]', text) if len(c.strip()) > min_len]
    if not chunks:
        chunks = [text]

    return chunks

def _split_by_sentence_if_big(chunks: list, min_len: int, max_len: int) -> list:
    result = []
    for c in chunks:
        if len(c) <= max_len:
            result.append(c)
        else:
            sub = [x.strip() for x in re.split(r'[。；\n]', c) if len(x.strip()) > min_len]
            result.extend(sub if sub else [c])
    return result


# ═══════════════════════════════════════════════════════════════════
# ⑧ Prompt 模板管理 —— prompts/*.txt
# ═══════════════════════════════════════════════════════════════════
PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPTS_DIR.mkdir(exist_ok=True)

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
    p = PROMPTS_DIR / f"{name}.txt"
    template = p.read_text(encoding="utf-8") if p.exists() else _BUILTIN_PROMPTS.get(name, _BUILTIN_PROMPTS["fabe"])
    return template.format(**kwargs) if kwargs else template

@app.get("/prompts/{name}")
def get_prompt(name: str, _: bool = Depends(verify_api_key)):
    p = PROMPTS_DIR / f"{name}.txt"
    content = p.read_text(encoding="utf-8") if p.exists() else _BUILTIN_PROMPTS.get(name, "")
    return {"name": name, "content": content, "builtin": not p.exists()}

@app.put("/prompts/{name}")
def put_prompt(name: str, body: dict, _: bool = Depends(verify_api_key)):
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(400, "content required")
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise HTTPException(400, "invalid prompt name")
    p = PROMPTS_DIR / f"{name}.txt"
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "name": name, "path": str(p)}

@app.get("/prompts")
def list_prompts(_: bool = Depends(verify_api_key)):
    builtin_names = set(_BUILTIN_PROMPTS.keys())
    file_names = {p.stem for p in PROMPTS_DIR.glob("*.txt")}
    all_names = builtin_names | file_names
    return [
        {"name": n, "has_file": n in file_names, "has_builtin": n in builtin_names}
        for n in sorted(all_names)
    ]


# ═══════════════════════════════════════════════════════════════════
# ⑨ 端点实现
# ═══════════════════════════════════════════════════════════════════

@app.get("/")
async def portal():
    """知识库管理门户 —— 返回 portal/index.html"""
    portal_path = Path(__file__).parent / "portal" / "index.html"
    if portal_path.exists():
        return FileResponse(portal_path)
    return {
        "service": "LTC RAG Bot v3.0",
        "health": "ok",
        "hint": "把 portal/index.html 放到 portal/ 目录下即可启用管理门户",
    }

@app.get("/portal")
async def portal_alt():
    """Portal 备用路径 —— 307 redirect 到 /"""
    return RedirectResponse(url="/", status_code=307)

@app.get("/health")
def health():
    """健康检查（公开免鉴权）"""
    return {
        "status": "ok",
        "version": "3.0",
        "docs_count": VECTOR_STORE.count(),
        "embedding": "TF-IDF + Jieba (自实现, 零模型下载)",
        "vector_dim": VECTOR_STORE.vocab.get("_dim", 0) if VECTOR_STORE.vocab else 0,
        "api_key_mode": "开发模式(无强制鉴权)" if API_KEY == "dev-only-key-change-in-prod" else "生产模式(已开启鉴权)",
        "feishu_configured": bool(FEISHU_APP_ID and FEISHU_APP_SECRET),
    }

# ── 知识库文档管理 ──

@app.get("/kb")
async def list_knowledge(_: bool = Depends(verify_api_key)):
    """列出所有已入库文档（按 source 分组）"""
    meta = VECTOR_STORE.metadata
    sources = {}
    ids_by_source = {}
    for md in meta:
        src = md.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        ids_by_source.setdefault(src, []).append(md["id"])

    return {
        "total_chunks": VECTOR_STORE.count(),
        "sources": sources,
        "ids_by_source": ids_by_source,
    }

@app.delete("/kb/{chunk_id}")
async def delete_chunk(chunk_id: str, _: bool = Depends(verify_api_key)):
    """删除单个切片"""
    try:
        VECTOR_STORE.delete(ids=[chunk_id])
        return {"ok": True, "deleted": chunk_id, "remaining": VECTOR_STORE.count()}
    except Exception as e:
        raise HTTPException(400, f"删除失败: {e}")

@app.delete("/kb")
async def clear_all_knowledge(_: bool = Depends(verify_api_key)):
    """清空整个知识库（危险操作）"""
    all_ids = [md["id"] for md in VECTOR_STORE.metadata]
    if all_ids:
        VECTOR_STORE.delete(ids=all_ids)
    return {"ok": True, "cleared_all": True}

@app.post("/ingest")
async def ingest(
    file: UploadFile,
    _: bool = Depends(verify_api_key),
    strategy: str = "default",
):
    """上传 .pdf / .md / .txt 文件入库"""
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

    VECTOR_STORE.add(documents=chunks, ids=ids, metadatas=metadatas)
    return {"ingested": len(chunks), "source": source, "strategy": strategy, "total": VECTOR_STORE.count()}

@app.post("/ingest-text")
async def ingest_text(
    body: dict,
    _: bool = Depends(verify_api_key),
):
    """直接录入一段文本"""
    text = body.get("text", "").strip()
    source = body.get("source", "inline")
    strategy = body.get("strategy", "default")
    if not text:
        raise HTTPException(400, "text required")

    chunks = _chunk_text(text, strategy=strategy)
    ids = [str(uuid.uuid4()) for _ in chunks]
    metadatas = [{"source": source, "strategy": strategy} for _ in chunks]

    VECTOR_STORE.add(documents=chunks, ids=ids, metadatas=metadatas)
    return {"ingested": len(chunks), "source": source, "strategy": strategy, "total": VECTOR_STORE.count()}

# ── 切片管理 ──

@app.get("/chunks")
def list_chunks(
    page: int = Query(1, ge=1, description="第几页"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    source: str | None = Query(None, description="按来源过滤"),
    strategy: str | None = Query(None, description="按切片策略过滤"),
    keyword: str | None = Query(None, description="按关键词全文搜索"),
):
    """分页列出所有切片 —— 支持按 source/strategy/keyword 过滤"""
    where = {}
    if source:
        where["source"] = source
    if strategy:
        where["strategy"] = strategy

    # keyword 搜索 → 用混合检索
    if keyword:
        chunks, distances, metadatas = _hybrid_search(keyword, VECTOR_STORE, top_k=page_size * 3, use_bm25=True)
        if where:
            # where 过滤
            filtered = []
            filtered_d = []
            filtered_m = []
            for c, d, m in zip(chunks, distances, metadatas):
                if all(m.get(k) == v for k, v in where.items()):
                    filtered.append(c)
                    filtered_d.append(d)
                    filtered_m.append(m)
            chunks = filtered[:page_size]
            distances = filtered_d[:page_size]
            metadatas = filtered_m[:page_size]
        total = len(chunks)
        chunks_data = []
        for i, (c, d, m) in enumerate(zip(chunks, distances, metadatas)):
            chunks_data.append({
                "id": f"kw_{i}",
                "text": c,
                "text_preview": (c[:80] + "…") if len(c) > 80 else c,
                "length": len(c),
                "source": (m or {}).get("source", "unknown"),
                "strategy": (m or {}).get("strategy", "unknown"),
                "score": round(1 - d, 4),
            })
    else:
        offset = (page - 1) * page_size
        result = VECTOR_STORE.get(limit=page_size, offset=offset, where=where if where else None, include=["documents", "metadatas"])
        total = VECTOR_STORE.count()
        ids = result["ids"]
        docs = result["documents"]
        metas = result["metadatas"]

        chunks_data = []
        for i, cid in enumerate(ids):
            text = docs[i] if docs else ""
            m = metas[i] if metas else {}
            chunks_data.append({
                "id": cid,
                "text": text,
                "text_preview": (text[:80] + "…") if len(text) > 80 else text,
                "length": len(text),
                "source": (m or {}).get("source", "unknown"),
                "strategy": (m or {}).get("strategy", "unknown"),
                "score": None,
            })

    total_all = VECTOR_STORE.count()
    return JSONResponse({
        "total": total if keyword else total_all,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total if keyword else total_all + page_size - 1) // page_size),
        "chunks": chunks_data,
    })

@app.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: str):
    """单条切片详情"""
    result = VECTOR_STORE.get(ids=[chunk_id], include=["documents", "metadatas"])
    if not result["ids"]:
        raise HTTPException(404, "chunk not found")
    return JSONResponse({
        "id": chunk_id,
        "text": result["documents"][0],
        "length": len(result["documents"][0]),
        "metadata": result["metadatas"][0] or {},
    })

@app.patch("/chunks/{chunk_id}")
def update_chunk(chunk_id: str, body: dict):
    """手动修改切片 —— 自动重算向量"""
    result = VECTOR_STORE.get(ids=[chunk_id], include=["documents", "metadatas"])
    if not result["ids"]:
        raise HTTPException(404, "chunk not found")

    updates = {}
    new_text = body.get("text")
    new_source = body.get("source")
    new_strategy = body.get("strategy")

    if new_text is not None:
        if len(new_text.strip()) < 5:
            raise HTTPException(400, "text too short (min 5 chars)")
        updates["documents"] = [new_text]

    if new_source or new_strategy:
        old_meta = result["metadatas"][0] or {}
        new_meta = dict(old_meta)
        if new_source:
            new_meta["source"] = new_source
        if new_strategy:
            new_meta["strategy"] = new_strategy
        updates["metadatas"] = [new_meta]

    if not updates:
        raise HTTPException(400, "nothing to update —— 需要 text 或 source 或 strategy")

    VECTOR_STORE.update(ids=[chunk_id], documents=updates.get("documents"), metadatas=updates.get("metadatas"))

    result2 = VECTOR_STORE.get(ids=[chunk_id], include=["documents", "metadatas"])
    return JSONResponse({
        "updated": True,
        "id": chunk_id,
        "new_text": result2["documents"][0],
        "new_metadata": result2["metadatas"][0] or {},
    })

@app.delete("/chunks/{chunk_id}")
def delete_single_chunk(chunk_id: str, _: bool = Depends(verify_api_key)):
    """删除单个切片（/chunks/{id} 路径，替代 /kb/{id}）"""
    VECTOR_STORE.delete(ids=[chunk_id])
    return {"ok": True, "deleted": chunk_id, "remaining": VECTOR_STORE.count()}

# ── RAG 核心逻辑（独立函数，可被路由层和 webhook 直接调用） ──

def _do_rag(question: str, top_k: int = 5, use_bm25: bool = True) -> dict:
    """RAG + LLM FABE 问答 —— 纯函数，不做鉴权，返回结构化 dict"""
    q = question.strip()
    if not q:
        raise ValueError("question required")

    chunks, distances, metadatas_list = _hybrid_search(q, VECTOR_STORE, top_k=top_k, use_bm25=use_bm25)

    if not chunks:
        return {
            "answer": "⚠️ 知识库为空，请先上传产品文档。",
            "sources": [],
            "total_docs": 0,
            "hybrid": {"bm25_available": JIEBA_AVAILABLE, "use_bm25": False},
            "llm_used": "none",
        }

    top3 = list(zip(chunks[:3], distances[:3], metadatas_list[:3]))
    context_block = "\n".join(
        f"[{i+1}] (相似度 {1-d:.3f}, 来源 {(m or {}).get('source','?')}) {c}"
        for i, (c, d, m) in enumerate(top3)
    )

    fabe_prompt = _get_prompt("fabe", question=q, context=context_block)
    llm_reply, llm_backend = call_llm(fabe_prompt)

    if llm_backend == "template-fallback":
        answer = f"**问题**：{q}\n\n**📎 参考片段**：\n{context_block}\n\n{llm_reply}"
    else:
        answer = f"**问题**：{q}\n\n**🤖 AI 话术（FABE · {llm_backend}）**：\n{llm_reply}\n\n**📎 参考片段**：\n{context_block}"

    return {
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
        "total_docs": VECTOR_STORE.count(),
        "hybrid": {
            "bm25_available": JIEBA_AVAILABLE,
            "jieba_available": JIEBA_AVAILABLE,
            "use_bm25": use_bm25 and JIEBA_AVAILABLE,
        },
    }


# ── RAG 问答路由（thin wrapper：鉴权 → _do_rag → 返回 JSONResponse） ──

@app.post("/query")
async def query(body: dict, _: bool = Depends(verify_api_key)):
    """RAG + LLM FABE 问答 —— 混合检索"""
    q = body.get("question", "").strip()
    if not q:
        raise HTTPException(400, "question required")

    result = _do_rag(
        question=q,
        top_k=body.get("top_k", 5),
        use_bm25=body.get("use_bm25", True),
    )
    return JSONResponse(result)

# ── 飞书 Webhook（增强版 v2）──

@app.post("/webhook")
async def webhook(payload: dict, request: Request):
    """飞书事件回调 · 带加密解密 + token 校验 + 事件去重 + 完整日志"""
    try:
        raw = await request.body()
        print(f"[webhook] ← 收到 {len(raw)} bytes")

        # 1. 解密（如果飞书发的是加密格式）
        if "encrypt" in payload and FEISHU_ENCRYPT_KEY:
            try:
                payload = _feishu_decrypt(payload["encrypt"])
                print("[webhook] 🔓 解密成功")
            except Exception as e:
                print(f"[webhook] ❌ 解密失败: {e}")
                return {"ok": False, "error": "decrypt_failed"}

        # 2. Verification Token 校验（可选，不配就跳过）
        if FEISHU_VERIFICATION_TOKEN:
            token = payload.get("token", "")
            if token and token != FEISHU_VERIFICATION_TOKEN:
                print(f"[webhook] ❌ token mismatch (got={token[:8]}..)")
                return {"ok": False}

        # 3. URL Verification（飞书回调配置时发的 challenge）
        if "challenge" in payload and "type" in payload:
            print("[webhook] ← URL verification challenge")
            return {"challenge": payload["challenge"]}

        # 4. Ping / Heartbeat
        if payload.get("type") == "ping":
            return {"ok": True}

        # 5. 事件去重
        event_id = payload.get("header", {}).get("event_id", "")
        if event_id and _is_duplicate_event(event_id):
            print(f"[webhook] ↻ 重复事件跳过: {event_id[:12]}")
            return {"ok": True}

        # 6. 提取消息
        evt = payload.get("event", {})
        msg = evt.get("message", {})
        header_type = payload.get("header", {}).get("event_type", "")
        chat_type = msg.get("chat_type", "")
        msg_type = msg.get("message_type", "")

        print(f"[webhook] 📋 event={header_type} chat={chat_type} msg={msg_type} "
              f"chat_id={msg.get('chat_id','')[:12]}... sender={evt.get('sender',{}).get('sender_id',{}).get('open_id','')[:12]}...")

        if msg_type != "text":
            print(f"[webhook] ⏭️ 非文本消息，跳过")
            return {"ok": True}

        try:
            text = json.loads(msg.get("content", "{}")).get("text", "").strip()
        except Exception:
            text = ""

        if not text:
            print("[webhook] ⏭️ 空文本")
            return {"ok": True}

        # 7. 去掉 @机器人 的 mention tag
        original_text = text
        text = re.sub(r'<at[^>]*>.*?</at>', '', text).strip()
        text = re.sub(r'@\S+\s*', '', text).strip()
        text = text.replace('@_user_1', '').strip()

        if not text:
            print(f"[webhook] ⏭️ 只有 @mention，无实际内容 (raw='{original_text[:50]}')")
            return {"ok": True}

        if text.startswith("/"):
            print(f"[webhook] ⏭️ 命令消息，跳过: {text[:30]}")
            return {"ok": True}

        print(f"[webhook] 💬 处理消息: '{text[:80]}'")

        # 8. RAG 查询 —— 直接调 _do_rag()，跳过 FastAPI 路由层的 API key 鉴权
        reply = ""
        llm_used = ""
        try:
            rag_result = _do_rag(question=text)
            reply = rag_result.get("answer", "")
            llm_used = rag_result.get("llm_used", "")
            print(f"[webhook] ✅ RAG 完成 · llm={llm_used} · total_docs={rag_result.get('total_docs',0)}")
        except Exception as e:
            print(f"[webhook] ❌ RAG 查询失败: {e}")
            reply = f"⚠️ 查询出错: {str(e)[:200]}"

        # 9. 飞书回复
        token = feishu_token()
        if not token:
            print("[webhook] ❌ 无 FEISHU_APP_ID/SECRET，跳过回复")
            return {"ok": True}

        chat_id = msg.get("chat_id", "")
        if not chat_id:
            print("[webhook] ❌ 无 chat_id，跳过回复")
            return {"ok": True}

        try:
            r = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": reply[:4000]}),
                },
                timeout=15,
            )
            resp = r.json()
            if resp.get("code") == 0:
                print(f"[webhook] ✅ 飞书回复成功 (chat={chat_id[:12]}... msg_id={resp.get('data',{}).get('message_id','?')})")
            else:
                print(f"[webhook] ❌ 飞书回复失败: code={resp.get('code')} msg={resp.get('msg')} detail={json.dumps(resp)}")
        except Exception as e:
            print(f"[webhook] ❌ 发送飞书异常: {e}")

        return {"ok": True}

    except Exception as e:
        print(f"[webhook] ❌ 顶层异常: {e}")
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(e)[:200]}


# ═══════════════════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 LTC RAG Bot v3.0 启动 · port={port} · embedding=TF-IDF+Jieba · 内存优化版")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
