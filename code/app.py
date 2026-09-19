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
from datetime import datetime, timezone
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
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, HTMLResponse
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
# ①-B 可插拔 Embedding 后端（V4.1 · 默认 tfidf，行为与旧版一致）
#      EMBED_BACKEND = "tfidf"（默认，零依赖零成本）
#                    | "siliconflow"（OpenAI 兼容 /v1/embeddings 稠密向量）
#    ⚠️ 只有显式把 EMBED_BACKEND 设为 siliconflow 才会改变检索行为；
#       未设置时走原 TF-IDF 路径，零回归。
# ═══════════════════════════════════════════════════════════════════
EMBED_BACKEND_OVERRIDE = (os.environ.get("EMBED_BACKEND") or "").strip().lower()  # 显式覆盖；空=跟随磁盘标记
EMBED_BACKEND = EMBED_BACKEND_OVERRIDE or "tfidf"   # 无磁盘标记时的兜底默认
EMBED_MODEL   = (os.environ.get("EMBED_MODEL") or "BAAI/bge-m3").strip()
EMBED_API_URL = (os.environ.get("EMBED_API_URL") or "https://api.siliconflow.cn/v1/embeddings").strip()
EMBED_API_KEY = (os.environ.get("SILICONFLOW_API_KEY") or os.environ.get("SF_API_KEY") or "").strip()
EMBED_BATCH   = int(os.environ.get("EMBED_BATCH") or "16")

# ── V5-M2 省 Token 三件套开关 ──────────────────────────────────────
# CACHE_SIM_THRESHOLD：L1 语义近似命中阈值（问句向量余弦）。
#   0.92 = 保守（宁可漏命中，也不把不同问题当同一个）；设 <0.5 视为关闭 L1
CACHE_SIM_THRESHOLD = float(os.environ.get("CACHE_SIM_THRESHOLD") or "0.92")
# REJECT_THRESHOLD：L2 拒答短路阈值（top1 检索相似度）。
#   默认 0 = 关闭 —— 先埋点观测真实分布，再按数据定阈值，避免拍脑袋误杀
REJECT_THRESHOLD    = float(os.environ.get("REJECT_THRESHOLD") or "0")
# SOFT_REFUSE_THRESHOLD：软拒答注入阈值（top1 低于它时向 prompt 注入强拒答指令，最终由 LLM 判断）。
#   与 L2 硬短路的区别：不直接拦，LLM 仍可基于真实相关内容作答 —— 误杀风险更低。
#   默认 0 = 关闭。注意：tfidf 与 bge-m3 的相似度基线完全不同，切后端后必须重新标定再开！
SOFT_REFUSE_THRESHOLD = float(os.environ.get("SOFT_REFUSE_THRESHOLD") or "0")
# CACHE_MAX_VECS：L1 最多保留多少条问句向量（按 hit_count 保留，控制 answer_cache.json 体积）
CACHE_MAX_VECS      = int(os.environ.get("CACHE_MAX_VECS") or "300")
# 单条 L1 向量的维度上限（超过则不存，防止 tfidf 超大词表把缓存文件撑爆）
CACHE_MAX_DIM       = int(os.environ.get("CACHE_MAX_DIM") or "2048")


