# LTC RAG Bot 项目交付文档

> **交接时间**: 2026-09-19（基于生产版本 **026** 刷新，替代 018 基线）  
> **接手人**: WorkBuddy  
> **上线版本**: 生产版本 **026**（2026-09-19 · V5 深空 Portal + M2 省 Token 漏斗 + 拒答双层 + 显卡/算力 KB 文档）。**完整现状见同包 `handoff.md` 与 `docs/` 交付文档**
> **项目类型**: FastAPI + RAG + CloudBase + Portal 三件套

---

## 一、快速入口（30 秒看懂项目是什么）

LTC RAG Bot 是一个知识库问答系统，给内部 7 份 md 文档（**215 条切片**）做语义检索 + LLM 问答。用户通过 Web Portal（V5 深空指挥台）或飞书机器人提问，系统经过 **省 Token 四层漏斗（L0/L1/L2/L3）+ 向量召回 + BM25 + RRF 融合 → cosine 重排 → LLM 生成** 回答。检索 Embedding 已切 **BAAI/bge-m3**（SiliconFlow API，1024 维），支持可插拔后端（tfidf 兜底）。

---

## 二、所有关键链接

| 资源 | URL |
|---|---|
| **GitHub 仓库** | https://github.com/lily-love-lemon/ltc-rag-bot（clone 502，本地无 remote） |
| **CloudBase 在线 Portal** | https://ltc-rag-bot-315461-10-1450031534.sh.run.tcloudbase.com |
| **CloudBase 控制台** | https://tcb.cloud.tencent.com/dev?envId=lily0625ai-d1gpwc1vw89141edd |
| **COS Bucket** | `6c69-lily0625ai-d1gpwc1vw89141edd-1450031534`（cos.ap-shanghai.myqcloud.com） |
| **FDE Skill v1.0.0** | `<项目根>/.agents/skills/rag-knowledge-base-skill/` |

---

## 三、环境配置

```
CloudBase Env ID:  lily0625ai-d1gpwc1vw89141edd
Service Name:      ltc-rag-bot
规格:              2 核 2 GB（已升级，非免费版）
COS Mount:         /mnt/chroma → COS bucket 根目录
Embedding:        BAAI/bge-m3 via SiliconFlow API（dim=1024，可插拔）
LLM:              Zhipu glm-4-flash
```

**API Keys**（存于 CloudBase 控制台环境变量，本地复制到 `code/.env`，**均已排除出交付包**）:

```
API_KEY=<API_KEY>
ZHIPUAI_API_KEY=<ZHIPUAI_API_KEY>
ZHIPUAI_MODEL=glm-4-flash
FEISHU_APP_ID=cli_aab2169e42badbea
FEISHU_APP_SECRET=<FEISHU_APP_SECRET>
EMBED_BACKEND=siliconflow
EMBED_MODEL=BAAI/bge-m3
EMBED_API_URL=https://api.siliconflow.cn/v1/embeddings
SOFT_REFUSE_THRESHOLD=0.45
CHROMA_PATH=/mnt/chroma
```

---

## 四、系统架构图

```
┌────────────────────────────────────────────────────────────┐
│                       客户端层                              │
│  ┌──────────────┐   ┌───────────────┐   ┌───────────────┐  │
│  │ Web Portal   │   │  飞书机器人    │   │  curl / API   │  │
│  │  (V5 深空)   │   │   (webhook)   │   │ /health / /kb │  │
│  └──────┬───────┘   └───────┬───────┘   └───────┬───────┘  │
└─────────┼───────────────────┼───────────────────┼──────────┘
          │                   │                   │
          ▼                   ▼                   ▼
┌────────────────────────────────────────────────────────────┐
│  CloudBase · FastAPI :8080 (app.py 2073 行)                │
│                                                            │
│  ┌────────────── RAG 问答链路（含省 Token 漏斗）─────────┐ │
│  │ 0. AnswerCache L0 精确缓存：归一化同问直返（0 token）   │ │
│  │ 1. AnswerCache L1 语义近似：cos≥0.92 且 hit≥2 直返     │ │
│  │ 2. L2 拒答短路：top1<REJECT_THRESHOLD(=0关闭)→拒答     │ │
│  │ 3. 向量召回 top15（bge-m3 / TF-IDF 可插拔）           │ │
│  │ 4. BM25 + Jieba 关键词召回 top15                      │ │
│  │ 5. RRF 融合 k=60 → top5                              │ │
│  │ 6. ★ cosine 点积重排 → top3（纯 numpy, 零依赖）       │ │
│  │ 7. context = top2 × 300 字截断                       │ │
│  │ 8. LLM (glm-4-flash) + fabe.txt 接地契约            │ │
│  │    （含 SOFT_REFUSE_THRESHOLD=0.45 软拒答注入）        │ │
│  │ 9. AnswerCache.store + TokenEconomy 埋点             │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                            │
│  ┌───────── SimpleVectorStore (自实现, numpy+JSON) ──────┐ │
│  │  path = /mnt/chroma  (COS 挂载)                      │ │
│  │  ├─ metadata.json    (215 条, 含 created_at 等字段)   │ │
│  │  ├─ vectors.npy      (bge-m3 1024 维, 828KB)         │ │
│  │  ├─ vocab.json       (TF-IDF 词表 36KB, tfidf后端)    │ │
│  │  └─ answer_cache.json (L0/L1 缓存快照 + 指纹)         │ │
│  └─────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
          ▲                   ▲
          │ COS Mount         │ COS Mount
          ▼                   ▼
┌────────────────────────────────────────────────────────────┐
│  COS Bucket: 6c69-lily0625ai-d1gpwc1vw89141edd-1450031534  │
└────────────────────────────────────────────────────────────┘
```

