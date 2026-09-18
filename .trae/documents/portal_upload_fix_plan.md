# Portal 上传失败修复 Implementation Plan

## Repository Research

### 问题现象
用户打开 Portal（`/` 或 `/portal`）后，在 📁 上传文档 tab 或输入文本 tab 上传 → 右上角 toast 显示 `✗ 401 {"detail":"Invalid or missing API Key"}`。

### 根因分析
| # | 发现 | 证据 |
|---|---|---|
| 1 | **后端 API 正常** | `curl -X POST /ingest-text -H "X-API-Key: lily-prod-key-2026"` → ✅ 成功 |
| 2 | **后端 API 鉴权有效** | 不带 API Key → `{"detail":"Invalid or missing API Key"}` |
| 3 | **COS 挂载生效** | `docs_count: 3`（curl 刚写入的数据持久化在 COS 上） |
| 4 | **Portal JS 逻辑正确** | `apiKey = localStorage.getItem('ltc_api_key')` → 有 key 才加 header |
| 5 | **Portal 无引导** | API Key 输入框藏在 `⚙️ 设置` tab 里，默认 tab 是 `📁 上传文档` |
| 6 | **placeholder 文案错误** | 写着"开发模式留空即可"但当前是**生产模式** |
| 7 | **错误提示不友好** | `✗ 401 {"detail":"..."}` 用户看不懂要去 Settings 填 key |

### 当前 Portal 架构
- `portal/index.html`（30363 bytes）静态文件，由 `app.py` 的 FileResponse 返回
- API Key 通过 `localStorage.setItem('ltc_api_key', ...)` 持久化
- `API_BASE = location.origin`（同源，无跨域问题）

## Files and Modules

| 文件 | 变更 |
|---|---|
| `app.py`（portal 路由 + `/health`） | portal 路由改为"模板渲染"：读取 HTML → 注入后端生产 API Key（从 `API_KEY` env）→ 返回；`/health` 增加 `need_api_key: true/false` + `api_key_hint` 字段 |
| `portal/index.html` | ① 生产模式+无 key 时顶部大红色 banner 提示 ② 401 错误 toast 自动跳转 Settings tab 并友好提示 ③ placeholder 动态切换 ④ 上传按钮在无 key 时禁用或提示 |

## Implementation Steps

### Step 1: 修改 `app.py` — portal 路由支持 API Key 模板注入

当前 portal 路由返回静态 `FileResponse(portal/index.html)`。改成：
- 读 HTML 文件内容为字符串
- 如果 `API_KEY` 环境变量存在且非默认值，替换 `{{AUTO_API_KEY}}` 占位符为实际 key
- 返回 `HTMLResponse(rendered_html)`

同时 `/health` 增加字段：
```json
{
  "need_api_key": true,  // 从 api_key_mode 判断
  "api_key_hint": "当前是生产模式（API_KEY 已设非默认值），Portal 上传/问答需要在 ⚙️ 设置 tab 填 API Key"
}
```

### Step 2: 修改 `portal/index.html` — 4 项 UX 增强

**① 顶部红色 banner（生产模式 + 无 key 时显示）**
```html
<div id="need-key-banner" class="banner" style="display:none">
  🔐 检测到当前是生产模式，但 API Key 未设置。
  <button onclick="switchTab('settings')">去 ⚙️ 设置 填 API Key →</button>
</div>
```
JS 逻辑：`loadStatus()` 读 `/health` → 如果 `need_api_key=true` 且 `!apiKey` → 显示 banner

**② `/health` 读取 `need_api_key` 并驱动 UX**
```js
const h = await api('/health', { noKey: true });
const needKey = h.need_api_key;
if (needKey && !apiKey) {
  document.getElementById('need-key-banner').style.display = 'block';
}
// 还可以自动填充 {{AUTO_API_KEY}}（如果后端注入了，首次打开自动有）
```

**③ 401 错误友好化**
在 `api()` 函数 catch 401 时：
```js
if (r.status === 401) {
  toast('🔐 API Key 缺失或错误，请先去 ⚙️ 设置 tab 填入', 'err');
  // 自动跳转到 settings tab
  document.querySelector('.tab[data-tab="settings"]').click();
  return;
}
```

**④ placeholder 动态 + 默认值**
API Key 输入框初始值：如果后端注入了 `{{AUTO_API_KEY}}` → 自动填充。否则 placeholder 从 `/health` 读取 `api_key_hint`。

### Step 3: 部署 + 冒烟测试

- 部署新版本到 CloudBase
- curl 验证 `/health` 新字段
- curl 验证 `/portal` 返回的 HTML 里是否注入了 API Key（如果有）
- 模拟用户场景：清空 localStorage → 打开 Portal → 检查 banner 是否显示
- 检查上传是否正常（有 key 时）

## Dependencies and Considerations

- **安全**：后端注入 API Key 到 HTML 意味着任何能访问 Portal 的人都能看到 key。内部使用场景可接受。如果未来要公网部署，需在 `API_KEY_IN_HTML=false` 环境变量控制。
- **CloudBase 容器重启**：修改 `app.py` + `portal/index.html` 后需要重新 build 和 deploy。
- **Portal 静态文件**：当前 Portal 是单 HTML 文件（30363 bytes），内嵌所有 CSS/JS。改完后需要整体替换。

## Validation

1. `/health` 返回 `need_api_key: true` + `api_key_mode` 生产模式
2. `GET /portal` 返回的 HTML 包含正确的 `{{AUTO_API_KEY}}` 注入值（如果 API_KEY env 已设）
3. `curl -X POST /ingest-text -H "X-API-Key: xxx"` → ✅ 成功；不带 key → 401
4. Portal 前端（浏览器打开）：如果是生产模式 + localStorage 无 key → 显示红色 banner；点击「去设置」→ 自动跳转到 settings tab
5. 带正确 API Key 上传 → ✅ 成功，toast 友好提示
6. COS 持久化验证：curl 写入 → 重启 → `/health` docs_count 保留

## Risks

- **API Key 泄露到前端**：低风险（内部使用）。如果要更安全，可以后续加 token 或 session 机制。当前需求是简单可用。
- **HTML 模板渲染性能**：可忽略（30KB 文件在首次请求读一次，CloudBase 容器会热缓存）。
- **用户浏览器缓存旧 Portal**：加 `?v=20260918` 版本查询参数或在 HTML 加 cache-control meta。
