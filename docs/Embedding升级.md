# Embedding 升级（TF-IDF → BAAI/bge-m3 API）

> 状态：**已上线（2026-09-18 23:10，DeployId 021）** · 可一键回滚
> 对应 handoff 待办 #5。本文是该项的**唯一真源**（决策依据 / 实现 / 指标 / 回滚）。

---

## 一、结论（先给结论，不做骑墙派）

**采用 SiliconFlow `BAAI/bge-m3` API 稠密向量，替换原自实现 TF-IDF；原 TF-IDF 保留为兜底与回退路径。**

三条理由：
1. **实测更好**：口语化探针 hit 由 **2/6 → 4/6**（可答探针 2/4 → 4/4），相似度区分度从 ~0.10–0.31 提升到 ~0.49–0.63。
2. **零成本零磁盘**：bge-m3 在硅基流动为免费额度；走 API **不下载模型**，磁盘占用为 0（对比本地 sentence-transformers 需 ~500MB+）。
3. **对现网约束更友好**：向量维度 **1983 → 1024**，`vectors.npy` 由 1.64 MB 降到 828 KB，内存更省。

不选本地模型的原因：Lily 设备为 8GB Mac 无 GPU，本地 embedding 需数百 MB 磁盘 + 常驻内存，且 CloudBase 容器仅 2GB 内存。

> ⚠️ **不属于本文范围但必须知道**：KB 未覆盖的话题（如"显卡/GPU"），LLM 仍会**自信作答**（实测 query "没有显卡能不能跑大模型" 返回了通用知识而非拒答）。这是**提示词拒答阈值**问题，建议列入下一轮（见第六节）。

---

## 二、三个候选方案对比

| 维度 | 自实现 TF-IDF（原） | 本地向量模型（bge-small-zh） | **API bge-m3（采用）** |
|---|---|---|---|
| 依赖 | numpy + jieba | + torch/sentence-transformers | 仅 requests |
| 磁盘 | 0 | ~500 MB+ | **0** |
| 内存 | 极低 | 需常驻模型 | **极低** |
| 语义召回 | 弱（词面重合） | 中 | **强** |
| 单次查询延迟 | 极低 | 中（本地推理） | +约 0.3–1s（外部 API） |
| 成本 | 0 | 0（但占本地资源） | **0（免费额度）** |
| 离线可用 | ✅ | ✅ | ❌（失败自动退化 BM25-only） |
| 换模型成本 | — | 高 | **低（改 `EMBED_MODEL` 一次调用切换）** |

---

## 三、实现（最小侵入，默认零回归）

改动集中在 `code/app.py`：

| 位置 | 内容 |
|---|---|
| 配置区 | `EMBED_BACKEND` / `EMBED_MODEL` / `EMBED_API_URL` / `EMBED_API_KEY` / `EMBED_BATCH` |
| `_embed_via_api()` | OpenAI 兼容 `/v1/embeddings` 调用，**行 L2 归一化**（与 TF-IDF 同约定 → `query` 里 `vectors @ q_vec` 仍等价余弦，检索链路零改动） |
| `SimpleVectorStore._compute_tfidf()` | 原 `_compute_vectors` 原样更名 |
| `SimpleVectorStore._compute_vectors()` | 新增**统一分发入口**（tfidf / siliconflow） |
| `_rebuild_all()` | 按后端分支；`vocab.json` 写 `_backend`/`_model` 标记 |
| `set_backend_and_rebuild()` | **原子切换**：失败回滚内存、不落盘 |
| `query()` | embedding 异常 → **本查询退化 BM25-only**，不抛 500 |
| `GET /embed/info` | 只读：当前后端/模型/维度/文档数/key 是否就绪 |
| `POST /embed/rebuild` | 鉴权；`{"backend":"tfidf"\|"siliconflow","model":可选}`；失败自动回退并在响应标注 `fell_back` |
| `/health` | 新增 `embed_backend` / `embed_model`，与 `/embed/info` 同源 |

### 后端解析优先级（关键设计）

```
env EMBED_BACKEND（显式覆盖）  >  磁盘 vocab.json 的 _backend（持久化状态）  >  tfidf（兜底）
```

- **env 未设置** → 沿用磁盘标记 ⇒ `/embed/rebuild` 的切换**跨容器重启生效**，**无需改线上任何环境变量**（尊重「不重新配置已有模块」）。
- **env 显式设置** → 声明式强制该后端（可用于一键强制回退）。
- 老 `vocab.json` 无 `_backend` 键 → 视为 `tfidf`，**向后兼容**。

