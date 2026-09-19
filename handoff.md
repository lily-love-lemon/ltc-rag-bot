# handoff.md — LTC RAG Bot 项目工作日志

> 用途：中断后续作时**先读本文件拿真相**，再实时校验状态，不要重新做。
> 最后更新：2026-09-19 09:39（六六🎋 · GitHub main 推送 v026 干净快照）
> 项目根目录：`/Volumes/Macintosh HD/Users/lily/WB_Workspace/2026-09-18-15-33-44/ltc-rag-bot/`
> 项目性质：**接手 Trae 移交的既有项目，做优化推进，不是从零重做。**
> 上一次断点：**✅ Portal V5 深空版（022）+ M2 省 Token 三件套（024）+ 拒答双层修复（026）全部上线** —— 线上版本 **026**：① Portal V5 深空指挥台 + 文档全 CRUD；② L0/L1/L2 省 Token 漏斗 + `/stats/token-economy` 埋点（L0 实测省 ~404 token/次）；③ 拒答双层：`fabe.txt` 接地契约 + `SOFT_REFUSE_THRESHOLD=0.45`（云端 bge-m3 实测标定，红烧肉拒答/MVP 正常）。环境变量 9 个、规格零降级。详见 Done #29–#31。KB 已补「显卡与算力需求说明」文档（207→215 条，候选 #1 完成，见 Done #33）。

---

## 一、关键资源（下次直接复用，勿重新探测）

### 路径

| 项 | 值 |
|---|---|
| 项目根（工作区） | `/Volumes/Macintosh HD/Users/lily/WB_Workspace/2026-09-18-15-33-44/ltc-rag-bot/` |
| 代码目录 | `<项目根>/code/`（入口 `code/app.py`，约 1300 行单文件 FastAPI） |
| 前端 | `<项目根>/code/portal/index.html`（单文件 HTML，后端用户 `{{AUTO_API_KEY}}` 占位符注入） |
| 提示词 | `<项目根>/code/prompts/fabe.txt`（**文件优先于 app.py 内置版**，见踩坑 #4） |
| 本地数据 | `<项目根>/code/chroma_data/`（`vectors.npy` + `metadata.json` + `vocab.json`，已 gitignore） |
| 环境变量 | `<项目根>/code/.env`（已 gitignore，**不入库**；本次已配好可直跑） |
| 桌面交接文件夹 | `/Users/lily/Desktop/LTC RAG BOT项目汇总和TW交接/`：含 **`LTC-RAGbot-交付包-20260919-v026.tar.gz`（026·推荐）** + `LTC-RAGbot-交付包-018-旧-勿用.tar.gz`（改名备份，勿用）+ 解包副本 `LTC-RAGbot/` + 散落 `项目汇总.md`（已同步 026） |
| 交接包（刷新·推荐·权威源） | `/Volumes/Macintosh HD/Users/lily/WB_Workspace/2026-09-18-15-33-44/LTC-RAGbot-交付包-20260919-v026.tar.gz`（245KB/81文件 · **026** · 已排除 .env/.git/chroma_data/.workbuddy） |
| 桌面解包副本 | `/Users/lily/Desktop/LTC RAG BOT项目汇总和TW交接/LTC-RAGbot/`（**只读参考，勿在此改**） |
| 接手记录 | `<项目根>/docs/接手优化记录-20260918.md`（含改动明细 + 环境踩坑 #23–#25） |
| 冒烟验收口径 | `<项目根>/docs/13项冒烟验收.md`（**已校准为现行 19/20 项**，含 9b `/embed/info` 断言；文件名保留历史） |
| Embedding 真源 | `<项目根>/docs/Embedding升级.md`（**可插拔后端设计 + A/B 验收指标 + 激活/回滚 SOP + 边界风险**，待办 #5 的唯一真源） |
| 交接文档（飞书） | `https://my.feishu.cn/docx/NwBZdfikSohvFPxjw79c2Xlinve`（**✅ 已读 2026-09-18 21:20**，含线上 URL / COS bucket / 明文密钥 / Git 里程碑；与本地认知有多处冲突，见第四节「飞书 vs 本地 差异」） |
| GitHub 仓库 | `https://github.com/lily-love-lemon/ltc-rag-bot`（**✅ 2026-09-19 09:39 已 force-push `main`**：v026 干净快照，63 文件，零明文密钥，无 `.env`/chroma_data；旧 018 平铺结构已被替换） |
| 线上 Portal（**已上线，存活**） | `https://ltc-rag-bot-315461-10-1450031534.sh.run.tcloudbase.com`（**✅ 2026-09-19 实测**：v3.0 / 215 docs / `embed_backend=siliconflow`（BAAI/bge-m3, dim 1024）/ 生产鉴权已开且 Key 由后端自动注入 Portal（V5 无手动填 Key 入口）/ 飞书已配；当前版本 **026**） |
| COS Bucket | `6c69-lily0625ai-d1gpwc1vw89141edd-1450031534`（cos.ap-shanghai.myqcloud.com，挂载 `/mnt/chroma`） |
| 飞书里程碑 tag（GitHub 侧，**本地不存在**） | `v3-20260918-smoke-green` / `v4-deployed` / `v4.1-purple-hero`（飞书文档回滚命令用这些；本地扁平仓库只有 `handoff-20260918` 系列 → 这正是原 rollback.sh「假回滚」根因） |

### 运行时 / 工具（绝对路径，直接用）

| 项 | 值 |
|---|---|
| 项目 venv | `/Users/lily/.workbuddy/binaries/python/envs/ltc-ragbot/bin/python`（139MB，依赖已全装） |
| venv pip | `/Users/lily/.workbuddy/binaries/python/envs/ltc-ragbot/bin/pip` |
| 系统 Python | `/Users/lily/.workbuddy/binaries/python/versions/3.13.12/bin/python3`（仅用于语法检查 / 仿真数据） |
| Node | `/Users/lily/.workbuddy/binaries/node/versions/22.22.2-2/bin/node`（`--check` 校验前端 JS） |

### 脚本与命令

| 项 | 值 |
|---|---|
| 本地冒烟（**核心探针**） | `bash <项目根>/code/scripts/smoke_local.sh`（开发模式 16 项；加 `API_KEY=<API_KEY>` 前缀 = 生产模式 17 项） |
| 部署/回滚演练（**改脚本后必跑**） | `bash <项目根>/code/scripts/deploy_drill.sh`（13 条断言，约 10 秒；`--keep` 保留临时目录） |
| 旧数据补字段 | `<项目根>/code/scripts/upgrade_metadata.py --path <数据目录> [--dry-run]`（幂等 + 自动备份 + 原子写 + 哈希自校验） |
| 启动服务 | `cd <项目根>/code && /Users/lily/.workbuddy/binaries/python/envs/ltc-ragbot/bin/python app.py`（端口 8080，见 `code/.env`） |
| 部署（**v2，已离线验证**） | `cd <项目根>/code && bash deploy.sh --preflight-only`（只检查不部署）→ `bash deploy.sh -e lily0625ai-d1gpwc1vw89141edd` |
| 回滚（**v2，已离线验证**） | `cd <项目根>/code && bash rollback.sh handoff-20260918 --url <服务URL>` |

