# 归档运维 SOP + 静态资产清单 · LTC RAG Bot

> 版本：v1 · 2026-09-18
> 适用：项目交付归档、月度运维、故障后恢复、下线退役
> 配套：`docs/环境清单.md` · `docs/部署方案.md` · `docs/回滚预案.md` · `docs/上线巡检记录表.md`
> ⚠️ 资产清单的大小/行数为 **2026-09-18 实测**，改动代码后请同步刷新本表，否则归档会缺件。

---

## 一、静态资产清单

### 1.1 运行时资产（随镜像发布，Dockerfile COPY）

| 文件 | 大小 / 行数 | 入 git | 进容器 | 说明 |
|---|---|---|---|---|
| `code/app.py` | 60 KB / 1479 行 | ✅ | ✅ | 单文件 FastAPI，全部接口在此 |
| `code/portal/index.html` | 39 KB / 811 行 | ✅ | ✅ | 单文件 HTML；后端用 `{{AUTO_API_KEY}}` 注入 Key |
| `code/configs/kb_strategies.json` | 937 B | ✅ | ✅ | 分块策略配置 |
| `code/prompts/fabe.txt` | 466 B / 14 行 | ✅ | ✅ | **文件优先于 `app.py` 内置 prompt**（踩坑 #4） |
| `code/requirements.txt` | 168 B | ✅ | ✅ | 9 个依赖，无重模型 |

> Dockerfile 只 COPY 以上 4 项 + `requirements.txt`。**`scripts/`、`docs/`、`deploy.sh`、`rollback.sh`、`.env` 都不进容器。**

### 1.2 构建 / 交付资产（入 git，不进容器）

| 文件 | 大小 / 行数 | 说明 |
|---|---|---|
| `code/Dockerfile` | 1.6 KB | `python:3.11-slim`；HEALTHCHECK 30s/5s/15s/3 次；建 `/mnt/chroma` 与 `/data/chroma` |
| `code/cloudbaserc.json` | 269 B | **envId 唯一真源**；`envParams` 留空（密钥不入库） |
| `code/deploy.sh` | 9.2 KB / 196 行 | 部署；支持 `--preflight-only` 只检查不部署 |
| `code/rollback.sh` | 10.8 KB / 209 行 | 回滚；无效目标 exit 2、脏树 exit 3、自覆盖保护 |
| `code/scripts/smoke_local.sh` | 11.2 KB / 199 行 | 本地冒烟（dev 16 项 / prod 17 项） |
| `code/scripts/deploy_drill.sh` | 11.1 KB / 232 行 | 离线演练（15 断言，stub `tcb`，不碰线上） |
| `code/scripts/upgrade_metadata.py` | 8.0 KB / 206 行 | 旧切片补字段（幂等 + 备份 + 原子写 + 哈希自校验） |
| `code/render.yaml` | 257 B | 备用平台（Render）配置，**当前未启用** |

### 1.3 运行时数据（gitignored，必须单独备份）

| 文件 | 大小 | 位置（线上） | 说明 |
|---|---|---|---|
| `metadata.json` | 2 KB | `/mnt/chroma/` | 切片元数据（207 条基线） |
| `vectors.npy` | 2.5 KB | `/mnt/chroma/` | TF-IDF 向量矩阵 |
| `vocab.json` | 2.5 KB | `/mnt/chroma/` | 词表 |
| `answer_cache.json` | 6 KB | `/mnt/chroma/` | 高频问答快照缓存（可重建，**非关键**） |
| `.env` | 1.3 KB | 仅本地 | 密钥；**线上改在控制台**，`.dockerignore` 已排除 |

> ⚠️ 前 3 项是知识库本体，**丢了必须重新入库**。COS 挂载生效时它们在 `/mnt/chroma`；挂载失败会写到容器本地，实例回收即丢。

### 1.4 归档排除项（不发布、不备份）

| 项 | 原因 |
|---|---|
| `portal/index.html.bak` / `.bak.v2` / `.bak.v3` | `.gitignore` 的 `*.bak.*` + `.dockerignore` 双重排除 |
| `__pycache__/`、`*.pyc` | 编译产物 |
| `.git/`、`chroma_data/`（本地副本） | 分别被 `.dockerignore` / `.gitignore` 排除 |

### 1.5 文档资产（`docs/`，交付物的一部分）

`13项冒烟验收.md` · `22条踩坑完整清单.md` · `AnswerCache设计.md` · `CloudBase约束.md` · `Portal设计规范.md` · `metadata字段标准.md` · `回滚预案.md` · `cosine重排架构.md` · `省token7种手法.md` · `接手优化记录-20260918.md` · `部署方案.md` · `部署验证用例与报告.md` · `环境清单.md` · `上线巡检记录表.md` · `归档运维SOP与静态资产清单.md`

---

## 二、归档 SOP（每次发版后执行）

### 步骤 1：打版本标记（必做）

```bash
cd "<项目根>"
git status --porcelain        # 必须为空，有改动先提交
git tag opt-$(date +%Y%m%d)-<主题>     # 例：opt-20260918-envidfix
git tag -l                    # 确认新 tag 在列
```

