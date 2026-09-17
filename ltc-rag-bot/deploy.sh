#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# LTC RAG Bot → CloudBase 云托管一键部署
# 前置: npm i -g @cloudbase/cli && tcb login
# 使用: bash deploy.sh
# ═══════════════════════════════════════════════════════════════════

set -e

echo "🚀 LTC RAG Bot → CloudBase 云托管"
echo "────────────────────────────────"

# 1. 检查 tcb CLI
if ! command -v tcb &> /dev/null; then
    echo "📦 安装 @cloudbase/cli ..."
    npm install -g @cloudbase/cli
fi

# 2. 登录（首次需要浏览器扫码）
if ! tcb login status &> /dev/null; then
    echo "🔐 请在浏览器中完成登录 ..."
    tcb login
fi

# 3. 选择 / 创建环境
echo "🌍 可用环境:"
tcb env:list
read -p "👉 输入环境 ID（回车自动选第一个）: " ENV_ID
ENV_ID="${ENV_ID:-$(tcb env:list --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["envId"])' 2>/dev/null)}"
echo "✅ 使用环境: $ENV_ID"

# 4. 部署
echo "📦 开始构建并部署 ..."
tcb cloudrun deploy --envId "$ENV_ID" --servicePath .

# 5. 显示结果
echo ""
echo "✅ 部署完成！"
echo "👉 到 CloudBase 控制台开启 COS 挂载（路径 /mnt/chroma）"
echo "👉 环境变量已在 cloudbaserc.json 里配置，后台可改"
echo "👉 你的服务 URL 在控制台里能看到"