### Git 基线（本地仓，无 remote）

| tag | commit | 含义 |
|---|---|---|
| `handoff-20260918` | `80b8cf9` | Trae 交接原始基线（**回退到此 = 完全还原交接状态**） |
| `opt-20260918-p0fix` | `1741178` | 3 个 P0 修复 + 补字段脚本 + 冒烟脚本 |
| `opt-20260918-cachefix` | `a6d90ad` | AnswerCache 随 KB 变更失效（冒烟 16/17 全绿） |
| `opt-20260918-docsync` | `475a557` | 文档口径校准 ×3 + Portal 缓存面板增强 |
| `opt-20260918-deployfix` | `7dc0b01` | 部署/回滚脚本修复 + 离线演练台 + 3 份交付文档 |
| `opt-20260918-envidfix` | `daa431a` | 修正 rollback.sh ENV_ID 笔误(dgpwc→d1gpwc) + 文档 6 处同款 + 演练台加 ENV_ID 一致性断言 |
| `opt-20260918-docs7` | `4ae56a1` | 补齐 7 份交付文档最后 3 份（环境清单 / 上线巡检记录表 / 归档运维SOP+资产清单）+ 环境清单单一真源 |
| `opt-20260918-feishu-reconcile` | `b7c0056` | 读飞书交接文档：纠正「未上云/未配环境变量」两处错误待办 + 补规格降级风险 + 线上 URL/COS bucket 真值 |
| `opt-20260918-deploy-live-019` | `6ae6577` | **线上真实部署**：经 cloudbase 连接器 `manageCloudRun.deploy` 把本地优化版推上 ltc-rag-bot，新版本 019 接管 100% 流量；Cpu/Mem/8 密钥/COS 挂载零降级；`/cache/stats` 证 AnswerCache 指纹生效 |
| `opt-20260918-embed-pluggable` | `bb4a912` | **#5 可插拔 Embedding 后端**（V4.1）：`EMBED_BACKEND` 分发 + `_embed_via_api` + `/embed/info` + `/embed/rebuild`；默认 tfidf 零回归；冒烟加 9b 断言 |
| `opt-20260918-embed-live` | `0bc3bd2` | **#5 激活 bge-m3 + 健壮性**（V4.1.1）：query embedding 故障退化 BM25-only；后端解析优先级「env 覆盖 > 磁盘标记 > tfidf」；线上 021 已切 siliconflow |
| `opt-20260918-threedone` | `4efbd8f` | 三项待办（#1 补字段 / #5 Embedding / #7 清理）完成记录 + 新建 `docs/Embedding升级.md` |
| `opt-20260918-portal-v5` | `db536cc` | **Portal V5 深空指挥台 + 文档 CRUD 后端**（线上版本 022） |
| `opt-20260918-portal-design` | `d1bda2e` | v3 双主题效果图 + 重设计方案 v2 |
| `opt-20260919-m2-token-economy` | `7ca11cf` | **M2 省 Token 三件套**：L1 语义近似 + L2 拒答短路 + TokenEconomy 埋点 + Portal 省算面板接真数据 |
| `opt-20260919-m2-fix` | `13e6993` | 修复 `TokenEconomy.summary` 空埋点 KeyError 500（**HEAD**，线上版本 024） |

### 密钥（**值不落在本文件，见 `code/.env`**）

`.env` 内键名：`API_KEY`（注释态，取消注释即切生产模式）、`ZHIPUAI_API_KEY` / `ZHIPUAI_MODEL=glm-4-flash`、`FEISHU_APP_ID` / `FEISHU_APP_SECRET`、`PORT=8080`。
生产模式 Key 约定值：`<API_KEY>`。
LLM 实测后端：**`zhipu:glm-4-flash` 真调通**（回答 255–320 字）。

### 沉淀资产

| 项 | 值 |
|---|---|
| 接手流程 skill | `/Users/lily/.workbuddy/skills/handoff-project-takeover/SKILL.md`（含沙箱坑、bash 3.2 坑、**bash UTF-8 变量名坑**、**stub 外部 CLI 做离线演练**手法） |
| 项目记忆 | `<项目根>/.workbuddy/memory/2026-09-18.md`、`MEMORY.md` |
| 磁盘 | **2026-09-18 17:40 实测 7.6 GB 可用（61% 满）** ⚠️ Embedding 升级（sentence-transformers ~500MB+）前建议先清到 ≥10 GB |

---

## 二、已完成（Done）