命名约定沿用现有链：`handoff-20260918`（基线）→ `opt-20260918-p0fix` → `cachefix` → `docsync` → `deployfix` → `envidfix`。

### 步骤 2：确认回归全绿（必做，三探针）

```bash
cd "<项目根>/code"
bash scripts/smoke_local.sh 2>&1 | tail -3                       # 期望 16 通过 / 0 失败
API_KEY=<API_KEY> bash scripts/smoke_local.sh 2>&1 | tail -3   # 期望 17 通过 / 0 失败
bash scripts/deploy_drill.sh 2>&1 | tail -3                      # 期望 15 通过 / 0 失败
```

任一不为 0 失败 → **不许归档**，先修。

### 步骤 3：备份线上知识库（有 `tcb` 权限时）

```bash
# ① 从 COS / 挂载点把 3 个数据文件拉回本地归档目录
mkdir -p ~/ltc-ragbot-archive/$(date +%Y%m%d)
# ② 核对文件完整性（条数与本地基线一致，基线 207 条）
```

归档目录建议结构：

```
~/ltc-ragbot-archive/20260918/
├── code/            # 当前代码快照（git archive 导出）
├── chroma_data/     # metadata.json / vectors.npy / vocab.json
├── docs/            # 12+ 份交付文档
└── MANIFEST.txt     # 本清单
```

代码快照导出：

```bash
git archive --format=tar.gz --prefix=ltc-ragbot/ -o ~/ltc-ragbot-archive/20260918/code.tar.gz opt-20260918-envidfix
```

### 步骤 4：记录部署凭证

把本次 `DeployId`、服务 URL、部署时间、巡检结论追加进 `docs/部署验证用例与报告.md`，并填一份 `docs/上线巡检记录表.md` 第五节。

---

## 三、运维例行

| 周期 | 事项 | 判据 |
|---|---|---|
| **日** | 看 `/health` 是否 200、`/cache/stats` 的 `stale_count` 是否异常增长 | `stale_count` 持续涨 = 知识库被反复改动或指纹异常 |
| **周** | 跑一次 `deploy_drill.sh`；检查控制台实例重启次数 | 演练必须 15/15；重启次数 > 0 要查日志 |
| **月** | 备份知识库 3 文件；归档一次（第二节）；检查 COS 挂载仍生效 | store path 必须是 `/mnt/chroma` |
| **季度** | 复核密钥是否外泄、是否轮换（D2 待决）；复核 `docs/` 与代码是否漂移 | 见「避免」清单 |

---

## 四、恢复 SOP（知识库丢失时）

1. **确认丢失范围**：`GET /health` 的 `docs_count` 是否为 0；控制台日志看 store path。
2. **确认 COS 挂载**：云托管 → 存储 → 挂载路径必须是 `/mnt/chroma`；不对就改（改完要重新部署才生效）。
3. **恢复数据**：把归档的 `metadata.json` / `vectors.npy` / `vocab.json` 放回 `/mnt/chroma/`。
   - ⚠️ 三者必须**同版本配套**，只恢复 `metadata.json` 会导致向量与元数据错位（`upgrade_metadata.py` 有 `vectors.npy` 哈希自校验，可用来验证）。
4. **删除 `answer_cache.json`**（可选）：缓存快照带 `kb_fingerprint`，指纹变了会自动失效，但清掉更干净。
5. **验证**：`GET /health` 的 `docs_count` 回到 207；`POST /query` 能检索到已入库内容。
6. 若归档也没有 → 只能**重新入库**（这是 `docs/上线巡检记录表.md` 0-4 项要求确认 COS 挂载的原因）。

---

## 五、退役 / 下线 SOP

1. 导出最后一次完整归档（第二节全 4 步）。
2. 控制台：云托管 → `ltc-rag-bot` → 删除服务（先确认已归档）。
3. 清理 COS 桶：删除 `/mnt/chroma` 数据（注意：桶名含 envId `lily0625ai-d1gpwc1vw89141edd`，别删错环境）。
   - 残留的 `chroma.sqlite3`（196 KB，早期 ChromaDB 遗留）在此步一并清掉。
4. 轮换所有已上线过的密钥（`ZHIPUAI_API_KEY` / `FEISHU_APP_SECRET` / `API_KEY`），因为曾在 `项目汇总.md` 与交付包里明文出现过（D2 待决）。
5. 本地保留 git 仓与 tag 链，至少保留 6 个月。

---

## 六、交接检查表（交给下一个人时逐项勾）

- ☐ git tag 链完整，工作区干净（`git status --porcelain` 为空）
- ☐ 三探针全绿（16 / 17 / 15）
- ☐ 知识库 3 文件已备份，条数与 `docs_count` 对得上
- ☐ 7 份交付文档齐全（部署方案 / 环境清单 / 回滚预案 / 验证报告 / 巡检表 / 归档 SOP / 变更单）
- ☐ 线上环境变量清单已交接（**值不写在文档里**，走控制台或密码管理器）
- ☐ `handoff.md` 已更新到最新断点

---

*变更记录：2026-09-18 首版（六六🎋）。资产大小与行数为当日实测；本表应随代码改动同步刷新。*
