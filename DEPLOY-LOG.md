# 部署日志 · Portal 公网发布

**时间**：2026-09-16
**目标**：FDE Hub 门户（`/workspace/portal`）发布到公网
**状态**：❌ 未完成，沙箱无凭据，本地执行方案 A 或 B

---

## 沙箱环境事实速查（下次先读）

| 项 | 值 |
|----|----|
| 代理 | `HTTP_PROXY=http://127.0.0.1:18080`（全部小写大写都设了） |
| npm CloudBase CLI | ✅ 已装 `@cloudbase/cli@latest` v3.8.2 |
| cloudflared | ✅ 已装 v2026.9.1 |
| ngrok | ✅ 已装 v3.39.11（但需 authtoken） |
| tcb 登录 | ❌ 无凭据，`tcb env:list` 报 `No valid identity information` |
| SSH 隧道 | ❌ 代理下 serveo.net / localhost.run port 22 timeout |
| trycloudflare API | ⚠️ HTTP 代理能 POST 成功拿到 tunnel JSON，但 cloudflared 二进制本身不读代理变量，tunnel 数据层连不上 |
| 腾讯云官网 | ❌ 直连 timeout（必须走代理） |
| 本地 http.server | ✅ `http://localhost:8080` 跑通，title="FDE Hub · 企业 AI 工程实战" |

---

## 下次部署最短路径（按优先级试）

### ✅ 方案 1：用户本地 tcb（推荐）

```bash
cd portal && tcb login && tcb hosting deploy . -e <TCB_ENV_ID>
```
或触发 GitHub Actions `.github/workflows/deploy-tcb.yml` 推 main。

### ⚠️ 方案 2：沙箱内 cloudflared（必须让它读代理）

cloudflared 不读标准 `HTTP_PROXY`，需要走 `.cloudflared/config.yml`：

```yaml
proxy:
  https_proxy: http://127.0.0.1:18080
  http_proxy: http://127.0.0.1:18080
```

然后：
```bash
cloudflared tunnel --config .cloudflared/config.yml --url http://localhost:8080
```

如果还是不行，试 `cloudflared tunnel --hello-world` 先测连性。

### ⚠️ 方案 3：直接 curl trycloudflare + 手动转发

```bash
curl -X POST https://api.trycloudflare.com/tunnel -x http://127.0.0.1:18080
# 拿到 hostname + secret 后，用 socat/nginx 手动做反向代理
# 或者直接让用户本地跑 cloudflared
```

### ❌ 已排除

- `ngrok`：要 authtoken，沙箱没
- SSH 隧道 (serveo / localhost.run)：port 22 被代理挡
- `lt localtunnel`：命令静默无输出，且官网已迁移
- `tcb login --flow device`：需要浏览器扫码，沙箱无浏览器
- Playwright：chromium 未安装

---

## 门户本地预览命令

```bash
cd /workspace/portal
python3 -m http.server 8080
# 打开 http://localhost:8080
# （SPA hash 路由 + fetch docs/*.md，file:// 协议不行，必须 http.server）
```

---

## 本次已完成的门户修改（防遗忘）

- ✅ Hero 卡片 + About 头像 → `assets/avatar-lily.png`（莲花图）
- ✅ `你的名字` → `Lily`（全站 6 处）
- ✅ Hero 头衔删后半段 `· 前 CRM 系统管理员`
- ✅ 引号：`懂 CRM 和不懂 AI 的人` → `懂代码和不懂 AI 的人`
- ✅ Hero 标签：`CRM实施/AI Agent/PMP/中/英双语` → `跨系统数字化/LTC AI化/AI新范式`
- ✅ About 标签：`CRM 实施专家` → `海内外 CRM`
- ✅ 时间线：`CRM系统管理员/发现AI机会/亲自下场/沉淀` → `留日硕士/前IBM·迪拜/独角兽公司·AI效能组/沉淀`
- ✅ 经历卡：`6年CRM实施/销售易落地` → `留日硕士/IBM迪拜/独角兽AI效能组`
- ✅ 全站 `销售易` → `海内外 CRM`（Footer/Hero描述/Props/Roadmap标题/Helpcard/同步桥接 8处）
- ✅ 故事区第一则改成 IBM 迪拜回忆
