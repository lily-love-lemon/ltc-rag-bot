---
name: "cloudbase-deploy"
description: "Deploys static sites to Tencent CloudBase Hosting. Invoke when user asks to publish a site, get a public URL, or deploy to CloudBase/tcloudbase.com hosting."
---

# CloudBase 静态网站部署 Skill

从零到公网 URL 的完整 CloudBase（腾讯云开发）静态托管部署流程。

---

## 适用场景

- 用户说"发布到公网"、"上线网站"、"给个公网链接"、"部署到 CloudBase/tcloudbase"
- 用户截图显示 CloudBase 控制台（带「静态网站托管」标签）
- 用户提到环境 ID 形如 `xxxx-xxxxx`、Region、体验版、正式版

**遇到以上任意一种，立即启用本 Skill。**

---

## 四步部署流程

### 步骤 1：安装 + 登录（只做一次）

```bash
npm install -g @cloudbase/cli
```

**登录（关键）**：
```bash
tcb login --flow device
```
输出形如：
```
https://tcb.cloud.tencent.com/dev#/cli-auth?user_code=XXXX-XXXX&from=cli&flow=device
用户码: XXXX-XXXX
```

**必须让用户在自己的浏览器打开该链接 + 输入用户码 + 点「确认授权」。**

> ⚠️ **不要用 web flow**（`--flow web`）——它在本地起 127.0.0.1:9012 回调，沙箱里 9012 端口不可达。
> ⚠️ 登录是**交互式等待**，用 `blocking: false` 启动后轮询状态。

### 步骤 2：确认环境 ID 和 Region（不要猜！）

**用户截图里的环境 ID 不能直接信**——经常抄错或少打字符。部署前必须用 CLI 查一次：

```bash
# 先查上海（默认）
tcb env list -r ap-shanghai

# 再查新加坡（备选）
tcb env list -r ap-singapore
```

**关键信息**：从输出表格里精确复制：
- **Environment ID**（复制全部字符，不要省略）
- **Region**（ap-shanghai 或 ap-singapore）
- **Status**（必须是 Normal 才能部署）

### 步骤 3：部署

```bash
# 最简单（静态站）
tcb hosting deploy <目录路径> -e <ENV_ID> -r <REGION>
```

**典型目录结构**：
```
├── index.html          # SPA 入口
├── css/
├── js/
├── assets/             # 图片、字体等
└── docs/               # Markdown 文档（fetch 加载）
```

> SPA 需要在 CloudBase 控制台配置「**默认文档**」为 `index.html`（hash 路由无需 rewrite）。

**输出示例**（成功标志）：
```
✔ File upload succeeded
ℹ Visit website: https://<env-id>-数字.tcloudbaseapp.com
```

### 步骤 4：拿到公网 URL

从输出里提取 `Visit website: https://...` 那个链接，返回给用户。

---

## 高频踩坑清单（按出现频率排序）

| # | 现象 | 根因 | 解决方案 |
|---|------|------|----------|
| 1 | `Env xxx not exist in your account` | **环境 ID 打错**（截图抄错或少打字符） | 必须用 `tcb env list -r <region>` 查，**不要相信截图里的 ID** |
| 2 | `Current region: ap-shanghai ... If not in current region, specify -r` | Region 不对 | 两个 region 都查一遍：`-r ap-shanghai` 和 `-r ap-singapore` |
| 3 | `No valid identity information` | **未登录**或登录进程被杀 | 重新 `tcb login --flow device`，等用户授权完再继续 |
| 4 | `tcb: command not found` | CLI 未装或路径不对 | `npm install -g @cloudbase/cli` 后 `tcb --version` 验证 |
| 5 | 部署成功但页面显示旧内容 | CDN 缓存 | 告诉用户「无痕窗口打开」或发 `curl -H "Cache-Control: no-cache" <url>` 给用户自测 |
| 6 | `File upload failed: [DescribeStaticStore]` | 登录刚过期 / 代理超时 | 重跑 `tcb login`，确认授权码后再 deploy |
| 7 | 沙箱里 cloudflared / ngrok / SSH 隧道都不通 | 沙箱强制 HTTP 代理 + 出网限制 | **放弃隧道，直接用 tcb**——tcb 走 curl 代理链路可通，这是最快的公网方案 |
| 8 | tcb login 等 Y/n 输入时被杀 | 进程等待 stdin 被 kill | 即使进程被 kill，**凭据通常已写入**，可以直接 deploy 试一下 |

---

## 快速诊断命令（按顺序跑）

```bash
# 1. CLI 是否可用
tcb --version
# 2. 是否已登录
tcb env list -r ap-shanghai 2>&1 | head -5
# 3. 两个 region 都查
tcb env list -r ap-singapore 2>&1 | tail -10
# 4. 部署（正确的 ENV_ID + REGION）
tcb hosting deploy ./portal -e <ENV_ID> -r <REGION>
```

---

## 完整示例（本次 FDE Hub 部署）

```bash
# 用户已授权后，直接跑
cd /workspace/portal
tcb hosting deploy . -e lily0625ai-d1gpwc1vw89141edd -r ap-shanghai

# 输出（截取关键部分）：
# ✔ File upload succeeded
# ℹ Visit website: https://lily0625ai-d1gpwc1vw89141edd-1450031534.tcloudbaseapp.com
```

---

## CloudBase vs 其他公网隧道

| 方案 | 沙箱可用 | 持久化 | 用户需要做什么 |
|------|----------|--------|----------------|
| **tcb hosting deploy** | ✅ curl 代理通 | ✅ 永久 | 浏览器点一次授权 |
| cloudflared quick tunnel | ❌ Go 二进制不认代理 | ❌ 重启失效 | 无 |
| ngrok | ❌ 要 authtoken | ❌ 重启失效 | 注册 + 复制 token |
| SSH (serveo) | ❌ 22 端口被挡 | ❌ 重启失效 | 无 |
| Vercel | ✅ npm | ✅ | 要有 GitHub |

**结论**：只要用户有 CloudBase 环境，`tcb` 是沙箱里唯一可靠的公网发布路径。