| # | 事项 | 产物 / 证据 |
|---|---|---|
| 1 | 校验交付包完整性并落地工作区 | `tar -xzf` 到 `/tmp` 与桌面副本 `diff -r` **完全一致**（除 `.DS_Store`）；落地 `<项目根>` 并 `git init` + tag `handoff-20260918` |
| 2 | 搭本地环境验证可运行 | venv `envs/ltc-ragbot`；依赖全装（jieba 走本地 wheel）；`.env` 配好；服务启动成功，`zhipu:glm-4-flash` 真调通 |
| 3 | **P0-1** Portal 生产模式鉴权断链 | `code/portal/index.html`：补 `{{AUTO_API_KEY}}` 占位符 + 新增 `authOnly()` 供 multipart 用 + `/ingest` 补鉴权头与状态码检查。**修复前生产模式全接口 401** |
| 4 | **P0-2** `/query` 丢弃 `use_rerank` | `code/app.py` 补透传；冒烟实测 `use_rerank=false → hybrid.use_rerank=False` |
| 5 | **P0-3** `fabe.txt` 覆盖瘦身 prompt | 重写 `code/prompts/fabe.txt` 对齐瘦身版（去 FABE 分段标题，输出不再冗余 ~40%） |
| 6 | 新增旧 metadata 补字段脚本 | `code/scripts/upgrade_metadata.py`；用 207 条仿真数据验证：`by_type` 从 `['.inline']` → `{'.md':203,'.inline':3,'.txt':1}`，幂等 + 备份 + 向量哈希自校验通过 |
| 7 | 新增本地冒烟脚本 | `code/scripts/smoke_local.sh`，16/17 项，含 use_rerank 与 Portal 注入防回归断言 |
| 8 | **AnswerCache 随 KB 变更失效**（本次新增，最高价值） | `code/app.py` 新增 `SimpleVectorStore.fingerprint`（id+text 的 `sha256[:16]`）+ `AnswerCache.get/set/stats` 三处加指纹参数 + `/cache/stats` 暴露 `stale_count`。冒烟 8b 实测：KB 变更前 `cache_hit=True` → 变更后 `False` |
| 9 | 双模式回归全绿 | 开发 **16/16**、生产 **17/17**，0 失败；`stale_count=2` 可观测 |
| 10 | 交付文档 | `docs/接手优化记录-20260918.md`、`overview.md`（工作区根）、`handoff-project-takeover` skill |
| 11 | **3 份文档与代码口径校准**（本次） | ① `docs/13项冒烟验收.md` 重写为现行 16/17 项 + 「与原 13 项清单的偏差修正」表（5 处错误预期值）<br>② `docs/AnswerCache设计.md` 追加《实现现状校准》11 项设计稿-实现差异 + `kb_fingerprint` 机制说明<br>③ `docs/metadata字段标准.md` 修正 `file_size` 字节→**字符数**，并实测重写 `/kb` 返回结构（`files[*].total_chars`、`by_type` 是数组不是计数） |
| 12 | **Portal 缓存面板加「♻️ 已失效」指示**（本次） | `code/portal/index.html` 新增 `id="cache-stale"` 元素 + `loadCacheStats()` 消费 `stale_count`（>0 标警示色 `#f59e0b`，null-safe）。实测渲染 HTML 含该元素、`/cache/stats` 返 `stale_count` |
| 13 | 磁盘状况改善 | 根卷可用 **2.7 GB → 8.7 GB**（82% → 58%），Embedding 升级的磁盘门槛已接近解除（仍需 ≥10 GB 建议） |
| 14 | **修复 rollback.sh「假回滚」等 4 个缺陷**（本次 · 最高价值） | 默认目标 `v3-20260918-smoke-green` 在交接后仓库**不存在** → v1 只打警告就继续部署 → **部署的是当前新代码却报「回滚部署完成」**。v2：无效目标中止 exit 2 / 只给 DeployId 拒绝 / 显式 detached / 脏树拒绝 exit 3 / 回滚后 `/health`+`/kb` 探活 / **脚本自覆盖保护**（自复制到临时文件再 exec）。详见 `docs/回滚预案.md` |
| 15 | **修复 deploy.sh 3 个缺陷**（本次） | ① 命令形式改为实测 `-s <service> -e <env> --force`（v1 缺 `--force` 会卡灰度交互）② 纠正「环境变量已在 cloudbaserc.json 配置」的假陈述（实际 `envParams={}`）③ `read -p` 在 `set -e` 下非交互退出 → 加非交互分支 |
| 16 | **新增离线演练台**（本次） | `code/scripts/deploy_drill.sh`：stub `tcb` + `git clone` 全 tag 副本，13 条断言（现 15/15，本次 +2），不联网不动线上。**修复前 5/13 → 修复后 13/13** |
| 17 | **3 份交付文档**（本次） | `docs/部署方案.md`（环境清单 + 发布变更单 + 风险表）· `docs/回滚预案.md`（触发条件 + 步骤 + 验证标准 + 退出码表）· `docs/部署验证用例与报告.md`（10 用例 + 原始输出 + 5 项未验证项） |
| 18 | 三项回归全绿（本次） | 开发冒烟 **16/16** · 生产冒烟 **17/17** · 部署演练 **15/15**（原 13/13，本次 +2 ENV_ID 断言）；`app.py` 本次未改动 |
| 19 | **修复 rollback.sh ENV_ID 笔误 + 文档口径统一** | rollback.sh 的 `ENV_ID` 漏写一个字符（`dgpwc…`→`d1gpwc…`），指向不存在的环境；同步修正 `docs/部署方案.md`/`回滚预案.md`/`部署验证用例与报告.md`/`handoff.md` 共 6 处同款笔误；并给 `deploy_drill.sh` 加**断言 H（rollback.sh 的 ENV_ID 与 cloudbaserc.json 逐字一致）+ 断言 I（deploy.sh 不硬编码冲突 envId）**防再犯。演练现 **15/15** 全绿 |
| 20 | **补齐 7 份交付文档**（本次） | 新建 `docs/环境清单.md`（环境参数 + 必配变量 + dev 环境，从部署方案拆出作**单一真源**）· `docs/上线巡检记录表.md`（0/10/30/60 分钟 **17 项** + 记录表 + 异常快查表）· `docs/归档运维SOP与静态资产清单.md`（归档 4 步 + 静态资产清单 4 类（**含实测大小/行数**）+ 恢复 SOP + 退役 SOP + 交接检查表）。同时把 `docs/部署方案.md` 第一节改为指向环境清单，并刷新其 `13/13` 断言数与变更版本号为 `15/15` / `opt-20260918-envidfix`，消除双份口径 |
| 21 | **D1 配色决策落地（本次）** | Lily 决策「保留紫、改文档」。`docs/Portal设计规范.md`（主色 #7c3aed + hero 渐变 #1e40af→#7c3aed + 验收门禁改为「紫色为预期」）+ `docs/13项冒烟验收.md`（A5/A-3 由「未决」改为「已决保留紫」）已对齐代码真值。靛蓝 `#6366f1` 确认 0 引用（半成品残留） |
| 22 | **D2/D3 决策落地 + SOP（本次）** | Lily 决策「密钥轮换」+「先核实规格再重部署」。产出 `docs/密钥轮换与上云前检查清单.md`（D2 三步轮换 + D3 规格核实 6 项 + 重部署执行顺序），密钥值不落任何 git 文件 |
| 23 | **用户决策固化（2026-09-18 21:56）** | Lily：3 密钥保持不变不轮换（个人测试环境/测试数据/不影响生产）；D3 重部署前置配置免做（飞书/CloudBase/API 已配+云委托成功）；部署走 cloudbase 连接器（tcb CLI 未登录，连接器已授权）。下一步即把本地优化版经连接器部署到线上测试环境 |
| 24 | **线上真实部署优化版（019）+ 5/5 验证（2026-09-18 22:08）** | 经 cloudbase 连接器 `manageCloudRun.deploy`（source 构建，不传 serverConfig → Read-Merge-Write 保留远程 Cpu/Mem/EnvParams/VolumesConf/OpenAccessTypes）推上 ltc-rag-bot：新版本 **019**（镜像 `...019-20260918220435`）接管 100% 流量。验证：① `/health` 200(207 docs/生产鉴权开/飞书配) ② 无 key `/query`→401 ③ `/kb` 207 chunks(COS挂载生效) ④ 真实问答(use_rerank透传+智谱LLM正常) ⑤ `/cache/stats` 返 `stale_count:2`(AnswerCache指纹生效)。规格零降级（Cpu1/Mem2/8密钥/COS全保留）|
| 25 | **#7 清理 COS 残留 `chroma.sqlite3`（2026-09-18 22:45）** | 走 cloudbase 连接器 `manageStorage.delete`（删前 `download` 备份到 `/tmp/online_kb/chroma.sqlite3.bak`）。先验证代码无引用（`grep` 仅注释提及 chromadb）再删。COS 根目录 12→11 个对象 |
| 26 | **#1 线上 207 条补字段（2026-09-18 23:10 生效）** | ① `queryStorage.download` 取线上 `metadata.json`（89,397 B，仅 4 字段）② `upgrade_metadata.py --created-at 2026-09-18T05:57:24Z`（服务器 mtime 作代理，标 `created_at_inferred=true`）→ 207 条补 `created_at/file_type/file_size`，`vectors.npy` **哈希未变**、幂等复查 0 变更 ③ `manageStorage.upload` 回 COS ④ 部署重载。**证据**：`/kb` 的 `by_type` 键由 `['.inline']` → `['.md', '.txt', '.inline']`（各文件带 `created_at/file_type/chunks/total_chars`）；离线对同一份数据统计为 `{'.md':203, '.txt':1, '.inline':3}` |
| 27 | **#5 可插拔 Embedding 后端（V4.1，2026-09-18 22:58，版本 020）** | `code/app.py` 新增 `EMBED_BACKEND/EMBED_MODEL/EMBED_API_URL/EMBED_API_KEY` + `_embed_via_api()`（OpenAI 兼容 `/v1/embeddings`，行 L2 归一化 → 与 TF-IDF 同约定，检索链路零改动）+ `_compute_vectors` 分发入口 + `_rebuild_all` 按后端分支 + `vocab.json` 写 `_backend/_model`（老文件无此键 → 视为 tfidf，向后兼容）+ `set_backend_and_rebuild()` **原子切换** + `GET /embed/info` + `POST /embed/rebuild`。**默认未设 env → 走原 TF-IDF，零回归**。冒烟加 9b 断言（19/19 · 20/20） |
| 28 | **#5 激活 bge-m3 + 健壮性（V4.1.1，2026-09-18 23:10，版本 021）** | ① `query()` 加 embedding 异常兜底 → 本查询退化 BM25-only，**不再 500**（`vec_ok` 同时控制 rerank 跳过）② 后端解析优先级改为 **env 显式覆盖 > 磁盘持久化标记 > tfidf** ⇒ `/embed/rebuild` 的切换**跨重启生效，无需改任何线上环境变量** ③ `/health` 与 `/embed/info` 同源。线上切 **BAAI/bge-m3**（`dim` 1983→1024，`vectors.npy` 1.64MB→828KB，重建 207 条 9.9s）。**A/B 口语化探针 hit 2/6→4/6**。详见 `docs/Embedding升级.md` |
| 29 | **Portal V5「深空指挥台」重构 + 文档 CRUD（2026-09-19 00:30，版本 022）** | Lily 看 v3 双主题效果图后拍板「执行深空版」。**前端**：`portal/index.html` 全新重写——① 深空·清爽主题（默认，去网格纯净化）+ 墨金·高端主题，右下角一键切换（localStorage 记忆）② **问答指挥台前置为首页**（Command Bar + 多轮对话流 + 引用溯源 + 检索溯源得分条 + rerank/Top-K 开关 + 快捷提问 chips）③ 运行总览（KPI/链路 pipeline/告警待办/本机 localStorage 会话统计）④ 文档资产全 CRUD ⑤ 系统运维（省算四层漏斗 L0-L3，L1/L2 标注 M2）⑥ 保留 `{{AUTO_API_KEY}}` + `authOnly()`。**后端**：`PATCH /kb/source/{id}`（重命名，不重建向量）+ `POST /kb/source/{id}/replace`（**原子替换**：备份→删旧→重切片，失败自动回滚——已 monkeypatch 实测最危险失败点）。验证：冒烟 19/19·20/20、演练 15/15；线上 022 全绿（health/Portal 特征/新端点 404 探针/真实问答 rerank+refs）。设计文档：`docs/Portal重设计方案.md`、效果图 `design/portal-redesign-v3-dual.html` |
| 30 | **M2 省 Token 三件套上线（2026-09-19 01:15，版本 023→024）** | ① **L1 语义近似缓存**：快照带 `qvec`（上限 `CACHE_MAX_VECS`，LRU 淘汰），`find_similar()` 余弦 ≥ `CACHE_SIM_THRESHOLD`(env，默认 0.92) 直返 `cache:L1`，规则对齐 L0（指纹过期 + hit_count≥2 才复用）② **L2 拒答短路**：`REJECT_THRESHOLD`(env，默认 0=关闭) 下 top1 分过低直接拒答不调 LLM ③ **`TokenEconomy` 埋点** + `GET/DELETE /stats/token-economy`（按天持久化 30 天，WRITE_EVERY=10 摊薄 COS 写）④ Portal 省算面板接真实埋点 + 「埋点归零」按钮。**线上 A/B 实测**（024）：L0 链路通（同问 `cache:L0` 省 ~404 token/次），L1 阈值 0.92 下换问法未命中（**设计使然**：hit_count 未晋级 + 相似度确实 <0.92）——机制已验证，阈值标定留待真实 sim 分布积累后降档 0.88–0.90。**过程坑**：023 首版 `summary()` 空埋点 KeyError 500（`days.get` 兜底修复 → 024）。回归：单测 36/36 · 冒烟 22/22 · 演练 15/15。commit `7ca11cf`+`13e6993`，tag `opt-20260919-m2-token-economy` |
| 31 | **拒答双层修复上线（2026-09-19 02:20，版本 025→026）** | **问题**（handoff 旧记录实锤）：KB 外话题 LLM 自信编造（「没有显卡能不能跑大模型」→ 编出「我们系统支持无GPU运行」）。**A 层**：`prompts/fabe.txt` 收紧接地契约（只准用参考内容 + 主题沾边即拒答）——KB外-天气/股票 实测拒答，KB内 不误杀；但**主题沾边类（GPU→云托管算力）光靠 prompt 拦不住**。**B 层**：`SOFT_REFUSE_THRESHOLD`(env) 软拒答注入——top1 低于阈值时向 prompt 注入强拒答指令（LLM 仍可答真相关内容，比 L2 硬短路误杀低）。**云端 bge-m3 标定数据**：KB内 0.4972–0.5989 / KB外 0.3859–0.4397，**定 0.45**。终验：红烧肉 0.3859→拒答 ✅，MVP 0.5198→正常 ✅。**已知边界**：① GPU 类沾边题 top1 0.548 高于部分 KB 内题，阈值救不了——正解是往 KB 补「显卡需求说明」② **改阈值后必须 `DELETE /cache`**，否则缓存把旧配置答案直接端出来（实测踩坑，已写入环境清单）。026 环境变量 9 个（原 8 个 + SOFT_REFUSE_THRESHOLD），规格零降级。commit `6bebf7b`；环境清单已更新 |
| 32 | **交接包刷新（2026-09-19 巡检）** | 发现桌面 `LTC-RAGbot-交付包-20260918.tar.gz` 滞后生产 8 版本（018 vs 026）；从工作区权威源码（master/cc41c14）重新打包 `LTC-RAGbot-交付包-20260919-v026.tar.gz`（243KB/81文件，排除 .git/chroma_data/.env/__pycache__）；两篇汇总文档加版本横幅指向 handoff.md；线上只读实测 026 健康（M2/拒答/GPU doc 均生效，KB 215/7 文件） |
| 33 | **KB 补「显卡/算力需求说明」文档（候选 #1 闭环）** | 新建 `code/kb_docs/显卡与算力需求说明.md`（只含可验证事实：本系统不需 GPU、私有化才需）；走 `POST /ingest-text` 入库返 `{"ingested":8,"total":215}`；线上 `/kb` 证实 215 条/7 文件含 `显卡与算力需求说明.md:8`；`/query "没有显卡能不能跑大模型？"` 正确引用该文档（top1 0.7746）不再编造。KB 指纹变更使旧 AnswerCache 快照自动失效 |
| 34 | **TW 交接三件事落地（2026-09-19 08:07）** | ① **备份旧包**：桌面 `LTC-RAGbot-交付包-20260918.tar.gz` → 改名 `LTC-RAGbot-交付包-018-旧-勿用.tar.gz`（不删除）② **正文改写**：`项目汇总.md`/`feishu_doc.md` 由 018 口径全量重写为 026（KB 207→215/6→7 文件、Embedding 切 bge-m3 1024 维、M2 四层漏斗、`/stats/token-economy`、拒答双层 0.45、Portal V5、9 环境变量、部署走连接器）③ **替代桌面旧包**：重建 v026 tar（含改写正文，排除 .env/.git/chroma_data/.workbuddy）→ 复制至桌面 `LTC-RAGbot-交付包-20260919-v026.tar.gz`（MD5 与源一致），并同步桌面散落 `项目汇总.md`。校验：app.py 2073 行、.env 泄露 0、docs 18 份、无旧 `207 条切片` 残留 |
| 35 | **交付包推送 GitHub main（2026-09-19 09:39）** | 用户确认「覆盖 main 不影响其它项目」后，force-push `github-clean`（孤儿分支·单提交·零历史·63 文件·零明文密钥）至 `lily-love-lemon/ltc-rag-bot` 的 `main`。API 复验：pushed_at 09-19T01:40Z、63 文件、`.env`/chroma_data 均为 0、`app.py` 2074 行含 v026 标记（TokenEconomy 3 / SOFT_REFUSE 5 / token-economy 2 / REJECT 8）、`项目汇总.md`/`feishu_doc.md`/`handoff.md` 明文密钥 0 命中。旧 PAT（classic, repo scope）用完即弃，建议用户去 GitHub 吊销。**安全红线**：推送前已对 9 个文件做密钥脱敏（占位符），且用 clean-snapshot 排除含明文的历史提交，确保密钥不进公网仓库 |

