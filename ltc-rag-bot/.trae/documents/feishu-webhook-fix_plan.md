# 飞书机器人无答复根因修复计划

## 调研结论

### 截图 & 日志分析
- **截图1（CloudBase 运行时日志）**：只看到 URL verification challenge 和收到的字节数，**没有** `💬 处理消息` / `❌ RAG 查询失败` / `✅ 飞书回复成功` 等关键日志 → 说明飞书群的 @消息 webhook 处理链在中间某步静默失败了。
- **截图2（飞书群）**：王莹 @AI专家 发了 "产品怎么部署？"，机器人完全无响应。

### 公网接口实测
| 测试 | 结果 | 含义 |
|---|---|---|
| `GET /health` | `feishu_configured:true, api_key_mode:生产模式, docs_count:0` | FEISHU_APP_ID/SECRET 已配；API_KEY 已改非默认值（生产鉴权开启）；知识库空 |
| `POST /query`（带 dev key） | `401 Invalid or missing API Key` | 生产 API_KEY ≠ dev key |
| `POST /webhook`（模拟飞书事件） | `{"ok":true}` | webhook 端点可达，但不代表内部 RAG 调用成功 |

### 核心根因
**`app.py:1143` webhook 内部调用 `query()` 时，用 `_=True` 想绕过 API key 鉴权，但 verify_api_key 签名在生产模式下必须有匹配的 header，直接传 `True` 会被 FastAPI 的 Security 当成 "没有 header 值"，于是抛 401**。webhook 的 try/except 捕获了这个异常，所以 `reply = "⚠️ 查询出错: 401 ..."`，然后继续往下走飞书回复——但飞书回复那步大概率也是静默失败（因为 reply 里有 ⚠️ 但 webhook 没有日志输出飞书 API 调用结果）。

另外：
- **知识库为空**（docs_count=0）：即使鉴权修好了，也只能回 "知识库为空"
- **没配 LLM API key**（ZHIPU/SILICONFLOW）：修完后会走 template fallback 而不是真正 LLM

## 修复方案

### 文件变更

#### 1. `ltc-rag-bot/app.py` —— 修复 webhook 内部 RAG 调用（**本计划唯一代码变更**）

**当前代码**（第 1140-1151 行）：
```python
# 8. RAG 查询 —— 错误绕过方式
result = await query({"question": text}, _=True)
```

**改为**：在 webhook 内部直接调用 `_hybrid_search + call_llm`，完全跳过 FastAPI 路由层（不走 API key 鉴权），同时增加详细日志确保每步可追踪。

具体变更点：
- 将原 `/query` 端点中**业务逻辑**（混合检索 + prompt 组装 + call_llm）抽成独立函数 `_do_rag(question, top_k, use_bm25)`
- `/query` 路由变成 thin wrapper：`await verify_api_key() → _do_rag() → return JSONResponse`
- webhook 内部直接调 `_do_rag()`，跳过 FastAPI 依赖注入层
- **新增**：飞书 API 回复后的响应体完整日志（`code / msg / data`），当前代码只打成功/失败一句话

#### 2. CloudBase 环境变量 —— 需用户在控制台配置（**代码外**）

| 变量 | 值 | 说明 |
|---|---|---|
| `ZHIPU_API_KEY` | 用户的智谱 key | 让 call_llm 能出真 LLM 回复 |
| `SILICONFLOW_API_KEY` | 备用 | 智谱失败时 fallback |
| (已配) FEISHU_APP_ID / FEISHU_APP_SECRET | ✓ | health 已显示 configured |
| (可选) FEISHU_ENCRYPT_KEY | 若飞书后台开启加密 | |
| (可选) FEISHU_VERIFICATION_TOKEN | 若飞书后台开启 token | |

#### 3. 知识库数据 —— 需用户上传（**代码外**）

部署后通过 `/ingest` 上传产品文档，或使用 portal 前端上传。

### 实施步骤

1. **重构** `/query` 路由：抽 `_do_rag(question)` 独立函数，路由层只做鉴权 + 包装返回
2. **修改** webhook 第 8 步：`await query(..., _=True)` → `_do_rag(text)`，捕获异常保持不变
3. **增强** webhook 第 9 步飞书回复日志：打印 `resp` 完整 JSON，当前只打成功/失败一句话
4. **本地启动验证**：启动服务 → 模拟飞书事件 → 检查 webhook 处理链路是否完整走通（RAG + call_llm + 飞书回复调用）
5. **CloudBase 部署**：`tcb cloudrun deploy --force --wait`
6. **公网验证**：curl /health → 模拟 webhook 事件 → 查运行时日志

### 验证要点

| 步骤 | 验证方式 | 成功标志 |
|---|---|---|
| 本地启动 | `python app.py` → `/health` | `status: ok` |
| webhook 消息处理 | curl POST 模拟事件 payload | 返回 `{"ok":true}` + 控制台看到 `💬 处理消息` / `✅ 飞书回复` 或 `⚠️ RAG 查询失败` |
| CloudBase 部署 | `tcb cloudrun deploy` | `Deployment successful` |
| 公网 /health | curl 公网 URL | `status: ok` + 版本号 3.0 |
| CloudBase 运行时日志 | 控制台或 API 查 | 有 `[webhook] 💬` + `[webhook] ✅/❌` 日志 |
| 飞书端到端 | 用户在飞书群 @机器人 | 收到回复（哪怕是知识库为空） |

### 风险 & 降级

| 风险 | 处理 |
|---|---|
| 重构 `_do_rag` 时遗漏 prompt 组装逻辑 | 从 `/query` 当前代码原样抽出，不做任何逻辑改动，只改变调用路径 |
| `call_llm` 内部超时 30s 导致飞书端超时 | CloudRun 请求超时默认 60s，call_llm 超时 30s，叠加还有 RAG 检索时间，应该够。飞书也要求 webhook 3s 内返回——但当前代码是先返回 {"ok":true} 再异步处理（同步阻塞），实际上飞书不会等 RAG 完成。这个问题已存在，本次只修鉴权，异步化作为后续 |
| 智谱 API key 没配 | `_do_rag` 会走 template fallback，能返回但效果差。在 fallback 回复开头加 "【当前无 LLM，请配置 ZHIPU_API_KEY】" |

## 不在本计划内

- 知识库数据导入（需要用户提供文档）
- LLM API key 配置（需要用户在 CloudBase 控制台输入）
- webhook 异步化（飞书要求 3s 内返回，当前同步处理可能超时）
- 飞书事件加密支持（飞书后台没开加密就不用配；开了再加）