### 检索链路流程图（含省 Token 漏斗）

```
    用户问题
        │
   ┌────┴────┐
 L0 精确命中? ├─ 是 → 直返（0 token）★省
   │ 否       │
 L1 语义近似? ├─ 是(cos≥0.92,hit≥2) → 直返（~404 token 省）★省
   │ 否       │
 L2 top1<阈值?├─ 是(REJECT>0) → 拒答不调 LLM ★省
   │ 否       │
   ▼         ▼
 向量召回    BM25+Jieba
 top 15      top 15
   │         │
   └────┬────┘
        ▼
    RRF 融合 Σ 1/(60+rank) → top 5
        │
        ▼
   ★ cosine 重排 → top 3
        │
        ▼
  top2 × 300 字
        │
        ▼
  LLM (glm-4-flash) + fabe.txt 接地契约
```

---

## 五、代码文件清单

```
ltc-rag-bot/
├── app.py                   后端核心 (2073 行, FastAPI 单文件)
├── Dockerfile               CloudBase 容器定义
├── requirements.txt         Python 依赖 (6 个)
├── rollback.sh              一键回滚脚本 (v2: 无效目标中止 + 自覆盖保护)
├── deploy.sh               部署脚本 (v2: -s <svc> -e <env> --force)
├── cloudbaserc.json         CloudBase 配置 (envParams={}, 不覆盖控制台变量)
├── render.yaml             渲染配置
├── .env.example            环境变量模板（不含真实密钥）
│
├── portal/
│   └── index.html          Portal 前端 (V5 深空指挥台 + 文档全 CRUD)
│
├── configs/kb_strategies.json  切片策略
├── prompts/fabe.txt         RAG prompt（接地契约：只准用参考内容 + 主题沾边即拒答）
├── scripts/                 smoke_local.sh / deploy_drill.sh / upgrade_metadata.py
├── docs/                   18 份深度文档（架构/坑/缓存/重排/7 份交付文档/巡检报告…）
├── kb_docs/                本地 KB 源（显卡与算力需求说明.md 等；线上 KB 在 COS）
│
└── .agents/skills/
    └── rag-knowledge-base-skill/   FDE Skill v1.0.0 (独立可复用)
        ├── SKILL.md                 主入口 · 4 阶段协议
        ├── _references/ (8 份)      pitfall-log / 架构 / 设计
        ├── _commands/ (3 份)        init-baseline / deploy / smoke-test
        ├── _templates/              Dockerfile + requirements
        └── README.md / install.sh
```

---

## 六、API 端点（全部可用）

| 方法 | 端点 | 功能 | 鉴权 |
|---|---|---|---|
| GET | `/health` | 健康检查 + 版本 + docs_count + bm25_available + embed_backend | ❌ 免 |
| GET | `/kb` | 知识库概览 (chunks + files + by_type) | ✅ |
| GET | `/chunks` | 切片列表 (分页 + 筛选) | ✅ |
| GET | `/chunks/{id}` | 单切片 | ✅ |
| PATCH | `/chunks/{id}` | 编辑切片文本/source | ✅ |
| DELETE | `/chunks/{id}` | 删除切片 | ✅ |
| GET | `/chunks/options` | 动态筛选选项 (sources / strategies) | ✅ |
| POST | `/query` | RAG 问答 (cache_hit / token 信息返回) | 可选 |
| POST | `/ingest-text` | 粘贴文本入库 | ✅ |
| POST | `/ingest` | 文件上传入库 | ✅ |
| GET | `/embed/info` | 当前 Embedding 后端 / 维度 / 重建状态 | ✅ |
| POST | `/embed/rebuild` | 切换 Embedding 后端并原子重建（tfidf / siliconflow） | ✅ |
| PATCH | `/kb/source/{id}` | 重命名知识源（不重建向量） | ✅ |
| POST | `/kb/source/{id}/replace` | 原子替换知识源（备份→删旧→重切片，失败回滚） | ✅ |
| GET | `/cache` | AnswerCache 快照列表 | ✅ |
| GET | `/cache/stats` | 缓存命中率 + stale_count 统计 | ✅ |
| DELETE | `/cache` | 清空缓存（**改阈值/换后端后必清**） | ✅ |
| GET/DELETE | `/stats/token-economy` | M2 省 Token 埋点（按天聚合 / 清零） | ✅ |