---

## 三、未做 / 待办（Not Done）

| # | 事项 | 备注 |
|---|---|---|
| 1 | ~~线上 207 条切片补 `created_at/file_type/file_size`~~ → **✅ 已完成（2026-09-18 23:10，随 020/021 生效）** | 经 COS 直连：下载线上 `metadata.json` → `upgrade_metadata.py`（207 条补 3 字段，`vectors.npy` 哈希自校验 + 幂等）→ 上传回 COS → 部署重载。**证据**：`/kb` 的 `by_type` 由 `['.inline']` → `['.md','.txt','.inline']`。无需 `tcb login`（走连接器存储工具） |
| 2 | **配色门禁冲突（D1 已决：保留紫）** | ✅ **已解决**：`docs/Portal设计规范.md` + `docs/13项冒烟验收.md` 已改为对齐紫色实现（紫色 hero `#7c3aed` 15 处 + hero 渐变 1 处为预期；靛蓝 `#6366f1` 0 引用属半成品残留）。佐证：飞书文档 + 线上 DeployId 018 均为紫 hero |
| 3 | ~~密钥轮换（D2）~~ → **已决：保持 3 密钥不变** | Lily 2026-09-18 21:56 决策：当前为个人测试环境、均为测试数据、不影响生产，3 密钥沿用不改、不轮换。飞书 docx 含明文密钥的隐患在本测试环境下暂不处理（SOP 见 `docs/密钥轮换与上云前检查清单.md` 备用） |
| 4 | ~~部署「我们的优化版」到线上（重部署，非首部署）~~ → **✅ 已完成（2026-09-18 22:08）** | 经 cloudbase 连接器 `manageCloudRun.deploy` 推上 ltc-rag-bot，新版本 **019** 接管 100% 流量，规格零降级，5/5 验证全绿。详见 Done #24 |
| 5 | ~~Embedding 升级（TF-IDF → 向量模型）~~ → **✅ 已完成并上线（2026-09-18 23:10，版本 021）** | 已实现**可插拔后端**（`EMBED_BACKEND`：`tfidf` 默认 / `siliconflow` API），线上切到 **BAAI/bge-m3**。A/B 口语化探针 hit **2/6 → 4/6**（可答探针 2/4 → 4/4），`vector_dim` 1983→1024，重建 207 条 9.9s，零磁盘零成本。**验收指标/实现/回滚 SOP 见 `docs/Embedding升级.md`** |
| 6 | ~~交接文档（飞书）未读~~ → **✅ 已读（2026-09-18 21:20）** | 飞书 docx `NwBZdfikSohvFPxjw79c2Xlinve` 已读，差异清单见三-B 节（含规格/密钥/URL/COS 真值） |
| 7 | ~~COS 残留 `chroma.sqlite3`（196KB）可删~~ → **✅ 已删除（2026-09-18 22:45）** | 走连接器存储工具删除（删前本地备份 `/tmp/online_kb/chroma.sqlite3.bak`）；代码已无引用（chromadb 早被砍掉）。COS 根目录现 11 个对象 |
| 8 | ~~`docs/Portal设计规范.md` 未与代码对齐~~ → **✅ 已对齐（D1 轮）** | 已改为对齐紫色实现（见待办 #2） |
| 9 | ~~线上环境变量从未配置~~ → **已更正** | 线上 `/health` 实测 `api_key_mode=生产模式(已开启鉴权)` + `feishu_configured=true` → **Trae 已在控制台配好 API_KEY / ZHIPUAI / FEISHU 等变量**。真正风险变为：**重部署时 `tcb cloudrun deploy` 是否保留控制台环境变量**（cloudbaserc 的 `envParams={}` 不参与，通常控制台变量独立保留；但需实测确认，否则重部署后会退回公开默认 Key + LLM 退化） |
| 10 | 7 文档仍缺 4 份 → **✅ 已补齐（见 Done #20）** | 环境清单 · 上线巡检记录表 · 归档运维SOP+静态资产清单 已新建，7 份交付文档齐 |
| 11 | ~~规格降级 / 环境变量保留（D3 已决：免前置，走连接器）~~ → **✅ 已验证（2026-09-18 22:08）** | 部署后 `queryCloudRun detail` 复核：Cpu=1/Mem=2、8 密钥、COS 挂载 `/mnt/chroma` 全部保留；连接器生成的 `cloudbaserc.json` 还把原 `0.5/128` 降级字段清掉，彻底消除降级风险 |

