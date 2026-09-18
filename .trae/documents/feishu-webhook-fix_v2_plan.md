# 飞书机器人无答复 · 根因重新诊断 & 修复计划

## 🔴 关键纠正：我之前的根因分析是错的

### 之前错误的根因
> "webhook 里 `await query(body, _=True)` 在生产模式下会触发 401" ← **此结论错误**

### 精确验证（Python 实验）
直接调用 FastAPI 路由函数 `await test_endpoint(body, _=True)` 时，**FastAPI 的依赖注入层完全不会介入**。`Depends(verify_api_key)` 只在 HTTP 请求经由 FastAPI 路由层处理时才会自动触发。直接调用函数时，`_` 就是你传的 `True`，`verify_api_key()` 根本不会被执行。

```python
# 实验结果：
await test_endpoint({"q": "hi"}, _=True)  # ✅ 直接返回 True，verify_api_key 没被调用
await test_endpoint({"q": "hi"})           # ✅ _ 拿到 Depends 对象，verify_api_key 也没被调用
# 只有走 HTTP 请求时 verify_api_key 才会被触发
```

**结论：原来的代码逻辑 `await query(body, _=True)` 是正确的，不会触发 401。我之前的 `_do_rag()` 重构是多余的——不过既然已经改了且验证过能跑，就保留也无妨。**

---

## ✅ 真正的根因定位（三重叠加）

### 根因 1（最关键）：飞书群消息根本没投递到 webhook
**证据**：CloudBase 运行时日志截图里只有 URL verification challenge，没有任何 `💬 处理消息` / `⏭️` / `❌` 等 webhook 处理日志。如果飞书群消息真的到达了 webhook，哪怕后面失败了，至少应该有「收到 N bytes」+ `📋 event=...` + `💬 处理消息` 这几行日志。

**可能原因**（需要用户在飞书开放平台确认）：
- ❌ 飞书事件订阅里没加 `im.message.receive_v1` 事件
- ❌ 回调 URL 填错了（但 challenge 通过了说明 URL 是对的）
- ❌ 飞书应用没发布（处于开发/自测模式）
- ❌ 飞书应用在群里不是"机器人"身份
- ❌ 飞书应用没有 `im:message` 和 `im:chat` 权限
- ⚠️ 飞书后台开了加密但 CloudBase 没配 `FEISHU_ENCRYPT_KEY`（加密消息会被忽略，payload 解析为空结构 → 被跳过但应该有 `⏭️` 日志）

### 根因 2（硬伤）：FEISHU_APP_SECRET 无效
**证据**：用 CloudBase 截图中的 `FEISHU_APP_ID=cli_aac61d0e793a5cfa` + `FEISHU_APP_SECRET=i9zJyCDUiy6qsn3h3FricwSV5bejVhN` 直接调飞书 token API 返回：
```json
{"code": 10014, "msg": "app secret invalid"}
```
CloudBase 控制台显示的 `FEISHU_APP_SECRET` 值可能是被遮罩/截断后显示的，或已过期/被重置。**即使消息到达 webhook，飞书回复这一步也会因为拿不到 token 而失败**。

### 根因 3（代码 Bug）：ZHIPU 变量名不匹配
**证据**：
| 位置 | 变量名 |
|---|---|
| CloudBase 环境变量 | `ZHIPUAI_API_KEY=8fa78348dcfb...` |
| CloudBase 环境变量 | `ZHIPUAI_MODEL=glm-4-flash` |
| 代码 `call_llm()` 读取 | `ZHIPU_API_KEY`（没有 AI 后缀） |
| 代码 `call_llm()` 读取 | `ZHIPU_MODEL`（没有 AI 后缀） |

变量名不匹配 → `ZHIPU_API_KEY` 读到空字符串 → 跳过智谱 → 继续看 `SILICONFLOW_API_KEY` → 也是空的 → 走 `OLLAMA_BASE_URL=localhost:11434` → CloudBase 容器里没有 Ollama → 走 **template fallback**。**即使飞书回复能发出去，内容也是模板 fallback 而不是真正的 LLM 回复**。

---

## 修复方案

### 文件变更

#### 1. `ltc-rag-bot/app.py` —— 修复 ZHIPU 变量名兼容（**唯一代码变更**）

