#!/usr/bin/env python3
"""M2 省 Token 三件套 · 离线单测（不联网、不调真实 LLM / Embedding）

覆盖：
  T1  _norm 归一化：标点/大小写/全角差异 → 同一个缓存 key
  T2  key 迁移：旧算法写入的快照能被搬到新 key
  T3  L0 精确缓存：同问第 2 次直返（PROMOTE_THRESHOLD=1），LLM 调用次数不再增长
  T4  L1 语义近似：换种问法命中已有快照（余弦 >= 阈值），且不打 LLM
  T5  L1 不误命中：语义无关问题不命中
  T6  L2 拒答短路：检索分低于阈值 → 直接拒答，不打 LLM
  T7  埋点：/stats/token-economy 口径正确（l0/l1/l2/llm_calls 各自计数）
  T8  KB 变更 → 快照自动失效（L0/L1 都不再复用旧答案）

运行：
  /Users/lily/.workbuddy/binaries/python/envs/ltc-ragbot/bin/python scripts/test_m2.py
"""
import os
import sys
import shutil
import tempfile
import hashlib

TMP = tempfile.mkdtemp(prefix="m2test-")
os.environ["CHROMA_PATH"] = os.path.join(TMP, "chroma")
os.environ["CACHE_SIM_THRESHOLD"] = "0.90"
os.environ["REJECT_THRESHOLD"] = "0.50"
os.environ["CACHE_PROMOTE"] = "1"
os.environ["EMBED_BACKEND"] = ""          # 不让 init 触发真实 API 重建

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)

import numpy as np
import app as A

PASS, FAIL = 0, 0
FAILED = []


def ok(name):
    global PASS
    PASS += 1
    print(f"  ✅ {name}")


def bad(name, detail=""):
    global FAIL
    FAIL += 1
    FAILED.append(name)
    print(f"  ❌ {name} {detail}")


def check(cond, name, detail=""):
    ok(name) if cond else bad(name, detail)


# ── 假 embedding：8 维关键词特征，语义由关键词决定，完全确定、零网络 ──
# 同义词归到同一维（什么/啥/如何 → 同一特征），否则「LTC是什么」与「LTC是啥」会被编成正交向量
FEATURES = ["ltc", "什么|啥|如何|怎么", "招投标|招标|投标", "流程|阶段|步骤",
            "价格|报价|多少钱", "天气|下雨", "合同", "产品"]
DIM = len(FEATURES)


def fake_embed(texts, model=None):
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        s = (t or "").lower()
        for j, f in enumerate(FEATURES):
            if any(kw in s for kw in f.split("|")):
                out[i, j] = 1.0
    norm = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.where(norm == 0, 1, norm)


A._embed_via_api = fake_embed
A.SimpleVectorStore._compute_vectors = lambda self, texts: fake_embed(texts)
A.VECTOR_STORE.embed_backend = "siliconflow"

# ── 假 LLM：计数，绝不联网 ──
LLM_CALLS = []


def fake_llm(prompt):
    LLM_CALLS.append(prompt)
    return f"[MOCK答案 len={len(prompt)}]", "zhipu:mock"


A.call_llm = fake_llm

print("=" * 62)
print("M2 省 Token 三件套 · 离线单测")
print("=" * 62)

# ── T1 归一化 ──
print("\n[T1] 问句归一化")
k1 = A.AnswerCache._key("LTC 是什么？")
k2 = A.AnswerCache._key("ltc是什么")
k3 = A.AnswerCache._key("ＬＴＣ　是什么！")  # 全角
check(k1 == k2 == k3, "标点/大小写/全角差异归一化为同一 key", f"{k1} / {k2} / {k3}")
check(A.AnswerCache._key("招投标流程") != k1, "不同问题仍是不同 key")

# ── T2 key 迁移 ──
print("\n[T2] 旧 key → 新 key 迁移")
old_style_key = hashlib.sha256("LTC 是什么？".strip().lower().encode()).hexdigest()[:16]
A.ANSWER_CACHE.data[old_style_key] = {
    "question": "LTC 是什么？", "answer": "旧答案", "sources": [],
    "llm_used": "zhipu:old", "hit_count": 5, "kb_fingerprint": "",
}
A.ANSWER_CACHE._migrate_keys()
check(old_style_key not in A.ANSWER_CACHE.data, "旧 key 已移除")
check(k1 in A.ANSWER_CACHE.data and A.ANSWER_CACHE.data[k1]["answer"] == "旧答案", "快照已迁到新 key")
check(A.ANSWER_CACHE.data[k1]["hit_count"] == 5, "hit_count 保留")
A.ANSWER_CACHE.data.clear()
A.ANSWER_CACHE._save()

# ── 灌入知识库 ──
print("\n[准备] 灌入 3 条文档")
A.VECTOR_STORE.add(
    documents=[
        "LTC 是 Lead to Cash 的缩写，指从线索到回款的全流程销售管理方法。",
        "招投标流程包括：招标、投标、开标、评标、定标五个阶段。",
        "产品价格体系分为标准版、专业版、旗舰版三档。",
    ],
    ids=["d1", "d2", "d3"],
    metadatas=[{"source": "ltc.md"}, {"source": "bid.md"}, {"source": "price.md"}],
)
check(A.VECTOR_STORE.count() == 3, f"文档入库 3 条（实际 {A.VECTOR_STORE.count()}）")
KB_FP = A.VECTOR_STORE.fingerprint