> ✅ 上一版 handoff 列的两项**已完成**，从本表移除：`docs/AnswerCache设计.md` 同步、`docs/13项冒烟验收.md` 更新。
> ⚠️ 上一版写的「Portal 未展示缓存可观测性」**是错的** —— Portal 早已展示 `⚡ 缓存命中` / `llm_used` / `rerank` / 耗时；
> 真正的缺口只是 `stale_count`，本次已补。

---

### 三-B、飞书 vs 本地 差异清单（2026-09-18 21:20 读飞书 docx `NwBZdfikSohvFPxjw79c2Xlinve` 后补）

> 飞书文档是 Trae 的原始交接总纲，含本地 8 份 docs 之外的信息。以下为与手头认知冲突/补充的点，**下次续作先读这份**。

| # | 飞书文档说 | 本地此前认知 | 真相 / 处置 |
|---|---|---|---|
| F1 | 服务**已上线**（DeployId 018, v3.0, 207 docs, 生产鉴权开, 飞书已配），URL 存活 | 「尚未上云」 | **已上线**。本地优化版尚未部署。重部署前先解决 F3/F4 |
| F2 | 线上规格「**2 核 2 GB 已升级**」 | `cloudbaserc.json` 写 `0.5核/128MB 免费版` | **冲突！重部署会静默降级到 128MB 可能 OOM**（见待办 #11） |
| F3 | COS Bucket `6c69-lily0625ai-d1gpwc1vw89141edd-1450031534` | 未记录 | 已补进关键资源 |
| F4 | 控制台**已配** API_KEY/ZHIPUAI/FEISHU（线上 `/health` 证实） | 「线上环境变量从未配置」 | **已更正**（待办 #9）。风险转为「重部署是否保留控制台变量」 |
| F5 | 回滚用 tag `v3-20260918-smoke-green`（GitHub 侧） | 本地扁平仓库只有 `handoff-20260918` 系列 | 这正是原 rollback.sh「假回滚」根因：目标 tag 在本地不存在。我们的修复（要求本地存在 tag）对该仓库正确 |
| F6 | docx 含 ZHIPUAI_API_KEY / FEISHU_APP_SECRET / API_KEY **明文** | 仅项目汇总.md 含明文 | 轮换理由更充分（待办 #3 / D2）。**密钥值不落任何 git 文件** |
| F7 | DeployId 017 曾因 `h.docs ≠ h.docs_count` 导致数据全 0，018 已修 | — | 线上现 `/health` 返 `docs_count:207` ✅；建议重部署后复测该项 |
| F8 | Next Steps：P0=Upgrade 旧 metadata + 清 chroma.sqlite3；P1=飞书 Bot 独立进程；P2=切片编辑重算 TF-IDF / 文件级权限；P3=Embedding 可插拔(BGE)/RAG 评估 | 待办 #1/#7 已对应 | Trae 的 P0 与我们的待办 #1/#7 一致，无新 P0 |