---

## 四、验收指标（A/B 实测，2026-09-18）

方法：6 条**口语化**探针（刻意与 KB 原文用词不同，检验语义召回），清空 AnswerCache 后各跑一遍，判定 top-2 命中切片是否含该话题关键词。

| 探针 | tfidf | siliconflow |
|---|---|---|
| P1 让模型只依据资料回答 | ❌ | **✅**（检索/来源/上下文） |
| P2 防胡说八道 | ❌ | ❌（KB 无此话题，见下） |
| P3 试用周期 | ❌ | **✅**（周/试用/阶段） |
| P4 无显卡跑大模型 | ❌ | ❌（KB 无此话题，见下） |
| P5 公司文档转问答 | ✅ | ✅ |
| P6 交付文档 | ✅ | ✅ |
| **合计** | **2/6** | **4/6** |

**诚实修正**：`幻觉/显卡/GPU/Ollama/vLLM/POC/拒答` 等词在 KB 中出现 **0 次** —— P2/P4 属「KB 未覆盖的不可答查询」，两后端都 miss 是**正确行为**，不应计为检索失败。
只看**可答**探针（P1/P3/P5/P6）：**tfidf 2/4 → siliconflow 4/4**。

其他量化：
- 相似度分布：tfidf 0.10–0.31（区分度低）；bge-m3 0.49–0.63（区分度明显）
- 重建 207 条耗时：**9.9s**（批量 16/次，共 13 次 API 调用）
- 向量体积：1.64 MB → 828 KB

---

## 五、激活 / 回滚 SOP（各一条命令）

```bash
BASE=https://ltc-rag-bot-315461-10-1450031534.sh.run.tcloudbase.com
KEY=<API_KEY>

# 查当前后端
curl -s -H "X-API-Key: $KEY" $BASE/embed/info

# 切到 bge-m3（API 向量）
curl -s -X POST $BASE/embed/rebuild -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" -d '{"backend":"siliconflow"}'

# 回滚到 TF-IDF（本地重算，不依赖任何外部 API）
curl -s -X POST $BASE/embed/rebuild -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" -d '{"backend":"tfidf"}'

# 换别的向量模型（如 bge-large-zh-v1.5）
curl -s -X POST $BASE/embed/rebuild -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" -d '{"backend":"siliconflow","model":"BAAI/bge-large-zh-v1.5"}'
```

- 切换是**原地生效**（无需重启/重新部署）；重建写入 COS 的 `vectors.npy` + `vocab.json`，**重启后仍保持**。
- 回滚到 tfidf **不需要外部 API**，始终可用。
- 若反悔想强制锁死某后端：设环境变量 `EMBED_BACKEND=tfidf`（env 覆盖优先级最高）。

---

## 六、边界与风险（老张式诚实标注）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| 1 | SiliconFlow API 抖动/配额耗尽 | 查询退化 BM25-only（仍可答，质量下降） | `query()` 已捕获异常并退化；启动重建失败回退 tfidf；日志打 `⚠️ query embedding 失败` |
| 2 | 每次查询多一次外部 API 往返 | 延迟 +约 0.3–1s | 可接受；如需极致延迟可回 tfidf |
| 3 | KB 未覆盖话题仍被自信作答 | **幻觉风险** | 属提示词拒答阈值问题，**建议下一轮**修 `prompts/fabe.txt`：无检索命中/相关度过低时明确拒答 |
| 4 | 免费额度政策变动 | 需重新选型 | 切换成本低（改 `EMBED_MODEL` 一次调用）；本地模型为备选 |
| 5 | 向量维度变化后旧缓存 | — | KB 指纹只摘要 `id+text`，与向量维度无关，AnswerCache 不受影响（已验证 `stale_count` 正常） |

---

## 七、验证记录

| 时间 | 版本 | 验证 |
|---|---|---|
| 2026-09-18 22:58 | 020 | 部署可插拔骨架（默认 tfidf，零回归）；smoke 16/16→19/19 · 17/17→20/20 |
| 2026-09-18 23:10 | 021 | 激活 bge-m3：`/health` `embed_backend=siliconflow` `dim=1024`；`/embed/info` OK；真实问答 `llm_used=zhipu:glm-4-flash`；`/kb` 207 条 + by_type 正常；无 key → 401；`/embed/rebuild` 幂等 |

*变更记录：2026-09-18 首版（六六🎋），基于线上 021 实测与 6 探针 A/B 数据。*
