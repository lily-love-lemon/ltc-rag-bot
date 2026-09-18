---
name: "rag-knowledge-base-skill"
description: "FDE protocol for production RAG knowledge bases on CloudBase free tier. Invoke when building/optimizing RAG KB, vector store + FastAPI + portal, or token-saving RAG."
tags: ["rag", "knowledge-base", "cloudbase", "fastapi", "cosine-rerank", "answer-cache", "token-optimize"]
version: "1.0.0"
---

# FDE Skill · RAG 知识库完整构建协议

> **Full-stack Delivery Engineering (FDE)**: 从 0 到 1 落地生产级 RAG 知识库系统的可执行协议。
> 所有"坑"都已在真实项目中踩过（22 条，见 `_references/pitfall-log.md`）。

---

## 适用场景（触发条件）

用户诉求包含以下任意一项时，**立即启用本 Skill**：

- 搭建 / 优化 / 重构 RAG 知识库系统
- 腾讯云 CloudBase 免费版（0.5核 128MB）上的 AI 应用
- 向量存储 + FastAPI + 管理台 Portal 三件套
- 飞书 / 企微 / 钉钉机器人 + RAG 问答
- 要求"省 token"、"缓存高频问题"、"重排提升检索精度"
- 构建知识切片管理 / 文件管理系统

---

## 四阶段生命周期协议

### Phase 0: 基线固化（**改任何代码之前必须完成**）

**3 项门禁，全勾才允许进入 Phase A：**

1. [ ] `git config user.email` / `user.name` 已设
2. [ ] `tcb cloudrun record list -s <service> -e <envId>` → 记下 DeployId + RunId 写进 rollback.sh
3. [ ] `git tag <baseline-name>` 固化基线

**跳过的坑**: 如果直接开工 → 出问题没法一键回滚（本次 v3 基线 DeployId 015 就是这么固化的）

---

### Phase A: 后端 RAG 架构

#### 标准 metadata schema

所有向量 store 的切片必须包含这些字段：

```json
{
  "id": "uuid",
  "text": "切片内容",
  "source": "product-manual.pdf",
  "strategy": "default",
  "created_at": "2026-09-18T12:00:00Z",
  "file_type": ".pdf",
  "file_size": 12345
}
```

旧数据（没有 created_at 等字段）靠 `.get(field, None)` 向后兼容，**绝不能直接重构 metadata.json**。

#### 检索链路（必须带 cosine rerank）

```
用户问题
  ↓
TF-IDF 向量
  ↓
BM25 (jieba 中文)
  ↓
RRF 融合 (top 15, k=60)
  ↓
★ cosine 点积重排 (top 3)   ← 零依赖 (numpy)，提升召回精度 ~15%
  ↓
LLM 生成回答
```

#### AnswerCache 设计（省 100% LLM token）

| 参数 | 值 | 理由 |
|---|---|---|
| 键 | `sha256(question.strip().lower())[:16]` | 碰撞率低 |
| PROMOTE_THRESHOLD | **2** | 第 3 次访问起返回缓存，平衡新鲜度和省 token |
| MAX_ENTRIES | 1000 | LRU 淘汰 hit_count 最低的 |
| 持久化 | `/mnt/chroma/answer_cache.json` | 和 vectors.npy 同目录（COS 挂载） |

**核心逻辑**:
```python
# 第一次问: 存快照但不返回缓存 → LLM 正常跑
# 第二次问: hit_count++ 存回 → 仍不返回缓存
# 第三次问: hit_count >= PROMOTE_THRESHOLD → 直接返回缓存，省 100% LLM token
```

#### Token 省法（必须全做）

| # | 手法 | 省多少 |
|---|---|---|
| 1 | AnswerCache 高频问题 → 省 LLM 全部 | 100% |
| 2 | context top3 → top2 × 300 字截断 | 50% input |
| 3 | prompt 瘦身（去段落标注 + 字数上限 150-250） | 30% output |
| 4 | answer 返回纯文本（去 Markdown 包裹） | 20% output |