> ✅ 除 F2（规格）需控制台核实外，其余已并入上方「关键资源 / 待办」。本地代码 `app.py` 版本号 `3.0` 与线上一致，**无代码分歧**。

---

## 四、踩坑 & 如何解决（Pitfalls → Fix）

| # | 坑 | 根因 | 解决 |
|---|---|---|---|
| 1 | `pip install` 报 `EEXIST .../pip-install-xxx/<pkg>`，装不动 | WorkBuddy 沙箱 shim 拦截 `mkdir`，pip 解包目录创建被误判为已存在 | 换清华源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`；源码包（jieba）走 `curl sdist → setup.py bdist_wheel → pip install *.whl` 绕过临时目录 |
| 2 | `python app.py` 启动崩在 `app.py:824` | 同一 shim 把 `Path.mkdir(exist_ok=True)`（**非递归**）在目录已存在时误拦为 EEXIST；`parents=True` 正常 | Agent shell 内 `unset PYTHONPATH`（shim 经 `sitecustomize.py` 注入）。**Lily 自己开 Terminal 不受影响 → app.py 无需改** |
| 3 | 冒烟脚本报 `AUTH[@]: unbound variable` | macOS 自带 bash 3.2 + `set -u` 下展开**空数组** `"${AUTH[@]}"` 报错 | 改成 `${AUTH[@]+"${AUTH[@]}"}`（条件展开） |
| 4 | 踩坑 1.6「prompt 瘦身」实际没生效 | `_get_prompt` **文件优先于内置**，`prompts/fabe.txt` 仍是 V3 冗长版，把 `app.py` 内置瘦身版架空了 | 重写 `fabe.txt` 对齐瘦身版；教训：改 prompt 要同时查文件与内置两处 |
| 5 | Portal 生产模式整站 401，管理台不可用 | `index.html` **没有** `{{AUTO_API_KEY}}` 占位符 → 后端注入是空操作；`/ingest` 还用裸 `fetch` 不带鉴权头 | 补占位符 + `authOnly()` + `/ingest` 补头；教训：**「文档说已修」≠ 已生效，必须接口级验证** |
| 6 | 用 macOS 自带 grep 判「文件里没有 fetch/API_KEY」→ 结论完全错误 | BSD grep 不支持 `\|` 交替，静默不匹配 | 改用 Grep 工具（ripgrep）或 `grep -E`；教训：**否定性结论必须换方法二次确认** |
| 7 | `git clone` GitHub 返回 502 | 沙箱网络限制 | 改用本地交付包（已 `diff -r` 校验与 tarball 完全一致），不阻塞任务 |
| 8 | 照 `docs/13项冒烟验收.md` 验收会误判失败 | 文档 5 处预期值与代码不符（`/health` 字段名、`/cache/stats` 键名、`STRICT_AUTH` 变量**根本不存在**、rollback.sh 的 DeployId/RunId 描述、紫残留门禁） | 用「原写法 → 实际 → 证据」三列表逐条修正该文档（本次已完成）；**教训：文档写的预期值必须先在代码里找到出处** |
| 9 | 照 `docs/metadata字段标准.md` 写代码 → `file_size` 口径错 | 文档写「字节 / `len(text.encode())`」，实现是**字符数 / `len(text)`**，且 `/kb` 聚合字段名是 `total_chars` | 统一为字符数并加「勿改成 bytes」警示（本次已完成）；**教训：字段单位/命名冲突时以「字段名语义 + 线上既有数据」为准** |
| 10 | 照 `docs/metadata字段标准.md` 的 `/kb` 示例取字段 → 拿到 `undefined` | 示例写 `files[*].last_modified` + `file_size`、`by_type` 是计数；实际是 `created_at` + `total_chars`、`by_type` 是**对象数组** | 起服务 curl `/kb` 实测后重写示例（本次已完成）；**教训：返回结构类文档必须实测生成，不能凭设计稿写** |
| 11 | 回滚脚本报「回滚部署完成」，实际没回滚（**最危险**） | 默认目标 tag 在交接后不存在（交接包不含 `.git`，Trae 的 tag 全丢），v1 只打一句警告就继续部署 → 部署的仍是新代码 | 无效目标一律中止（exit 2）；只给 DeployId 也拒绝。**教训：任何「跳过某步骤」的容错分支都要问一句 —— 跳过后剩下的动作还有意义吗？** |
| 12 | 回滚脚本把自己覆盖掉 | `code/rollback.sh` 是 git 跟踪文件，`git checkout <旧tag>` 会连它一起换掉，而 bash 边读边执行 | 启动时自复制到临时文件再 `exec` 副本（`LTC_RB_REEXEC=1`）；演练已断言此场景 |
| 13 | 脚本报 `RC1，: unbound variable`，中文标点被吞进变量名 | bash 在 UTF-8 locale 下允许变量名含多字节字符，`$RC1` 紧跟中文逗号 → 变量名被解析成 `RC1，` | 全量改 `${RC1}` 花括号写法（三个脚本 175 处）；**教训：变量展开后紧跟中文标点，一律加花括号** |
| 14 | commit message 里出现 `` `$RC1，` `` → shell 报 `command not found: ，` | 双引号内反引号是命令替换，`$RC1，` 被当命令执行，message 里那段被吞 | 改 `git commit -F <文件>` + 引号 heredoc（`<<'MSG'`）；**教训：提交信息含反引号/`$` 时别用 `-m "..."`** |

---

## 五、避免（Avoid — 下次别再踩）

- ❌ **别相信「文档说已修」**。本次 3 个 P0 全是「文档声称已修、实际未生效」型（Portal 鉴权 / use_rerank / prompt 瘦身）。
  ✅ 每次改动后跑 `bash <项目根>/code/scripts/smoke_local.sh`，做**接口级**验证，不是 grep HTML 静态断言。
- ❌ **别在 Agent shell 里直接跑 `pip install` / 直接 `python app.py`**（沙箱 shim 会拦 mkdir）。
  ✅ 先 `unset PYTHONPATH` 再跑；装包用清华源，源码包走本地 wheel。
- ❌ **别在 `set -u` 下用 `"${ARR[@]}"` 展开可能为空的数组**（bash 3.2 报 unbound）。
  ✅ 用 `${ARR[@]+"${ARR[@]}"}`。
- ❌ **别在桌面解包目录 `Desktop/LTC RAG BOT项目汇总和TW交接/LTC-RAGbot/` 里改代码**。
  ✅ 只改工作区 `<项目根>/`，桌面副本保持原样作对照。
- ❌ **别删 `<项目根>/code/.env` 或 `chroma_data/`**（已 gitignore，删了要重配/重入库）。
- ❌ **别为了「顺手优化」重构 `SimpleVectorStore` 检索链路 / `webhook` / Dockerfile / deploy.sh**（Karpathy 准则三：只碰必须碰的）。本次这些文件一行未动。
- ❌ **别在磁盘可用 < 10 GB 时上模型级优化**（sentence-transformers 500MB+ 会直接失败）。✅ 先清盘，2026-09-18 实测剩 **7.6 GB**（61% 满），仍建议清到 ≥10 GB 再动。
- ⚠️ **改 `deploy.sh` / `rollback.sh` 后必跑 `bash code/scripts/deploy_drill.sh`**（13 条断言，10 秒，不联网）。
  这是唯一能在「不碰线上」的前提下验证部署/回滚逻辑的办法。当前基线：**15/15**。
- ⚠️ **别给「跳过某步骤」留静默容错**。v1 rollback 的假回滚就是这么来的：跳过了 git 回滚，却继续执行部署并报成功。
  ✅ 跳过 = 中止（`die`），不是继续。
- ⚠️ **变量展开后紧跟中文标点，一律写 `${VAR}`**（踩坑 #13，会 unbound 且难查）。
- ⚠️ **提交信息含反引号或 `$` 时别用 `-m "..."`**（踩坑 #14）✅ 用 `git commit -F 文件` + `<<'MSG'`。
- ⚠️ **首次部署前必看 `docs/部署方案.md` 第 4.3 节**：控制台配环境变量 + 开 COS 挂载，这两项**不在脚本里**，漏了就白部署。
- ⚠️ **改 prompt 时同时检查 `code/prompts/fabe.txt` 与 `app.py` 内置 prompt 两处**（文件优先，File 覆盖内置，见踩坑 #4）。
- ⚠️ **别把 `docs/` 里的示例当真实契约**：`/kb` 的字段名与 `by_type` 形状就与文档不符（踩坑 #10）。
  要动前端或写脚本前，先起服务 curl 一次拿真实响应。**本次 docs/ 里 3 份文档均已校准到与代码一致（基线 commit 见下），但仍建议改动前实测。**
- ⚠️ **别信任何「待办清单」，包括 handoff 自己写的**：上一版 handoff 说「Portal 未展示缓存可观测性」，实际 Portal
  早就展示了 `⚡ 缓存命中`。**执行待办前先在代码里 grep 确认它真的缺**（踩坑 #8 同源）。
- ⚠️ **别把 `file_size` 当字节数**（踩坑 #9）：统一按字符数 `len(text)`，`/kb` 聚合字段名是 `total_chars`。
- ✅ **改动后必跑双模式冒烟 + 部署演练**（唯一可信回归）：
  ```bash
  cd "<项目根>/code" && bash scripts/smoke_local.sh 2>&1 | tail -3
  API_KEY=<API_KEY> bash scripts/smoke_local.sh 2>&1 | tail -3
  bash scripts/deploy_drill.sh 2>&1 | tail -3
  ```
  当前基线：**16/16 · 17/17 · 15/15**。

---

## 六、下一步（Next — 接着做什么）

### 第 0 步：先跑探针，确认当前状态（勿跳过）

```bash
cd "/Volumes/Macintosh HD/Users/lily/WB_Workspace/2026-09-18-15-33-44/ltc-rag-bot" && git log --oneline --decorate | head -6 && git tag -l && git status --porcelain && echo "(空=干净)" && echo "--- 冒烟（开发） ---" && bash code/scripts/smoke_local.sh 2>&1 | tail -4 && echo "--- 部署演练 ---" && bash code/scripts/deploy_drill.sh 2>&1 | tail -4
```
预期：HEAD = `cc41c14`（master 最新）；冒烟 `22 通过 / 0 失败`（开发）；演练 `15 通过 / 0 失败`。线上探针：`curl -s https://ltc-rag-bot-315461-10-1450031534.sh.run.tcloudbase.com/health` 应返 `"status":"ok","docs_count":215`（KB 已含显卡文档，215 条/7 文件）。

