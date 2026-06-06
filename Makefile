.PHONY: build up down logs restart deploy-agent

# ── Docker 操作（树莓派 Bot）──────────────────────────────────────────

build:
	docker build -t ops-bot:latest .

up:
	@mkdir -p logs
	docker compose up -d
	@echo "Bot 已启动，查看日志: make logs"

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

restart:
	docker compose restart ops-bot

# ── VPS Agent 部署提示 ────────────────────────────────────────────────

deploy-agent:
	@echo ""
	@echo "=== VPS Agent 部署步骤 ==="
	@echo ""
	@echo "1. 上传 agent/ 目录到目标 VPS："
	@echo "   scp -r agent/ root@VPS_IP:/opt/ops-agent"
	@echo ""
	@echo "2. SSH 到 VPS 并运行安装脚本："
	@echo "   ssh root@VPS_IP"
	@echo "   cd /opt/ops-agent && bash deploy/install.sh"
	@echo ""
	@echo "3. 验证 Agent 运行正常："
	@echo "   curl -H \"Authorization: Bearer <token>\" http://VPS_IP:9000/health"
	@echo ""
	@echo "详细说明见 agent/deploy/README.md"
	@echo ""
