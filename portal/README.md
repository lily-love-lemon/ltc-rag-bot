# FDE Hub · 企业 AI 工程实战门户

> 前沿部署工程师 FDE 的个人项目展示门户 — 销售易 CRM × 飞书 × Trae Agent 的实战项目、PRD、技术方案。
> **访问**：https://your-env-id.tcloudbase.com（CloudBase 国内 CDN 生产地址）

---

## 🚀 快速开始

```bash
# 本地预览
cd portal
python3 -m http.server 8080
# 浏览器打开 http://localhost:8080/
```

## 📦 目录结构

```
portal/
├── index.html              ← 单页 SPA 入口（hash 路由）
├── css/style.css           ← 立体/玻璃/3D 按钮主题
├── js/
│   ├── data.js             ← 📌 项目注册表（加新项目改这里）
│   └── app.js              ← 路由 + MD 渲染 + 章节抽取
├── docs/                   ← 📌 所有项目 md 文档放这里
│   └── fde-project-implementation.md
├── assets/                 ← 图片 / Logo
├── vercel.json             ← Vercel PR 预览配置
└── .github/workflows/deploy-tcb.yml   ← GitHub Actions → CloudBase 自动部署
```

## ⚡ 以后加新项目只需 3 步

```bash
# 1️⃣ 写一个 new-project.md → 放进 portal/docs/
# 2️⃣ 在 js/data.js 的 PROJECTS 数组里追加:
#      { id: "new-project", docFile: "new-project.md", docSection: "## 章节标题", ... }
# 3️⃣ 提交 git push → GitHub Actions 自动部署 CloudBase
```

---

## ☁️ 部署：CloudBase + GitHub Actions（生产）

### 前置

1. **注册腾讯云开发（CloudBase）** → https://cloud.tencent.com/product/tcb
2. 创建免费环境（个人版），复制 **Environment ID**（形如 `prod-xxx`）
3. 在 CloudBase 控制台 → **静态网站托管** 开启，获取部署命令所需权限

### GitHub Secrets 配置

GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**：

| Secret | 值 | 在哪找 |
|--------|-----|--------|
| `TCB_ENV_ID` | CloudBase 环境 ID | 控制台首页 |
| `TCB_SECRET_ID` | 腾讯云 API 密钥 SecretId | https://console.cloud.tencent.com/cam/capi |
| `TCB_SECRET_KEY` | 腾讯云 API 密钥 SecretKey | 同上 |

### 工作流自动触发

push 到 `main` 分支 → GitHub Actions 自动：

1. 检出代码 → 校验核心文件完整
2. `npm install -g @cloudbase/cli`
3. `cd portal && tcb hosting deploy . -e $TCB_ENV_ID`
4. 完成！CloudBase CDN 自动刷新

### 手动触发（备选）

```bash
# 本地手动部署
cd portal
tcb login                    # 浏览器扫码
tcb hosting deploy . -e prod-xxx
```

### ⚠️ 重要提醒

**每 6 个月登录 CloudBase 控制台续期免费环境**，避免被回收导致站点下线。
设置一个日历提醒 📅

---

## 🧪 双部署方案（推荐）

| 目标 | 平台 | 用途 |
|------|------|------|
| **生产正式访问** | CloudBase | 国内 CDN 加速，用户访问稳定 |
| **PR 预览 / 开发** | Vercel | 自动绑 GitHub，每个 PR 生成独立预览链接 |

### Vercel PR 预览配置

1. https://vercel.com/new → 导入你的 GitHub 仓库
2. **Root Directory** 设为 `portal/`（因为门户在子目录）
3. Vercel 自动识别 `vercel.json`，配置好 rewrite 规则（SPA hash 路由兼容）
4. 之后每个 PR 都会自动生成 `deploy-preview-xxx.vercel.app` 链接

---

## 🎨 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 构建 | **纯静态 HTML/CSS/JS** | 零构建、零依赖、CloudBase/Vercel 都能直接托管 |
| 路由 | **Hash Router** | `#/project/xxx` 格式，CloudBase hosting 无需 rewrite |
| MD 渲染 | **marked.js**（CDN） | 支持 GFM + 代码块 |
| 代码高亮 | **highlight.js**（CDN） | github-dark 主题 |
| 立体效果 | **CSS3 transform + box-shadow** | 无 JS 依赖，卡片悬浮 + 按钮按下 + 矢量 SVG 头像 |
| 数据 | **data.js + docs/*.md** | 新项目 = 加一条 JS + 一个 md 文件 |

---

## 🔗 相关链接

- 门户生产地址：https://your-env-id.tcloudbase.com
- GitHub 仓库：https://github.com/your-name/fde-hub
- CloudBase 控制台：https://console.cloud.tencent.com/tcb
- Vercel 控制台：https://vercel.com/dashboard
- 销售易开发者中心：https://www.xiaoshouyi.com/developer

---

## 📄 License

MIT © 你的名字