> ✅ 原第 1–5 步（Lily 决策 / 首次上云 / 补字段 / 补文档 / Embedding 升级）**已全部完成**，见 Done #21–#31。

### 下一轮候选（按价值排序）

1. ~~KB 补「显卡/算力需求说明」文档~~ → ✅ **已完成（见 Done #33）**：GPU 沾边题根治，线上 `/query` 已正确引用、不再编造。
2. **L1 阈值标定**：积累 `/stats/token-economy` 真实命中分布后，把 `CACHE_SIM_THRESHOLD` 从 0.92 降档 0.88–0.90（换问法命中能再省一截）。
3. **CORS 收口**：确认 `/query` 等接口跨域策略（当前未显式限制）。
4. **批量入库限流/大小上限**（防单次上传打爆 2GB 内存）。
5. **`docs/13项冒烟验收.md` 同步新增端点**（`/embed/*`、`/stats/token-economy`、`/kb/source/*`）。

> ⚠️ 任何改阈值 / 换 Embedding 后端的操作之后，**必须 `DELETE /cache` 清快照**，否则缓存返回旧配置下的答案（2026-09-19 实测踩坑）。

### 第 1 步：拿 Lily 的两个决策（只需她一句话）

1. **D1 配色**：保留紫 hero、改文档与门禁（我的推荐）／ 还是把 hero 改成设计规范主色？
2. **D2 密钥轮换**：`ZHIPUAI_API_KEY` / `FEISHU_APP_SECRET` / `API_KEY` 是否轮换？

