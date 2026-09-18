#!/bin/bash
# ─────────────────────────────────────────────────────────────
# FDE Skill · 一键安装
# 从 GitHub 克隆到 ~/.trae/skills/
# ─────────────────────────────────────────────────────────────
set -e

REPO_URL="${1:-https://github.com/<your-org>/rag-knowledge-base-skill.git}"
INSTALL_DIR="${HOME}/.trae/skills/rag-knowledge-base-skill"

echo "════════════════════════════════════════════════"
echo " FDE Skill · 一键安装"
echo " Repo:  $REPO_URL"
echo " Target: $INSTALL_DIR"
echo "════════════════════════════════════════════════"

mkdir -p "$(dirname "$INSTALL_DIR")"

if [ -d "$INSTALL_DIR" ]; then
  echo "⚠️  目录已存在，更新中..."
  cd "$INSTALL_DIR" && git pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

chmod +x "$INSTALL_DIR/_commands/"*.sh 2>/dev/null || true

echo ""
echo "✅ FDE Skill 已安装！"
echo "  路径: $INSTALL_DIR"
echo ""
echo "  下次 AI agent 会话自动识别并启用。"
echo "  或手动调用: SKILL_NAME=rag-knowledge-base-skill"
