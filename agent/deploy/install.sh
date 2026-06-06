#!/usr/bin/env bash
# Ops Agent 安装脚本
# 用法：在 agent 目录下运行  bash deploy/install.sh
set -euo pipefail

# ── 基本检查 ────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && { echo "❌ 请以 root 运行此脚本 (sudo bash deploy/install.sh)"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_SRC="$(dirname "$SCRIPT_DIR")"   # agent/ 目录
INSTALL_DIR="/opt/ops-agent"
ENV_DIR="/etc/ops-agent"
SERVICE_NAME="ops-agent"

echo "========================================"
echo "  Ops Agent 安装程序"
echo "========================================"
echo "源目录: $AGENT_SRC"
echo "安装目录: $INSTALL_DIR"
echo ""

# ── 安装 Python 依赖 ────────────────────────────────────────────────────
echo "→ 检查 Python 环境..."
if ! command -v python3 &>/dev/null; then
    echo "  安装 python3..."
    apt-get update -q && apt-get install -y python3 python3-pip -q
fi

echo "→ 安装 Python 依赖..."
pip3 install -r "$AGENT_SRC/requirements.txt" -q

# ── 复制文件（仅当源目录不是安装目录时）─────────────────────────────────
if [[ "$AGENT_SRC" != "$INSTALL_DIR" ]]; then
    echo "→ 复制 agent 文件到 $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"
    cp -r "$AGENT_SRC/." "$INSTALL_DIR/"
fi

# ── 创建配置文件 ────────────────────────────────────────────────────────
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

echo ""
echo "── 配置 Agent ────────────────────────────"
read -rp "  AGENT_TOKEN（自定义随机字符串，Bot 用同一个值）: " TOKEN
if [[ -z "$TOKEN" ]]; then
    echo "❌ AGENT_TOKEN 不能为空"; exit 1
fi

read -rp "  ALLOWED_SERVICES（逗号分隔，回车默认 nginx,trilium,x-ui）: " SERVICES
SERVICES="${SERVICES:-nginx,trilium,x-ui}"

read -rp "  监听端口（回车默认 9000）: " PORT
PORT="${PORT:-9000}"

cat > "$ENV_DIR/.env" <<EOF
AGENT_TOKEN=$TOKEN
ALLOWED_SERVICES=$SERVICES
PORT=$PORT
EOF
chmod 600 "$ENV_DIR/.env"
echo "  → 配置已写入 $ENV_DIR/.env"

# ── 安装 systemd 服务 ────────────────────────────────────────────────────
echo ""
echo "→ 安装 systemd 服务 ($SERVICE_NAME)..."
cp "$INSTALL_DIR/deploy/ops-agent.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# ── 完成 ────────────────────────────────────────────────────────────────
echo ""
echo "✓ 安装完成！服务状态："
systemctl status "$SERVICE_NAME" --no-pager -l 2>&1 | head -12
echo ""
echo "验证命令："
echo "  curl -H \"Authorization: Bearer $TOKEN\" http://localhost:$PORT/health"