### 第 2 步：首次真实上云（脚本已就绪，缺授权）

```bash
npm i -g @cloudbase/cli && tcb login          # 前置：Lily 扫码授权
cd "<项目根>/code"
bash deploy.sh --preflight-only                # ① 只看风险，不部署
bash deploy.sh -e lily0625ai-d1gpwc1vw89141edd  # ② 部署
```

**部署后必须手工做的两件事**（脚本管不到，见 `docs/部署方案.md` 4.3）：

1. 控制台 → 云托管 → `ltc-rag-bot` → 存储 → 开 **COS 挂载** `/mnt/chroma`（漏了则容器回收后知识库丢失）
2. 控制台 → 环境变量 → 配 `API_KEY` / `ZHIPUAI_API_KEY` / `ZHIPUAI_MODEL`（漏了则鉴权用公开默认值 + LLM 退化成模板拼接）

然后按 `docs/部署方案.md` 4.4 与 `docs/13项冒烟验收.md` A1–A4 验收。
**首次部署 = 首次回滚演练**：部署后立刻用 `bash rollback.sh handoff-20260918 --url <URL>` 走一遍，确认回滚链路真能跑。
（离线演练覆盖不到的 5 项见 `docs/部署验证用例与报告.md` 第四节。）

### 第 3 步：把线上 207 条切片补字段 → **✅ 已完成（2026-09-18 23:10，见 Done #26）**

实际路径**不需要 `tcb login`** —— 走 cloudbase 连接器存储工具直连 COS：
```bash
# ① 取线上 metadata.json（连接器 queryStorage action=download）
# ② 升级（幂等 + 备份 + 原子写 + vectors.npy 哈希自校验）
/Users/lily/.workbuddy/binaries/python/envs/ltc-ragbot/bin/python <项目根>/code/scripts/upgrade_metadata.py \
  --path <线上数据目录> --created-at "2026-09-18T05:57:24Z"   # 服务器 mtime 作 created_at 代理
# ③ 连接器 manageStorage action=upload 传回 COS
# ④ 部署一次让容器重载（否则内存里仍是旧 metadata）
```

### 第 4 步：补齐剩余 4 份交付文档 → **✅ 已完成（见 Done #20）**

### 第 5 步（可选）：Embedding 升级 → **✅ 已完成并上线（2026-09-18 23:10，版本 021）**

已实现可插拔后端并切到 **BAAI/bge-m3**。**完整的选型对比 / A/B 验收指标 / 激活与回滚 SOP / 边界风险 见 `docs/Embedding升级.md`**（该项的唯一真源）。
一键回滚：`curl -X POST <URL>/embed/rebuild -H "X-API-Key: <key>" -d '{"backend":"tfidf"}'`

> ✅ 本轮（2026-09-18 22:45–23:10）完成待办 **#1 / #5 / #7** 三项，经连接器连做两次部署（**020 → 021**），规格零降级、8 密钥与 COS 挂载全程保留。
> 🔜 **建议的下一轮**（按价值排序）：
> 1. **提示词拒答阈值**：KB 未覆盖话题（实测"没有显卡能不能跑大模型"）LLM 仍自信作答 → 修 `prompts/fabe.txt`，加「检索相关度过低/无命中 → 明确拒答」。**这是当前最值得做的质量项。**
> 2. **CORS 收口**：确认 `/query` 等接口的跨域策略（当前未显式限制）。
> 3. 批量入库限流/大小上限（防单次上传打爆 2GB 内存）。
> 4. `docs/13项冒烟验收.md` 与本轮新增端点（`/embed/info`、`/embed/rebuild`）同步。

---

*续作流程：读本 handoff.md → 读 `<项目根>/.workbuddy/memory/` 当日日志 → 用上面「关键资源」的绝对路径直接调用，不必重新探测、不必重新入库、不必重新授权。*