def _embed_via_api(texts: list[str], model: str | None = None) -> np.ndarray:
    """OpenAI 兼容 /v1/embeddings → L2 归一化 (N, D) float32。

    与 TF-IDF 向量保持同一约定（行 L2 归一化），因此 query 里的
    `vectors @ q_vec` 仍然等价于余弦相似度，检索链路无需改动。
    失败即抛异常，由调用方决定回退（store 会回退 tfidf，保证服务可用）。
    """
    if not EMBED_API_KEY:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY，无法使用 API embedding")
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    use_model = (model or EMBED_MODEL).strip()
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [(t.strip() or " ") for t in texts[i:i + EMBED_BATCH]]
        resp = requests.post(
            EMBED_API_URL,
            headers={"Authorization": f"Bearer {EMBED_API_KEY}", "Content-Type": "application/json"},
            json={"model": use_model, "input": batch, "encoding_format": "float"},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"embedding API {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        out.extend([d["embedding"] for d in items])
    arr = np.asarray(out, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] != len(texts):
        raise RuntimeError(f"embedding 返回形状异常: {getattr(arr, 'shape', None)} vs 输入 {len(texts)}")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    return arr / norms


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
        self.embed_backend: str = "tfidf"           # 磁盘向量所用后端（_load 依磁盘标记覆盖）
        self.embed_model: str = EMBED_MODEL

        self._load()
        self._fp_cache = None   # 内容指纹缓存（惰性计算，KB 变更时置空）

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

    @property
    def fingerprint(self) -> str:
        """知识库内容指纹 —— 任一切片新增/修改/删除后都会变。

        AnswerCache 用它判断快照是否过期（不匹配 → 该快照作废），
        避免改了源文档后仍把旧答案当"高频缓存"返回。
        只摘要 id + text，与 created_at / file_size 等元信息无关，
        因此补字段脚本（upgrade_metadata.py）不会误伤缓存。
        惰性计算 + 内存缓存，_rebuild_all() 时置空重算。
        """
        if self._fp_cache is None:
            h = hashlib.sha256()
            for md in self.metadata:
                h.update(str(md.get("id", "")).encode("utf-8", "ignore"))
                h.update(b"\x00")
                h.update(str(md.get("text", "")).encode("utf-8", "ignore"))
                h.update(b"\x01")
            self._fp_cache = h.hexdigest()[:16]
        return self._fp_cache

    def _load(self):
        """从磁盘加载数据"""
        try:
            if self._meta_file.exists():
                with open(self._meta_file, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
            if self._vocab_file.exists():
                with open(self._vocab_file, "r", encoding="utf-8") as f:
                    self.vocab = json.load(f)
            # V4.1: 记录磁盘向量所用后端（老 vocab.json 无 _backend 键 → 视为 tfidf）
            if isinstance(self.vocab, dict):
                self.embed_backend = (self.vocab.get("_backend") or "tfidf").strip().lower()
                self.embed_model = (self.vocab.get("_model") or EMBED_MODEL).strip()
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
        """大规模变更后重建向量 —— 按当前 embed_backend 分发"""
        self._fp_cache = None   # KB 内容已变 → 指纹失效，下次访问重算
        if not self.metadata:
            self.vocab = {}
            self.vectors = None
            return

        if self.embed_backend == "siliconflow":
            print(f"[vector-store] 重建向量(API) · {len(self.metadata)} docs · model={self.embed_model}")
            self.vectors = _embed_via_api([md.get("text", "") for md in self.metadata], model=self.embed_model)
            self.vocab = {
                "_backend": "siliconflow",
                "_model": self.embed_model,
                "_dim": int(self.vectors.shape[1]) if self.vectors.size else 0,
                "_df": [],
                "_n_docs": len(self.metadata),
            }
            self._save()
            return

        print(f"[vector-store] 重建词表 + 重算向量 · {len(self.metadata)} docs")
        self.vocab = self._build_vocab_from_scratch()
        self.vocab["_backend"] = "tfidf"
        self.vocab["_model"] = ""
        self.vectors = self._compute_tfidf([md.get("text", "") for md in self.metadata])
        self._save()

    def set_backend_and_rebuild(self, backend: str, model: str | None = None) -> dict:
        """切换 embedding 后端并全量重建（原子：失败不改内存状态、不落盘）"""
        backend = (backend or "tfidf").strip().lower()
        if backend not in ("tfidf", "siliconflow"):
            raise ValueError(f"不支持的 EMBED_BACKEND: {backend!r}")
        # 快照（_rebuild_all 是重新绑定而非原地改，持有旧引用即可回滚）
        snap = (self.embed_backend, self.embed_model, self.vocab, self.vectors, self._fp_cache)
        self.embed_backend = backend
        if model:
            self.embed_model = model.strip()
        try:
            self._rebuild_all()
        except Exception:
            # 失败回滚：磁盘未被触碰（_save 在赋值之后），内存恢复原状
            (self.embed_backend, self.embed_model,
             self.vocab, self.vectors, self._fp_cache) = snap
            raise
        return self.embed_info()

    def embed_info(self) -> dict:
        """当前 embedding 后端信息（/embed/info 与 /health 用）"""
        dim = int(self.vectors.shape[1]) if (self.vectors is not None and getattr(self.vectors, "size", 0)) else 0
        return {
            "backend": self.embed_backend,
            "model": self.embed_model if self.embed_backend != "tfidf" else "TF-IDF + Jieba (自实现)",
            "dim": dim,
            "docs": len(self.metadata),
            "api_key_present": bool(EMBED_API_KEY),
        }

    def _compute_tfidf(self, texts: list[str]) -> np.ndarray:
        """批量 TF-IDF 向量化（原实现，未改动）"""
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

    def _compute_vectors(self, texts: list[str]) -> np.ndarray:
        """统一向量入口 —— 按当前 embed_backend 分发。

        保持旧方法名不变，`_rebuild_all` / `query` 无需改动。
        """
        if self.embed_backend == "siliconflow":
            return _embed_via_api(texts, model=self.embed_model)
        return self._compute_tfidf(texts)

    # ── API 方法（对齐 ChromaDB Collection）──
    def count(self) -> int:
        return len(self.metadata)

    def add(self, documents: list[str], ids: list[str], metadatas: list[dict] | None = None):
        """添加文档"""
        if not documents:
            return
        if metadatas is None:
            metadatas = [{} for _ in documents]

        # 追加 metadata（V4 增强：created_at / file_type / file_size）
        for i, doc_id in enumerate(ids):
            created_at = metadatas[i].get("created_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            source_val = metadatas[i].get("source", "unknown")
            file_type = metadatas[i].get("file_type") or (
                os.path.splitext(source_val)[1].lower() or ".inline"
            )
            self.metadata.append({
                "id": doc_id,
                "text": documents[i],
                "source": source_val,
                "strategy": metadatas[i].get("strategy", "default"),
                "created_at": created_at,
                "file_type": file_type,
                "file_size": len(documents[i]),
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
            ret["metadatas"] = [{
                "source": md.get("source", "unknown"),
                "strategy": md.get("strategy", "default"),
                "created_at": md.get("created_at"),
                "file_type": md.get("file_type"),
                "file_size": md.get("file_size"),
            } for md in results]
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
        use_rerank: bool = True,
        query_vecs=None,          # V5-M2：外部已算好的问句向量（透传复用，避免同一次查询 embed 两次）
    ) -> dict:
        """混合检索 —— 向量 + BM25 + RRF 融合 + cosine 重排（V4 增强）"""
        if not self.metadata or self.vectors is None or len(self.vectors) == 0:
            empty = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
            return empty

        query = query_texts[0] if query_texts else ""
        results_per_query = []

        for qi, q_text in enumerate(query_texts):
            q_text = q_text or ""
            n = min(n_results, len(self.metadata))

            # ── 1. 向量检索（余弦相似度）──
            #    API embedding 后端可能因网络/配额失败 → 本次查询退化为 BM25-only，不抛 500
            q_vec = None
            if query_vecs is not None and qi == 0:
                q_vec = query_vecs          # ★ V5-M2：复用调用方已算好的向量（省一次 embedding 调用）
            else:
                try:
                    q_vec = self._compute_vectors([q_text])[0]  # (dim,)
                except Exception as e:
                    print(f"[vector-store] ⚠️ query embedding 失败 → 本查询退化 BM25-only: {e}")
            vec_ok = (q_vec is not None) and np.linalg.norm(q_vec) > 0
            if not vec_ok:
                # query 无有效向量 → 全零相似度，交给 BM25 排序
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

            # ── 4. V4 新增：cosine 重排（RRF 结果 → query 向量点积重排 → 取 top n）──
            #    vec_ok=False（embedding 失败）时跳过重排，沿用 RRF 顺序
            if use_rerank and vec_ok:
                rerank_pool = sorted(fused_ranks.keys(), key=lambda i: -fused_ranks[i])[:pool_size]
                rerank_scores = []
                for doc_idx in rerank_pool:
                    chunk_vec = self.vectors[doc_idx]
                    norm = np.linalg.norm(chunk_vec)
                    if norm > 0:
                        score = float(chunk_vec @ q_vec / norm)
                    else:
                        score = 0.0
                    rerank_scores.append((doc_idx, score))
                rerank_scores.sort(key=lambda x: -x[1])
                final_idx = [i for i, _ in rerank_scores[:n]]
            else:
                final_idx = sorted(fused_ranks.keys(), key=lambda i: -fused_ranks[i])[:n]

            # 构造返回
            ids = [self.metadata[i]["id"] for i in final_idx]
            docs = [self.metadata[i]["text"] for i in final_idx]
            metas = [{
                "source": self.metadata[i].get("source", "unknown"),
                "strategy": self.metadata[i].get("strategy", "default"),
                "created_at": self.metadata[i].get("created_at"),
                "file_type": self.metadata[i].get("file_type"),
                "file_size": self.metadata[i].get("file_size"),
            } for i in final_idx]
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
def _hybrid_search(query: str, vector_store: SimpleVectorStore, top_k: int = 5,
                   use_bm25: bool = True, use_rerank: bool = True, query_vec=None):
    """混合检索 —— V4 增强：RRF + cosine rerank｜V5-M2：可复用外部问句向量"""
    vector_k = top_k * 3 if use_bm25 else top_k
    results = vector_store.query(
        query_texts=[query],
        n_results=vector_k,
        include=["documents", "metadatas", "distances"],
        use_bm25=use_bm25,
        use_rerank=use_rerank,
        query_vecs=query_vec,
    )
    chunks = results["documents"][0] if results["documents"] else []
    distances = results["distances"][0] if results["distances"] else []
    metadatas = results["metadatas"][0] if results.get("metadatas") else []

    return chunks[:top_k], distances[:top_k], metadatas[:top_k] or [{}] * top_k


# ═══════════════════════════════════════════════════════════════════
# ③-1 V4 新增：AnswerCache 回答快照（高频问题省 100% token）
# ═══════════════════════════════════════════════════════════════════
class AnswerCache:
    """基于 question 哈希的回答快照 · 持久化到 COS 挂载路径"""

    MAX_ENTRIES = 1000          # 上限，超过 LRU 淘汰
    # 命中几次才开始复用快照。默认 1 = 同一个问题第 2 次问就直返缓存（省 token 优先）
    # 设 2 则第 3 次才复用（更保守，避免一次性提问污染缓存）
    PROMOTE_THRESHOLD = int(os.environ.get("CACHE_PROMOTE") or "1")

    def __init__(self, path: str):
        self.path = Path(path) / "answer_cache.json"
        self.data: dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except Exception:
                self.data = {}
        self._migrate_keys()

    def _migrate_keys(self):
        """V5-M2：key 算法升级（strip+lower → 归一化）后把旧 key 迁到新 key。幂等，重复执行无副作用。"""
        changed = False
        for old_k in list(self.data.keys()):
            entry = self.data.get(old_k)
            if not isinstance(entry, dict) or not entry.get("question"):
                continue
            new_k = self._key(entry["question"])
            if new_k == old_k:
                continue
            changed = True
            cur = self.data.pop(old_k)
            exist = self.data.get(new_k)
            # 新旧 key 撞车时保留命中次数更高的那条
            if not exist or cur.get("hit_count", 0) > exist.get("hit_count", 0):
                self.data[new_k] = cur
        if changed:
            self._save()
            print(f"[answer-cache] 🔁 key 归一化迁移完成 · {len(self.data)} 条")

    def _save(self):
        self._prune_vecs()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))

    # 问句归一化：全角→半角 + 去空白/标点 → 「LTC 是什么？」与「LTC 是什么」同 key
    _PUNCT_RE = re.compile(r"[\s\W_]+")

    @classmethod
    def _norm(cls, question: str) -> str:
        s = (question or "").strip().lower()
        s = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in s)
        return cls._PUNCT_RE.sub("", s)

    @classmethod
    def _key(cls, question: str) -> str:
        return hashlib.sha256(cls._norm(question).encode()).hexdigest()[:16]

    # ── V5-M2 L1：问句向量（语义近似缓存）──
    @staticmethod
    def _pack_vec(vec) -> list | None:
        """向量 → JSON 可存格式（float32 + 4 位小数，控体积）；超限或不合法返回 None"""
        try:
            arr = np.asarray(vec, dtype=np.float32).ravel()
        except Exception:
            return None
        if arr.size == 0 or arr.size > CACHE_MAX_DIM:
            return None
        return [round(float(x), 4) for x in arr]

    def _prune_vecs(self):
        """只保留 hit_count 最高的 CACHE_MAX_VECS 条向量，其余置空（控制缓存文件体积）"""
        with_vec = [k for k, v in self.data.items() if v.get("qvec")]
        if len(with_vec) <= CACHE_MAX_VECS:
            return
        with_vec.sort(key=lambda k: -self.data[k].get("hit_count", 0))
        for k in with_vec[CACHE_MAX_VECS:]:
            self.data[k]["qvec"] = None

    def find_similar(self, qvec, kb_fingerprint: str | None = None, threshold: float | None = None) -> tuple:
        """L1 语义近似命中 → (快照 or None, 最高相似度)

        规则对齐 L0：① 阈值 <0.5 视为关闭 ② KB 指纹不符 = 过期，不参与 ③ hit_count 未达高频阈值不复用
        """
        th = CACHE_SIM_THRESHOLD if threshold is None else float(threshold)
        if qvec is None or th < 0.5:
            return None, 0.0
        try:
            qv = np.asarray(qvec, dtype=np.float32).ravel()
            nq = float(np.linalg.norm(qv))
            if nq == 0 or qv.size == 0:
                return None, 0.0
        except Exception:
            return None, 0.0

        best, best_sim = None, 0.0
        for entry in self.data.values():
            v = entry.get("qvec")
            if not v or len(v) != qv.size:
                continue
            if kb_fingerprint is not None and entry.get("kb_fingerprint") != kb_fingerprint:
                continue
            if entry.get("hit_count", 0) < self.PROMOTE_THRESHOLD:
                continue
            try:
                ev = np.asarray(v, dtype=np.float32)
                ne = float(np.linalg.norm(ev))
                if ne == 0:
                    continue
                sim = float(qv @ ev / (nq * ne))
            except Exception:
                continue
            if sim > best_sim:
                best, best_sim = entry, sim

        if best is not None and best_sim >= th:
            best["hit_count"] = best.get("hit_count", 0) + 1
            self._save()
            return best, best_sim
        return None, best_sim

    def get(self, question: str, kb_fingerprint: str | None = None) -> dict | None:
        """取快照。kb_fingerprint 传入时，指纹不匹配的快照直接作废（防返回过期答案）。"""
        k = self._key(question)
        entry = self.data.get(k)
        if not entry:
            return None
        # ★ KB 内容变了 → 该快照已过期：删掉并当未命中，避免把旧答案当"高频缓存"返回
        if kb_fingerprint is not None and entry.get("kb_fingerprint") != kb_fingerprint:
            del self.data[k]
            self._save()
            print(f"[answer-cache] ♻️ 快照失效（KB 指纹变化）: {question[:40]}")
            return None
        entry["hit_count"] = entry.get("hit_count", 0) + 1
        self._save()
        if entry["hit_count"] < self.PROMOTE_THRESHOLD:
            return None  # 还没高频到值得返回缓存
        return entry

    def set(self, question: str, answer: str, sources: list, llm_used: str,
            kb_fingerprint: str = "", qvec=None):
        k = self._key(question)
        packed = self._pack_vec(qvec)
        if k not in self.data:
            self.data[k] = {
                "question": question,
                "answer": answer,
                "sources": sources,
                "llm_used": llm_used,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "hit_count": 0,
                "kb_fingerprint": kb_fingerprint,
                "qvec": packed,          # V5-M2 L1：问句向量（None = 未启用/超限）
                "qvec_dim": len(packed) if packed else 0,
            }
        elif packed is not None:
            # 已有快照但没向量（如 L1 当时未启用）→ 补上，让存量快照也能参与近似命中
            self.data[k]["qvec"] = packed
            self.data[k]["qvec_dim"] = len(packed)
        # LRU 淘汰（保留 hit_count 最高的）
        if len(self.data) > self.MAX_ENTRIES:
            sorted_keys = sorted(self.data.keys(), key=lambda h: -self.data[h].get("hit_count", 0))
            for old_k in sorted_keys[self.MAX_ENTRIES:]:
                del self.data[old_k]
        self._save()

    def stats(self, kb_fingerprint: str | None = None) -> dict:
        total = len(self.data)
        hot = sum(1 for v in self.data.values() if v.get("hit_count", 0) >= self.PROMOTE_THRESHOLD)
        out = {
            "total_snapshots": total,
            "hot_count": hot,
            "max_entries": self.MAX_ENTRIES,
            "vec_count": sum(1 for v in self.data.values() if v.get("qvec")),
            "vec_max": CACHE_MAX_VECS,
            "sim_threshold": CACHE_SIM_THRESHOLD,
        }
        if kb_fingerprint is not None:
            out["stale_count"] = sum(
                1 for v in self.data.values() if v.get("kb_fingerprint") != kb_fingerprint
            )
        return out

    def delete(self, question: str) -> bool:
        k = self._key(question)
        if k in self.data:
            del self.data[k]
            self._save()
            return True
        return False


# ═══════════════════════════════════════════════════════════════════
# ③-2 V5-M2：Token 省算埋点（按天统计 · 落盘到 COS 挂载路径）
# ═══════════════════════════════════════════════════════════════════
def _est_tokens(text: str) -> int:
    """粗估 token 数（中英混合 ~1.6 字符/token）。仅用于省算统计，非计费口径。"""
    if not text:
        return 0
    return max(1, int(len(text) / 1.6))


class TokenEconomy:
    """省算四层漏斗埋点：L0 精确缓存 → L1 语义近似 → L2 拒答短路 → L3 完整 RAG"""

    FIELDS = ("queries_total", "l0_hits", "l1_hits", "l2_short", "llm_calls",
              "llm_fallback", "embed_calls", "tokens_in", "tokens_out")
    WRITE_EVERY = 10          # 每 N 次事件落一次盘（COS 挂载，避免高频写）
    KEEP_DAYS = 30

    def __init__(self, path: str):
        self.path = Path(path) / "token_economy.json"
        self.days: dict[str, dict] = {}
        self._dirty = 0
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.days = json.loads(self.path.read_text())
            except Exception:
                self.days = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for d in sorted(self.days.keys())[:-self.KEEP_DAYS]:
            self.days.pop(d, None)
        self.path.write_text(json.dumps(self.days, ensure_ascii=False, indent=2))

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    def bump(self, field: str, n: int = 1):
        day = self.days.setdefault(self._today(), {f: 0 for f in self.FIELDS})
        day[field] = day.get(field, 0) + n
        self._dirty += 1
        if self._dirty >= self.WRITE_EVERY:
            self._save()
            self._dirty = 0

    def flush(self):
        self._save()
        self._dirty = 0

    def summary(self, days: int = 7) -> dict:
        """近 N 天聚合 —— saved_tokens_est 为估算值（按本次统计的平均单次 LLM token 折算）"""
        self.flush()
        picked = sorted(self.days.keys())[-days:] or [self._today()]
        total = {f: 0 for f in self.FIELDS}
        for d in picked:
            day_data = self.days.get(d) or {}
            for f in self.FIELDS:
                total[f] += day_data.get(f, 0)
        q = total["queries_total"] or 1
        l3 = max(0, total["queries_total"] - total["l0_hits"] - total["l1_hits"] - total["l2_short"])
        avg_llm = (total["tokens_in"] + total["tokens_out"]) / max(1, total["llm_calls"])
        saved = (total["l0_hits"] + total["l1_hits"] + total["l2_short"]) * avg_llm
        return {
            "range_days": len(picked),
            "days": picked,
            **total,
            "l3_full_rag": l3,
            "avg_tokens_per_llm": round(avg_llm, 1),
            "saved_tokens_est": round(saved),
            "cache_hit_rate": round((total["l0_hits"] + total["l1_hits"]) / q, 4),
            "llm_call_rate": round(total["llm_calls"] / q, 4),
            "thresholds": {
                "cache_sim": CACHE_SIM_THRESHOLD,
                "reject": REJECT_THRESHOLD,
                "l1_enabled": _l1_enabled(),
                "l2_enabled": bool(REJECT_THRESHOLD > 0),
            },
        }

    def top_scores(self) -> dict:
        """L2 阈值标定辅助（暂未采集分布，保留扩展位）"""
        return {"note": "top1 分值分布见 /query 响应 top1_score 字段"}


# ═══════════════════════════════════════════════════════════════════
# ③ SimpleVectorStore 初始化
# ═══════════════════════════════════════════════════════════════════
_default = str(Path(__file__).parent / "chroma_data")
CHROMA_PATH = os.environ.get("CHROMA_PATH") or ("/mnt/chroma" if os.path.isdir("/mnt/chroma") else _default)
VECTOR_STORE = SimpleVectorStore(path=CHROMA_PATH)
print(f"[init] ✅ SimpleVectorStore ready · path={CHROMA_PATH} · docs={VECTOR_STORE.count()}")

# V4.1: embedding 后端解析优先级 —— 显式 env 覆盖 > 磁盘持久化标记 > tfidf
#   ① env EMBED_BACKEND 明确设置 → 强制切换（声明式，可用来一键强制回退）
#   ② 未设置 → 沿用磁盘标记（保证 /embed/rebuild 的切换在容器重启后依然生效）
if EMBED_BACKEND_OVERRIDE and (VECTOR_STORE.embed_backend or "tfidf") != EMBED_BACKEND_OVERRIDE:
    try:
        _info = VECTOR_STORE.set_backend_and_rebuild(EMBED_BACKEND_OVERRIDE)
        print(f"[init] ✅ embedding 后端（env 覆盖）切换 → {_info}")
    except Exception as _e:
        print(f"[init] ⚠️ 切换到 {EMBED_BACKEND_OVERRIDE} 失败，回退 tfidf: {_e}")
        try:
            VECTOR_STORE.set_backend_and_rebuild("tfidf")
        except Exception as _e2:
            print(f"[init] ❌ tfidf 回退也失败（向量可能不可用）: {_e2}")
print(f"[init] ✅ embedding backend={VECTOR_STORE.embed_backend} · dim={VECTOR_STORE.embed_info()['dim']}")

# V4 新增：AnswerCache 初始化
ANSWER_CACHE = AnswerCache(CHROMA_PATH)
print(f"[init] ✅ AnswerCache ready · snapshots={len(ANSWER_CACHE.data)} · path={ANSWER_CACHE.path}")

TOKEN_ECONOMY = TokenEconomy(CHROMA_PATH)
print(f"[init] ✅ TokenEconomy ready · days={len(TOKEN_ECONOMY.days)} · path={TOKEN_ECONOMY.path}")


def _l1_enabled() -> bool:
    """L1 语义近似是否可用。

    条件：阈值 >= 0.5 且当前是 dense embedding 后端。
    tfidf 是稀疏词表向量，两句话字面不同就几乎正交，做语义近似没有意义（会误命中），故直接关闭。
    """
    return bool(
        CACHE_SIM_THRESHOLD >= 0.5
        and getattr(VECTOR_STORE, "embed_backend", "tfidf") == "siliconflow"
    )


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
    "fabe": """你是专业 B2B 销售话术专家。基于参考内容，按 FABE 法则回答客户问题。

规则：
1. 参考不足 → 直接说「知识库暂未收录相关内容」，不要编造
2. Feature→Advantage→Benefit→Evidence 按 FABE 结构组织，但不要显式标注段落名
3. Benefit 从客户视角（省多少钱/多少时间/降低什么风险）
4. 回答 150-250 字，口语化

客户问题：{question}

参考内容：
{context}

回答：""",
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

# ── Portal 模板渲染：注入 API Key ──
def _render_portal_html() -> HTMLResponse:
    """读取 portal/index.html，注入后端 API Key（生产模式且开启注入时）"""
    portal_path = Path(__file__).parent / "portal" / "index.html"
    if not portal_path.exists():
        return HTMLResponse("Portal index.html 未找到")
    html = portal_path.read_text(encoding="utf-8")
    # 注入 API Key（仅生产模式 + 环境变量开启注入时）
    is_prod = API_KEY != "dev-only-key-change-in-prod"
    inject_enabled = os.environ.get("API_KEY_IN_HTML", "true").lower() in ("true", "1", "yes")
    if is_prod and inject_enabled:
        html = html.replace("{{AUTO_API_KEY}}", API_KEY)
        html = html.replace("{{PORTAL_VERSION}}", "v3-prod")
    else:
        html = html.replace("{{AUTO_API_KEY}}", "")
        html = html.replace("{{PORTAL_VERSION}}", "v3-dev")
    return HTMLResponse(html)


@app.get("/")
async def portal():
    """知识库管理门户 —— 模板渲染 + API Key 注入"""
    return _render_portal_html()


@app.get("/portal")
async def portal_alt():
    """Portal 备用路径 —— 同 /"""
    return _render_portal_html()


@app.get("/health")
def health():
    """健康检查（公开免鉴权）"""
    is_prod = API_KEY != "dev-only-key-change-in-prod"
    _ei = VECTOR_STORE.embed_info()
    return {
        "status": "ok",
        "version": "3.0",
        "docs_count": VECTOR_STORE.count(),
        "embedding": _ei["model"],
        "embed_backend": _ei["backend"],
        "embed_model": _ei["model"],
        "vector_dim": _ei["dim"],
        "api_key_mode": "开发模式(无强制鉴权)" if not is_prod else "生产模式(已开启鉴权)",
        "feishu_configured": bool(FEISHU_APP_ID and FEISHU_APP_SECRET),
        "need_api_key": is_prod,
        "bm25_available": JIEBA_AVAILABLE,
        "api_key_hint": (
            "开发模式：API Key 留空即可（后端自动跳过鉴权）"
            if not is_prod
            else "生产模式：请在 ⚙️ 设置 tab 填入 API Key（后端 API_KEY 环境变量的值）"
        ),
    }

# ── 知识库文档管理 ──

@app.get("/kb")
async def list_knowledge(_: bool = Depends(verify_api_key)):
    """列出所有已入库文档 —— V4 增强：按 file_type 分组 + 完整文件视图"""
    meta = VECTOR_STORE.metadata
    sources = {}       # source -> chunk count
    ids_by_source = {}
    files = {}         # source -> {chunks, file_type, created_at, total_chars}

    for md in meta:
        src = md.get("source", "unknown")
        ft = md.get("file_type", ".inline")
        ca = md.get("created_at")
        sz = md.get("file_size", len(md.get("text", "")))
        sources[src] = sources.get(src, 0) + 1
        ids_by_source.setdefault(src, []).append(md["id"])

        if src not in files:
            files[src] = {
                "source": src,
                "file_type": ft,
                "created_at": ca,
                "chunks": 1,
                "total_chars": sz,
            }
        else:
            f = files[src]
            f["chunks"] += 1
            f["total_chars"] += sz
            # 保持最早的 created_at
            if ca and (not f["created_at"] or ca < f["created_at"]):
                f["created_at"] = ca

    # 按 file_type 分组
    by_type = {}
    for src, info in files.items():
        ft = info["file_type"]
        by_type.setdefault(ft, []).append(info)
    # 每组按 created_at 倒序
    for ft in by_type:
        by_type[ft].sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return {
        "total_chunks": VECTOR_STORE.count(),
        "total_files": len(files),
        "sources": sources,
        "ids_by_source": ids_by_source,
        "files": files,
        "by_type": by_type,
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

# ── V5：文档资产 CRUD（工作台「文档资产」视图）──

def _source_ids(source: str) -> list[str]:
    """返回某 source 下的全部切片 id"""
    return [md["id"] for md in VECTOR_STORE.metadata if md.get("source") == source]


@app.patch("/kb/source/{source:path}")
async def rename_source(source: str, body: dict, _: bool = Depends(verify_api_key)):
    """重命名文档（仅改 source 标签，不重建向量 —— 零成本）

    body: {new_source: str}
    改名后 KB 内容指纹不变（指纹只由 id+text 决定），缓存不会误失效。
    """
    new_source = (body.get("new_source") or "").strip()
    if not new_source:
        raise HTTPException(400, "new_source required")
    if new_source == source:
        return {"ok": True, "renamed": 0, "unchanged": True}

    # 目标名冲突检查
    if _source_ids(new_source):
        raise HTTPException(409, f"目标名称已存在: {new_source}")

    changed = 0
    for md in VECTOR_STORE.metadata:
        if md.get("source") == source:
            md["source"] = new_source
            # 扩展名变了则同步 file_type，保持文档资产口径一致
            try:
                ext = "." + new_source.rsplit(".", 1)[1].lower()
                if ext and len(ext) > 1 and "/" not in ext:
                    md["file_type"] = ext
            except (IndexError, AttributeError):
                pass
            changed += 1

    if changed == 0:
        raise HTTPException(404, f"source not found: {source}")

    VECTOR_STORE._save()
    return {"ok": True, "renamed": changed, "from": source, "to": new_source}


@app.post("/kb/source/{source:path}/replace")
async def replace_source(source: str, body: dict, _: bool = Depends(verify_api_key)):
    """原子替换文档内容：旧切片整体备份 → 删除 → 新内容重新切片入库；任一步失败自动回滚

    body: {text: str, strategy?: str}
    失败时恢复旧切片（含原文与 id），保证知识库不会因替换失败而丢数据。
    """
    text = (body.get("text") or "").strip()
    strategy = body.get("strategy") or "default"
    if not text:
        raise HTTPException(400, "text required")

    old_ids = _source_ids(source)
    if not old_ids:
        raise HTTPException(404, f"source not found: {source}")

    # ① 备份旧切片（含向量需重建，故只存 id/text/metadata）
    backup = [
        {"id": md["id"], "text": md.get("text", ""), "meta": {k: v for k, v in md.items() if k not in ("id", "text")}}
        for md in VECTOR_STORE.metadata if md.get("source") == source
    ]

    try:
        # ② 先在内存里切好（切片失败不会动知识库）
        chunks = _chunk_text(text, strategy=strategy)
        if not chunks:
            raise ValueError("切片结果为空，已取消替换")
        new_ids = [str(uuid.uuid4()) for _ in chunks]
        metadatas = [{"source": source, "strategy": strategy} for _ in chunks]

        # ③ 删旧 → 入新
        VECTOR_STORE.delete(ids=old_ids)
        VECTOR_STORE.add(documents=chunks, ids=new_ids, metadatas=metadatas)
    except Exception as e:
        # ④ 回滚：把备份切片重新写回
        try:
            VECTOR_STORE.delete(ids=[b["id"] for b in backup])
            VECTOR_STORE.add(
                documents=[b["text"] for b in backup],
                ids=[b["id"] for b in backup],
                metadatas=[dict(b["meta"]) for b in backup],
            )
        except Exception as e2:
            raise HTTPException(500, f"替换失败且回滚失败（请检查知识库完整性）: {e} / {e2}")
        raise HTTPException(500, f"替换失败，已回滚: {e}")

    return {
        "ok": True,
        "source": source,
        "removed": len(old_ids),
        "ingested": len(chunks),
        "strategy": strategy,
        "total": VECTOR_STORE.count(),
    }

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

# ── V4 A5：切片筛选动态选项 API ──
@app.get("/chunks/options")
def get_chunk_options():
    """返回当前 metadata 中所有 source 和 strategy 的去重集合 —— 供前端筛选下拉框使用"""
    sources = sorted(set(md.get("source", "unknown") for md in VECTOR_STORE.metadata))
    strategies = sorted(set(md.get("strategy", "default") for md in VECTOR_STORE.metadata))
    return JSONResponse({"sources": sources, "strategies": strategies})

# ── V4 A3：AnswerCache 管理端点 ──
@app.get("/cache")
def list_cache(_: bool = Depends(verify_api_key)):
    """列出所有 answer_cache 快照"""
    return JSONResponse(list(ANSWER_CACHE.data.values()))

@app.get("/cache/stats")
def cache_stats(_: bool = Depends(verify_api_key)):
    """answer_cache 命中率统计（stale_count = 因 KB 变更已失效的快照数）"""
    return JSONResponse(ANSWER_CACHE.stats(VECTOR_STORE.fingerprint))

# ── V5-M2：省算四层漏斗埋点 ──
@app.get("/stats/token-economy")
def token_economy(days: int = Query(7, ge=1, le=90), _: bool = Depends(verify_api_key)):
    """近 N 天省算统计：L0/L1/L2 各命中多少、真实调了几次 LLM、估算省了多少 token"""
    return JSONResponse(TOKEN_ECONOMY.summary(days))


@app.delete("/stats/token-economy")
def reset_token_economy(_: bool = Depends(verify_api_key)):
    """清空埋点（A/B 实测前归零用）"""
    TOKEN_ECONOMY.days.clear()
    TOKEN_ECONOMY.flush()
    return {"ok": True, "cleared": True}


@app.delete("/cache")
def clear_cache(_: bool = Depends(verify_api_key)):
    """清空 answer_cache（全部删除）"""
    ANSWER_CACHE.data.clear()
    ANSWER_CACHE._save()
    return {"ok": True, "cleared": True}

# ── V4.1：Embedding 后端观测 / 切换 ──
@app.get("/embed/info")
def embed_info(_: bool = Depends(verify_api_key)):
    """当前 embedding 后端信息（只读，不触发任何重建）"""
    return JSONResponse(VECTOR_STORE.embed_info())

@app.post("/embed/rebuild")
def embed_rebuild(body: dict | None = None, _: bool = Depends(verify_api_key)):
    """切换 embedding 后端并全量重建向量。

    body: {"backend": "tfidf" | "siliconflow", "model": "BAAI/bge-m3"(可选)}
    省略 backend 时按环境变量 EMBED_BACKEND 重建。
    切换失败自动回退 tfidf（保证服务可用），返回体标注 fell_back。
    """
    body = body or {}
    backend = (body.get("backend") or VECTOR_STORE.embed_backend or "tfidf").strip().lower()
    model = body.get("model")
    try:
        info = VECTOR_STORE.set_backend_and_rebuild(backend, model)
        return JSONResponse({"ok": True, "fell_back": False, **info})
    except Exception as e:
        print(f"[embed] ⚠️ rebuild 到 {backend} 失败，回退 tfidf: {e}")
        info = VECTOR_STORE.set_backend_and_rebuild("tfidf")
        return JSONResponse({"ok": False, "fell_back": True, "error": str(e), **info})

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
            mm = m or {}
            chunks_data.append({
                "id": f"kw_{i}",
                "text": c,
                "text_preview": (c[:200] + "…") if len(c) > 200 else c,
                "length": len(c),
                "source": mm.get("source", "unknown"),
                "strategy": mm.get("strategy", "unknown"),
                "created_at": mm.get("created_at"),
                "file_type": mm.get("file_type"),
                "file_size": mm.get("file_size"),
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
            mm = metas[i] if metas else {}
            chunks_data.append({
                "id": cid,
                "text": text,
                "text_preview": (text[:200] + "…") if len(text) > 200 else text,
                "length": len(text),
                "source": mm.get("source", "unknown"),
                "strategy": mm.get("strategy", "unknown"),
                "created_at": mm.get("created_at"),
                "file_type": mm.get("file_type"),
                "file_size": mm.get("file_size"),
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

def _do_rag(question: str, top_k: int = 5, use_bm25: bool = True, use_rerank: bool = True) -> dict:
    """RAG + LLM FABE 问答 —— V5-M2 省算四层漏斗

        L0 精确缓存（归一化同问）        → 0 token
        L1 语义近似缓存（问句向量余弦）  → 仅 1 次 embedding，不调 LLM
        L2 拒答短路（检索分过低）        → 0 LLM token
        L3 完整 RAG                      → 单次 LLM 调用（瘦身 prompt + top2 上下文）
    """
    q = question.strip()
    if not q:
        raise ValueError("question required")

    kb_fp = VECTOR_STORE.fingerprint
    TOKEN_ECONOMY.bump("queries_total")

    # ── L0：精确快照（归一化后同问）→ 省 100% token ──
    #   传入 KB 指纹：知识库变更后旧快照自动作废，不会把过期答案当缓存返回
    cached = ANSWER_CACHE.get(q, kb_fingerprint=kb_fp)
    if cached:
        TOKEN_ECONOMY.bump("l0_hits")
        return {
            "answer": cached["answer"],
            "sources": cached["sources"],
            "llm_used": "cache:L0",
            "total_docs": VECTOR_STORE.count(),
            "hybrid": {"bm25_available": JIEBA_AVAILABLE, "use_bm25": use_bm25, "use_rerank": use_rerank},
            "cache_hit": True,
            "cache_level": "L0",
            "cache_stats": ANSWER_CACHE.stats(kb_fp),
        }

    # ── L1：语义近似快照（换种问法也算命中）──
    #   只算一次问句向量，命中就直接返回；未命中时该向量还会复用给下面的检索，不浪费
    q_vec = None
    if _l1_enabled():
        try:
            q_vec = VECTOR_STORE._compute_vectors([q])[0]
            TOKEN_ECONOMY.bump("embed_calls")
        except Exception as e:
            print(f"[rag] ⚠️ L1 问句向量失败 → 跳过近似缓存，走常规流程: {e}")
            q_vec = None
        if q_vec is not None:
            near, sim = ANSWER_CACHE.find_similar(q_vec, kb_fingerprint=kb_fp)
            if near:
                TOKEN_ECONOMY.bump("l1_hits")
                print(f"[rag] 🎯 L1 语义近似命中 sim={sim:.4f} · 「{q[:30]}」≈「{near.get('question','')[:30]}」")
                return {
                    "answer": near["answer"],
                    "sources": near["sources"],
                    "llm_used": "cache:L1",
                    "total_docs": VECTOR_STORE.count(),
                    "hybrid": {"bm25_available": JIEBA_AVAILABLE, "use_bm25": use_bm25, "use_rerank": use_rerank},
                    "cache_hit": True,
                    "cache_level": "L1",
                    "cache_sim": round(sim, 4),
                    "cache_from": near.get("question", ""),
                    "cache_stats": ANSWER_CACHE.stats(kb_fp),
                }

    # ★ V4 A2：hybrid_search 现在自动带 cosine rerank（V5-M2：复用 L1 已算好的向量，避免二次 embedding）
    chunks, distances, metadatas_list = _hybrid_search(
        q, VECTOR_STORE, top_k=top_k, use_bm25=use_bm25, use_rerank=use_rerank, query_vec=q_vec
    )

    if not chunks:
        return {
            "answer": "⚠️ 知识库为空，请先上传产品文档。",
            "sources": [],
            "total_docs": 0,
            "hybrid": {"bm25_available": JIEBA_AVAILABLE, "use_bm25": False, "use_rerank": use_rerank},
            "llm_used": "none",
            "cache_hit": False,
            "cache_level": "L3",
        }

    top1_score = round(1 - float(distances[0]), 4) if distances else 0.0

    # ── L2：拒答短路 —— 检索最高分过低说明知识库里大概没有，别浪费 LLM ──
    if REJECT_THRESHOLD > 0 and top1_score < REJECT_THRESHOLD:
        TOKEN_ECONOMY.bump("l2_short")
        return {
            "answer": (
                f"⚠️ 知识库里没有找到与该问题足够相关的内容（最高相关度 {top1_score}"
                f"，低于阈值 {REJECT_THRESHOLD}）。建议换个问法，或先在知识库补充相关文档。"
            ),
            "sources": [{
                "text": chunks[0][:200],
                "score": top1_score,
                "source": (metadatas_list[0] or {}).get("source", "unknown"),
            }],
            "total_docs": VECTOR_STORE.count(),
            "hybrid": {"bm25_available": JIEBA_AVAILABLE, "use_bm25": use_bm25 and JIEBA_AVAILABLE, "use_rerank": use_rerank},
            "llm_used": "none:L2",
            "cache_hit": False,
            "cache_level": "L2",
            "top1_score": top1_score,
        }

    # ★ V4 A4：top3 → top2（rerank 后更精准）+ 每条截断到 300 字 → 省 ~50% input token
    top2 = list(zip(chunks[:2], distances[:2], metadatas_list[:2]))
    context_block = "\n".join(
        f"[{i+1}] 来源 {(m or {}).get('source','?')}: {(c[:300] + '...') if len(c) > 300 else c}"
        for i, (c, d, m) in enumerate(top2)
    )

    # ★ V4 A4：瘦身后的 prompt —— 不再让 LLM 写 4 段标题
    fabe_prompt = _get_prompt("fabe", question=q, context=context_block)

    # ── 软拒答注入：top1 过低时给 LLM 一个明确的「知识库大概率没覆盖」信号 ──
    #   只注入指令不改模板；LLM 仍可自行判断参考内容是否真相关（比 L2 硬短路误杀风险低）
    if SOFT_REFUSE_THRESHOLD > 0 and top1_score < SOFT_REFUSE_THRESHOLD:
        fabe_prompt += (
            f"\n\n（系统提示：本次检索最高相关度仅 {top1_score}，远低于正常水平，"
            f"知识库大概率未覆盖该问题。请严格执行规则 1 和规则 2，"
            f"若参考内容与问题无直接关联必须拒答，严禁用常识或推测补答案。）"
        )

    llm_reply, llm_backend = call_llm(fabe_prompt)

    # ★ V5-M2：埋点 —— 模板兜底不算真 LLM 消耗，单独记
    TOKEN_ECONOMY.bump("llm_calls")
    if llm_backend == "template-fallback":
        TOKEN_ECONOMY.bump("llm_fallback")
    else:
        TOKEN_ECONOMY.bump("tokens_in", _est_tokens(fabe_prompt))
        TOKEN_ECONOMY.bump("tokens_out", _est_tokens(llm_reply))

    answer = llm_reply

    # ★ V4 A3 → V5-M2：写入 answer_cache（首次访问存，但 hit_count=0 → 下次再查才返回）
    #   随快照记下 KB 指纹 + 问句向量，KB 变更后该快照自动作废，问句向量供 L1 近似命中
    ANSWER_CACHE.set(q, llm_reply, [
        {"text": c[:200], "source": (m or {}).get("source", "?"), "score": round(1-d, 4)}
        for c, d, m in top2
    ], llm_backend, kb_fp, qvec=q_vec)

    return {
        "answer": answer,
        "sources": [
            {
                "text": c,
                "score": round(1 - d, 4),
                "source": (m or {}).get("source", "unknown"),
            }
            for c, d, m in top2
        ],
        "llm_used": llm_backend,
        "total_docs": VECTOR_STORE.count(),
        "hybrid": {
            "bm25_available": JIEBA_AVAILABLE,
            "jieba_available": JIEBA_AVAILABLE,
            "use_bm25": use_bm25 and JIEBA_AVAILABLE,
            "use_rerank": use_rerank,
        },
        "cache_hit": False,
        "cache_level": "L3",
        "top1_score": top1_score,
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
        use_rerank=body.get("use_rerank", True),
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