让 `call_llm()` 同时支持 `ZHIPU_API_KEY` 和 `ZHIPUAI_API_KEY`，`ZHIPU_MODEL` 和 `ZHIPUAI_MODEL`：

```python
zhipu_key = os.environ.get("ZHIPU_API_KEY") or os.environ.get("ZHIPUAI_API_KEY", "").strip()
zhipu_model = os.environ.get("ZHIPU_MODEL") or os.environ.get("ZHIPUAI_MODEL", "glm-4-flash").strip()
```

硅基流动同理（以防 CloudBase 那边也用了不常见的命名）：
```python
sf_key = os.environ.get("SILICONFLOW_API_KEY") or os.environ.get("SF_API_KEY", "").strip()
sf_model = os.environ.get("SILICONFLOW_MODEL") or os.environ.get("SF_MODEL", "deepseek-ai/DeepSeek-V3").strip()
```

**同时保留**已经做的 `_do_rag()` 重构（虽然不是必须的，但让 webhook 跳过 FastAPI 依赖层的逻辑更干净，且已经验证过能跑）。

### CloudBase 环境变量变更 —— **需用户操作**

| 操作 | 详情 |
|---|---|
| ✏️ **修正** `FEISHU_APP_SECRET` | 去飞书开放平台 → 应用 → 凭证与基础信息，复制最新的 App Secret，替换 CloudBase 上当前无效的值 |
| ✏️ **确认** `ZHIPUAI_API_KEY` 名保留或统一 | 代码修复后两种名都能识别，不必动；或者统一改成 `ZHIPU_API_KEY` 让两边一致 |
| ✏️ **添加** `SILICONFLOW_API_KEY`（可选） | 当前为空，作为智谱失败的 fallback |
| ⚠️ **删除** `API_KEY` 的生产值？ | 不需要——当前是生产模式，API_KEY 鉴权是对的。webhook 已经用 `_do_rag()` 绕过了，公开的 `/query` 接口也应该保持鉴权 |

### 飞书开放平台配置确认 —— **需用户操作**

| 步骤 | 检查 |
|---|---|
| 1 | 进入飞书开放平台 → 你的应用 → **事件订阅** |
| 2 | 确认**请求 URL** 是 `https://ltc-rag-bot-315461-10-1450031534.sh.run.tcloudbase.com/webhook` |
| 3 | 检查下方**事件列表**里有没有 `im.message.receive_v1`（消息接收）→ 没有就添加 |
| 4 | 如果开了**加密**或**Verification Token** → 记下值，CloudBase 也要配 `FEISHU_ENCRYPT_KEY` / `FEISHU_VERIFICATION_TOKEN` |
| 5 | 进入**权限管理** → 确保已申请 `im:message`（获取与发送单聊、群组消息）和 `im:chat`（获取群组信息）权限 |
| 6 | 进入**版本管理与发布** → 如果还是"开发中"状态，需要发布一个版本才能让群聊消息被投递 |
| 7 | 打开飞书群 → 群设置 → 群机器人 → 确认 AI专家 机器人已在群里且是"可用"状态 |

---

## 实施步骤

### 代码侧（可自动执行）
1. 修复 `call_llm()` 里 ZHIPU / SILICONFLOW 环境变量名兼容
2. 本地启动 + 模拟 webhook 事件，确认 ZHIPUAI_API_KEY 能被识别
3. CloudBase 部署

### 用户侧（需手动操作）
1. **飞书开放平台**：确认事件订阅、权限、发布状态
2. **CloudBase 控制台**：修正 FEISHU_APP_SECRET
3. 飞书群里 @机器人 发消息测试

### 验证
1. curl `/health` → 版本号 3.0 + feishu_configured: true
2. 模拟 webhook 事件 → 查看完整日志链路
3. 飞书群 @机器人 → 收到回复（哪怕是知识库为空）

## 风险
- FEISHU_APP_SECRET 可能需要用户从飞书开放平台重新生成后复制，CloudBase 控制台可能有值长度限制
- 飞书事件订阅的 `im.message.receive_v1` 如果没加，加完后可能需要等几分钟才能生效
- 如果飞书开了加密（最容易被忽略的配置），CloudBase 必须加 `FEISHU_ENCRYPT_KEY`，否则所有加密消息都会被忽略