# ── T3 L0 精确缓存 ──
print("\n[T3] L0 精确缓存（同问第 2 次直返）")
LLM_CALLS.clear()
r1 = A._do_rag("LTC是什么")
r2 = A._do_rag("LTC 是什么？")
check(r1["cache_level"] == "L3" and r1["llm_used"] == "zhipu:mock", "第 1 次走完整 RAG", r1.get("llm_used"))
check(r2.get("cache_hit") is True and r2["cache_level"] == "L0", "第 2 次命中 L0", str(r2.get("cache_level")))
check(len(LLM_CALLS) == 1, f"LLM 只调了 1 次（实际 {len(LLM_CALLS)}）")
check(r2["llm_used"] == "cache:L0", "llm_used 标记为 cache:L0")

# ── T4 L1 语义近似 ──
print("\n[T4] L1 语义近似命中（换种问法）")
n_before = len(LLM_CALLS)
r3 = A._do_rag("LTC是啥")            # 与「LTC是什么」特征向量相同 → 余弦 1.0
check(r3.get("cache_hit") is True and r3["cache_level"] == "L1", "换种问法命中 L1", str(r3.get("cache_level")))
check(r3.get("cache_sim", 0) >= 0.90, f"相似度达标 {r3.get('cache_sim')}")
check(r3.get("cache_from") == "LTC是什么", f"溯源到原问句：{r3.get('cache_from')}")
check(len(LLM_CALLS) == n_before, "L1 命中不调 LLM")

# ── T5 L1 不误命中 ──
print("\n[T5] L1 不误命中（语义无关）")
n_before = len(LLM_CALLS)
r4 = A._do_rag("招投标流程")
check(r4.get("cache_hit") is not True, "无关问题不复用 LTC 的答案", str(r4.get("cache_level")))
check(len(LLM_CALLS) == n_before + 1, "无关问题正常走 LLM")
check(r4["cache_level"] == "L3", "落在 L3", str(r4.get("cache_level")))

# ── T6 L2 拒答短路 ──
print("\n[T6] L2 拒答短路（检索分过低）")
n_before = len(LLM_CALLS)
r5 = A._do_rag("天气怎么样今天")
check(r5["cache_level"] == "L2", "低分走 L2 短路", str(r5.get("cache_level")))
check(r5["llm_used"] == "none:L2", "llm_used = none:L2")
check(len(LLM_CALLS) == n_before, "L2 短路不调 LLM")
check(r5.get("top1_score", 1) < 0.5, f"top1_score={r5.get('top1_score')} < 0.5")
check("知识库里没有找到" in r5["answer"], "返回拒答文案")

# ── T7 埋点 ──
print("\n[T7] /stats/token-economy 埋点")
s = A.TOKEN_ECONOMY.summary()
print(f"     {s}")
check(s["queries_total"] == 5, f"queries_total=5（实际 {s['queries_total']}）")
check(s["l0_hits"] == 1, f"l0_hits=1（实际 {s['l0_hits']}）")
check(s["l1_hits"] == 1, f"l1_hits=1（实际 {s['l1_hits']}）")
check(s["l2_short"] == 1, f"l2_short=1（实际 {s['l2_short']}）")
check(s["llm_calls"] == 2, f"llm_calls=2（实际 {s['llm_calls']}）")
check(s["l3_full_rag"] == 2, f"l3_full_rag=2（实际 {s['l3_full_rag']}）")
check(s["tokens_in"] > 0 and s["tokens_out"] > 0, "token 计数 > 0")
check(s["saved_tokens_est"] > 0, f"估算省下 {s['saved_tokens_est']} tokens")
check(s["thresholds"]["l1_enabled"] is True and s["thresholds"]["l2_enabled"] is True, "开关状态上报正确")

# ── T8 KB 变更 → 快照失效 ──
print("\n[T8] KB 变更 → 快照自动失效")
A.VECTOR_STORE.add(documents=["LTC 补充：包含线索管理、商机推进、合同回款三个阶段。"],
                   ids=["d4"], metadatas=[{"source": "ltc2.md"}])
new_fp = A.VECTOR_STORE.fingerprint
check(new_fp != KB_FP, "KB 指纹已变化")
n_before = len(LLM_CALLS)
r6 = A._do_rag("LTC是什么")
check(r6.get("cache_hit") is not True, "KB 变更后不再复用旧答案", str(r6.get("cache_level")))
check(len(LLM_CALLS) == n_before + 1, "重新走了一次 LLM")
_k = A.AnswerCache._key("LTC是什么")
check(A.ANSWER_CACHE.data[_k]["kb_fingerprint"] == new_fp, "旧指纹快照已被新答案覆盖")
check(A.ANSWER_CACHE.data[_k]["qvec"] is not None, "新快照带上了问句向量（供后续 L1 复用）")

# ── 收尾 ──
print("\n" + "=" * 62)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
if FAILED:
    print("失败项：" + "、".join(FAILED))
print("=" * 62)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