---

## 七、Git 部署里程碑

| 版本 | 含义 | CloudBase |
|---|---|---|
| `v3-20260918-smoke-green` | 基线冒烟全绿 | DeployId 015 |
| `v4-deployed` | V4 功能上线 | DeployId 016 |
| `v4.1-purple-hero` | 紫色 hero 配色 | DeployId 017 → **018** |
| `opt-…-deploy-live-019` | 连接器首部署 | DeployId 019 |
| `opt-…-embed-pluggable` | 可插拔 Embedding | DeployId 020 |
| `opt-…-embed-live` | 激活 bge-m3 | DeployId 021 |
| `opt-…-portal-v5` | Portal V5 深空指挥台 | DeployId 022 |
| `opt-…-m2-token-economy` | M2 省 Token 三件套 | DeployId 023 → 024 |
| `opt-…-refuse` | 拒答双层 + GPU KB | DeployId 025 → **026（当前在线）** |
| `rag-knowledge-base-skill-v1.0.0` | FDE Skill 初版 | — |

> 注：本地仓库为扁平结构，仅含 `handoff-20260918` 系列 tag + 上述 `opt-*` tag；飞书文档提到的 `v3/v4` tag 为 GitHub 侧原始 tag，本地无 remote。

---

## 八、数据现状（线上实测 026）

```
COS Bucket 内容（215 条 / 7 文件）:
├── metadata.json     215 条切片（含 created_at/file_type/file_size，09-18 升级）
├── vectors.npy       828KB · bge-m3 向量 (215 × 1024)
├── vocab.json        36KB · TF-IDF 词表（仅 tfidf 后端用）
└── xiaoliu-identity/ 原始 .md 文件 7 份（6 份原 + 显卡与算力需求说明.md）
```

**切片来源**：主文件 200 + 显卡文档 8 + inline 3 + diag/test/ok/smoke 各 1 = **215 条**。
**✅ 已完成**：旧 metadata 补字段（09-18）、`chroma.sqlite3` 删除（09-18）、Embedding 升级到 bge-m3（021）。

---

## 九、当前在线数据状态（冒烟实测 026）

```
✅ 切片总数: 215
✅ 文件数: 7
✅ BM25 检索: 启用 (Jieba)；Embedding: bge-m3 (siliconflow, 1024 维)
✅ AnswerCache: L0/L1/L2 + /cache/stats(stale_count 可观测)
✅ 拒答双层: 红烧肉(库外)→拒答(0.3991<0.45)，MVP(库内)→正常(0.5198)
✅ GPU 题根治: "没有显卡能不能跑大模型？"→正确引用显卡文档(0.7746)，不再编造
✅ /stats/token-economy: 返回真实埋点（queries/l0_hits/saved_tokens_est…）
✅ Portal 配色: 紫色 hero #1e40af→#7c3aed, 靛蓝残留 0
✅ 本地冒烟: 开发 22/22 · 生产 17/17 · 部署演练 15/15
```

---

## 十、本地启动

```bash
# 1. clone + 环境（GitHub clone 当前 502，可用本地交付包解包）
cd ltc-rag-bot
cp .env.example .env  # 填入 API Keys（或用 CloudBase 控制台值）
pip install -r requirements.txt

# 2. 启动（本地数据在 chroma_data/）
python app.py

# 3. 看线上数据（经 cloudbase 连接器 queryStorage download，非 tcb CLI）
#    metadata.json / vectors.npy / vocab.json → 放到 chroma_data/ 后启动
```

---

## 十一、部署 / 回滚

> ⚠️ **部署走 cloudbase 连接器 `manageCloudRun.deploy`**（source 构建，不传 serverConfig → Read-Merge-Write 保留远程 Cpu/Mem/EnvParams/VolumesConf，零降级）。`tcb` CLI 未登录(502)，勿用 CLI。

