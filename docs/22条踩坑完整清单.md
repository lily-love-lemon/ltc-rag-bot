# 踩坑日志 · 22 条

> 全部在 LTC RAG Bot V4 项目中真实踩过。按项目阶段组织。
> 每条包含：**现象 → 根因 → 解法 → 预防**。

---

## Phase 0: 基线固化 / 回滚安全（3 条）

### 0.1 Git 没配置 user.email / user.name

- **现象**: `git commit` 报 `fatal: unable to auto-detect email address`
- **根因**: 全新环境，git 默认没有身份
- **解法**: `git config user.email "xxx" && git config user.name "xxx"`
- **预防**: Skill 首步检查 `git config user.email` 是否有值

### 0.2 回滚只靠 README 写的版本号

- **现象**: README 写了 "回滚到 DeployId 015"，但 git HEAD 和 DeployId 对不上
- **根因**: 没打 git tag，CloudBase 版本名也没记录
- **解法**: 先 `git tag v3-baseline` → 部署 → 记下 DeployId + RunId 到 rollback.sh
- **预防**: 固化为两步基线流程（git tag + rollback.sh）

### 0.3 CloudBase 部署是交互式的

- **现象**: 自动化脚本卡 `? Enable gray deployment?` 等待输入
- **根因**: 默认会问两次（确认 + 灰度选择）
- **解法**: `tcb cloudrun deploy --force 2>&1 <<< $'\n'`
- **预防**: `--force` + heredoc 是标配

### 0.4 CloudBase 网关从 sandbox 不可达

- **现象**: `tcb cloudrun run list` 报 `FetchError tcloudbasegateway.com`
- **根因**: sandbox egress 网络限制
- **解法**: deploy 本身走云端鉴权不受影响；URL 获取从 `record list` 拿 RunId 手动拼
- **预防**: 区分"部署提交"和"运行时查询"的网络通路

---

## Phase A: 后端 RAG 架构（8 条）

### 1.1 metadata 只存 4 字段

- **现象**: 前端做不了文件管理，没法按日期/类型分组
- **根因**: 最初 metadata 只有 id/source/text/strategy
- **解法**: 自动注入 `created_at` (ISO) / `file_type` (从文件名推断) / `file_size` (len(text))
- **预防**: 旧数据 `.get(..., None)` 向后兼容，不直接重构 metadata.json

### 1.2 BM25 和向量没有重排层

- **现象**: 只做 RRF 融合，召回精度不够
- **根因**: 以为 RRF 融合就够了
- **解法**: RRF 后加 **cosine 点积重排**（纯 numpy，零依赖）
- **预防**: 重排层是 RAG 标配，别省

### 1.3 AnswerCache 首次就返回缓存

- **现象**: 用户第一次问就收到了旧快照
- **根因**: 一开始没 PROMOTE_THRESHOLD，存了就返回
- **解法**: 改成 `hit_count >= 2` 才返回（第三次起）
- **预防**: 缓存必须有 promote 阈值，平衡新鲜度和省 token

### 1.4 COS 挂载可能晚于应用启动

- **现象**: 服务启动后第一次请求缓存时 `/mnt/chroma` 还没挂好
- **根因**: 容器启动时序不确定
- **解法**: `os.path.isdir("/mnt/chroma")` 检查，不存在 fallback 到本地 `chroma_data/`
- **预防**: 任何持久化路径都要有 fallback

### 1.5 list_chunks 不返回增强字段

- **现象**: 后端加了 metadata 字段，但 chunks_data 返回结构没变
- **根因**: 改了底层 store 但只改了一条读取路径
- **解法**: 统一用 `.get()` + 全链路检查（list_chunks 和 search 两条路径）
- **预防**: 改了底层 schema → 必须全链路 grep 检查

### 1.6 FABE prompt 让 LLM 写 4 段标题

- **现象**: 输出 40% 是冗余的 `**Feature:**` 这种 Markdown 标题
- **根因**: prompt 里显式要求按 F-A-B-E 分段标注
- **解法**: 瘦身版 prompt 去掉显式段落名，只让 LLM 按 FABE 结构组织但不标注
- **预防**: RAG prompt 里的"格式要求"尽量写在 system prompt，别占 context

### 1.7 jieba 首次加载 0.5s

- **现象**: 启动时间比预期长
- **根因**: 词表加载同步阻塞
- **解法**: 启动时 `_load()` 同步加载，忽略延迟（只发生一次）
- **预防**: 对启动慢的库，在 startup event 里 warm-up