---

### Phase B: Portal 前端

#### 配色规范（0 处紫黑残留）

| 元素 | 色值 | 用途 |
|---|---|---|
| body | `#0b0f19` | 近黑底 |
| header | `#ffffff` + 靛蓝/青渐变细线 | 干净商务 |
| card | `#1f2937` | 卡片容器 |
| 强调 | `#6366f1` | 靛蓝主色 |
| success | `#10b981` | 绿色成功态 |

**替换验收**: `grep -c "#7c3aed\|#8b5cf6\|rgba(124.*purple" portal/index.html` → **必须 = 0**

#### 必须有的 5 个 tab

| Tab | 功能 |
|---|---|
| 💬 问答 | RAG 对话 + 缓存状态 tag |
| 📤 上传 | 文件/文本入库 |
| 🔪 切片管理 | 动态筛选（source / strategy）+ 分页 + 删除 |
| 📁 文件管理 | 按 file_type 分组 + 日期筛选 + 自定义删除/编辑 |
| ⚙️ Prompt 设置 | 模型 / 温度 / 重排开关 |

#### 切片预览长度

后端 `text_preview[:200]` + 前端 CSS **不截断**（`word-break: break-word`）。
**别让 CSS 二次截断** —— 这是本次踩过的坑。

---

### Phase C: 部署 + 冒烟

#### CloudBase 部署命令（必须 --force）

```bash
tcb cloudrun deploy -s <service> -e <envId> --force 2>&1 <<< $'\n'
```

**为什么**: 默认会交互式问 "Enable gray deployment?"，`<<< $'\n'` 自动选 No。

#### 等待策略

```bash
# 等 60s + 轮询
sleep 60
tcb cloudrun record list -s <service> -e <envId>
# 直到 Status=normal 且 Traffic ratio=100
```

#### 13 项冒烟验收清单

见 `_references/verification-checklist.md`。核心几项：

- [ ] `GET /health` → 200
- [ ] `GET /chunks/options` → 返回非空 sources/strategies
- [ ] `GET /cache/stats` → 正常返回 total/hot
- [ ] 连续问同一问题 3 次 → 第 3 次 `cache_hit=true`
- [ ] `grep -c "#7c3aed" portal/index.html` → 0

---

## 回滚协议（rollback.sh）

```bash
./rollback.sh
# = (1) git checkout <baseline-tag>
# + (2) tcb cloudrun rollback --versionName <RunId>
# + (3) sleep 30
```

**本次基线**: DeployId 015, RunId `multi_tenant_1x7PV6ZTf8QEDK`, git tag `v3-20260918-smoke-green`

---

## 参考文档路由

| 想知道 | 去看 |
|---|---|
| 22 条完整踩坑清单 | `_references/pitfall-log.md` |
| 为什么 CloudBase 免费版砍 Chromadb | `_references/cloudbase-free-tier.md` |
| cosine rerank 具体实现 | `_references/rerank-architecture.md` |
| AnswerCache 为什么阈值=2 | `_references/answer-cache-design.md` |
| 标准 metadata 字段 | `_references/metadata-schema.md` |
| 7 种省 token 手法详解 | `_references/token-saving-playbook.md` |
| 13 项冒烟验收 | `_references/verification-checklist.md` |
| Portal 配色 + 交互规范 | `_references/portal-design-system.md` |
| 代码起点 | `_templates/` |
| 可执行脚本 | `_commands/` |

---

## 已知限制（沙箱网络）

- sandbox 里 `tcb cloudrun run list` / `service get` 可能报 `FetchError tcloudbasegateway.com`
- **但 deploy 提交本身走云端鉴权**，不受影响
- URL 获取需通过 `record list` 拿 RunId，或手动从控制台复制

---

## 版本历史

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0.0 | 2026-09-18 | 从 LTC RAG Bot V4 项目沉淀，22 条坑全记录 |