```bash
cd <项目根>/code
# 经连接器 deploy（已授权），轮询直到 Status=normal, Traffic=100

# 回滚（脚本 v2，离线演练 15/15）
bash rollback.sh handoff-20260918 --url <服务URL>
# 实际: 校验目标 tag 本地存在 → git checkout + deploy --force（无灰度时不能 traffic rollback）
```

**部署后必做**：① 控制台确认 COS 挂载 `/mnt/chroma` 开启；② 确认 9 个环境变量齐全（含 `SOFT_REFUSE_THRESHOLD=0.45`）；③ 改阈值/换后端后 **`DELETE /cache`**。

---

## 十二、关键踩坑速查（完整 22 条见 `docs/` 或 skill `_references/pitfall-log.md`）

| # | 坑 | 解法 | 重要度 |
|---|---|---|---|
| 0.3 | CloudBase deploy 交互式卡灰度 | `--force 2>&1 <<< $'\n'` | ⭐⭐⭐ |
| 1.3 | AnswerCache 一存就返回 → 旧 | PROMOTE_THRESHOLD=2，hit≥2 才复用 | ⭐⭐⭐ |
| 1.8 | Chromadb 免费版 OOM | 砍了 → 自实现 SimpleVectorStore | ⭐⭐⭐ |
| 2.3 | 配色替换漏残留 | grep -c 验收门禁（紫 hero 为预期） | ⭐⭐ |
| **🔥 P0** | **Portal 字段 h.docs ≠ h.docs_count** | DeployId 017 数据全 0 根因，018 已修 | ⭐⭐⭐⭐⭐ |
| A | COS mount 路径错 → 全没数据 | Dockerfile 设 CHROMA_PATH=/mnt/chroma | ⭐⭐⭐⭐ |
| **M2** | **改阈值/换后端后不清缓存 → 返回旧答案** | 任何变更后 **`DELETE /cache`** | ⭐⭐⭐ |
| **拒答** | **GPU 类沾边题阈值救不了** | 往 KB 补「显卡需求说明」文档（已做，207→215） | ⭐⭐⭐ |
| **部署** | **tcb CLI 未登录(502)** | 改用 cloudbase 连接器部署，保留远程规格 | ⭐⭐⭐ |

> 完整 22 条踩坑详解见项目本地 `docs/22条踩坑完整清单.md` 或 FDE Skill 的 `_references/pitfall-log.md`。

---

## 十三、FDE Skill 简介（可复用）

完整 Skill 已随项目创建，独立可复用：

- 本地路径: `.agents/skills/rag-knowledge-base-skill/`
- 安装: `git clone` → `./install.sh` 一键装到 skills 目录
- 包含: 4 阶段协议 + 22 条坑 + 8 份参考 + 3 脚本 + 代码模板
- **下次做类似项目直接 clone 安装即可，跳过所有坑**

---

## 十四、Next Steps 建议（候选，按价值排序）

| 优先级 | 事项 | 理由 |
|---|---|---|
| ✅ 已完成 | Upgrade 旧 metadata / 清 chroma.sqlite3 / Embedding 升级 / Portal V5 / M2 省 Token / 拒答双层 / GPU KB 文档 | 见 handoff Done #21–#33 |
| P1 | **L1 阈值标定**：`CACHE_SIM_THRESHOLD` 0.92 → 0.88–0.90 | 真实命中分布积累后降档，换问法再省 |
| P2 | **CORS 收口** | `/query` 等跨域策略当前未显式限制 |
| P2 | **批量入库限流/大小上限** | 防单次上传打爆 2GB 内存 |
| P3 | **冒烟文档同步新增端点** | `/embed/*`、`/stats/token-economy`、`/kb/source/*` |

---

## 十五、本地交接包

最新交付包已刷新为 **026**：

```
LTC-RAGbot-交付包-20260919-v026.tar.gz  （243KB / 81 文件 · 已排除 .env/.git/chroma_data）
├── 项目汇总.md                    ★ 本交付文档的本地 Markdown 版（026 口径）
├── feishu_doc.md                 飞书交付文档（026 口径）
├── handoff.md                    ★ 续作日志（每次先读拿真相）
├── code/                        完整源码 (app.py 2073 行 + portal V5 + scripts + prompts)
├── docs/                        18 份深度文档（架构/坑/缓存/重排/7 份交付文档/巡检报告）
├── design/                      Portal V3 双主题效果图
└── .agents/skills/
    └── rag-knowledge-base-skill/   FDE Skill v1.0.0 完整副本
```

> ⚠️ 桌面旧包 `LTC-RAGbot-交付包-20260918.tar.gz` 已改名备份为 `LTC-RAGbot-交付包-018-旧-勿用.tar.gz`（滞后生产 8 版本，请勿使用）。**TW 交接请使用 026 包**。