### 1.8 Chromadb 在 CloudBase 免费版内存爆

- **现象**: 容器启动被 OOM kill（~128MB 极限）
- **根因**: Chromadb + embedding 模型 + numpy > 200MB
- **解法**: 砍掉所有第三方向量库 → 自实现 SimpleVectorStore（numpy + JSON，加载 <50MB）
- **预防**: CloudBase 免费版 **0.5核 128MB**，别用重型向量库

---

## Phase B: 前端 Portal（4 条）

### 2.1 切片筛选下拉硬编码

- **现象**: 后端已支持 source/strategy filter，前端下拉是空的 `<option value="">`
- **根因**: 写死了 `<option>` 而不是从后端拉
- **解法**: 加 `GET /chunks/options` 动态 API → 前端 init 时 fetch 填充
- **预防**: 前端筛选选项 **必须** 动态拿，别硬编码

### 2.2 CSS 截断覆盖后端 80→200 字

- **现象**: 后端改了 text_preview 到 200 字，但 CSS `.chunk-item` 固定宽度导致仍只 ~30 字
- **根因**: CSS `max-width` + `text-overflow: ellipsis` 二次截断
- **解法**: 移除 truncate CSS，让 `word-break: break-word` 自然展开
- **预防**: 预览长度改后端就够，别让 CSS 二次截断

### 2.3 配色替换残留旧紫

- **现象**: 10 处颜色值全局替换后，grep 发现 header 里漏了一处 `#7c3aed`
- **根因**: 手工替换容易漏
- **解法**: 替换后 `grep -c "#7c3aed\|#8b5cf6" portal/index.html` → 必须 = 0
- **预防**: 配色替换要有验收门禁（grep 零残留）

### 2.4 Portal 单文件 800 行 + 内嵌 CSS 全改

- **现象**: diff 时很难看出改了什么
- **根因**: 大文件增量改的 diff 噪声大
- **解法**: 整体重写而不是增量改；改完 node --check JS 语法
- **预防**: 当改动 >50% 时，整体重写比 patch 更干净

---

## Phase C: 部署 + 验证（4 条）

### 3.1 部署进度查询慢

- **现象**: 构建期间 `record list` 只显示 `creating`，需要等 90-120s
- **根因**: CloudBase 镜像构建本身需要时间
- **解法**: `sleep 60` + 轮询，直到 Status = normal 且 Traffic ratio = 100
- **预防**: 别假设 deploy 提交后立刻可用

### 3.2 部署 URL 拿不到

- **现象**: `tcb cloudrun service get` 在 sandbox 里报 FetchError
- **根因**: sandbox egress 网络限制
- **解法**: 用 `record list` 拿 RunId 手动拼 URL，或直接看 tcloudbase 控制台
- **预防**: deploy 成功 ≠ 能即时 curl 验证，留后手

### 3.3 本地冒烟旧数据残留

- **现象**: 第二次跑 ingest 时旧数据还在，验证脏了
- **根因**: 没清 chroma_data 目录
- **解法**: 冒烟前 `rm -rf chroma_data/* && rm -f /mnt/chroma/*.json`
- **预防**: 冒烟脚本首步必清数据

### 3.4 API Key 开发模式自动跳过

- **现象**: `.env` 里没改 API_KEY 时后端自动跳过鉴权
- **根因**: 为了开发方便做的 fallback 太宽松
- **解法**: 线上部署前必须把 API_KEY 改掉并设 `STRICT_AUTH=true`
- **预防**: API Key 是验收门禁项

---

## 架构级额外踩坑（3 条）

### A CloudBase 免费版内存极限 ~128MB

- **根因**: 砍 Chromadb → 自实现 SimpleVectorStore（numpy + JSON < 50MB）
- **结论**: CloudBase free tier 别指望跑 LangChain + Chroma + embedding 三件套

### B COS 挂载异步化

- **根因**: `/mnt/chroma` 可能比应用启动晚挂载
- **解法**: 启动时 `os.path.isdir` 检查 + 本地 fallback

### C 飞书 webhook 签名验证

- **根因**: 线上被打 → 必须 ENCRYPT_KEY + VERIFICATION_TOKEN
- **解法**: `_feishu_decrypt` AES-256-CBC + `_is_duplicate_event` 内存去重
