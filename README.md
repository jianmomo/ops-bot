# Ops Bot — 轻量个人运维机器人

部署在树莓派 4B 上，同时支持 Telegram 和 QQ 两个平台，通过 HTTP Agent 管理多台 VPS 服务器。

**核心原则：稳定、轻量、长期运行、低成本。命令路由基于规则，禁止普通命令调用 LLM。**

---

## 功能特性

- **双平台**：Telegram Bot + QQ Bot（OneBot V11 直连，无需 NoneBot2）
- **多节点**：统一管理多台 VPS，命令可指定目标节点
- **权限控制**：用户白名单，平台隔离，拒绝未授权访问
- **服务管理**：查看状态、容器列表、实时日志、安全重启
- **AI 按需**：只有 `ai` 前缀命令才调用 LLM，其余全部规则路由
- **轻量部署**：Bot 跑 Docker，Agent 跑 systemd，无外部数据库依赖

---

## 架构

```
Telegram              QQ（OneBot V11）
    ↓                       ↓
TG Bot Handler        QQ Bot Handler
(python-telegram-bot) (websockets 直连)
         ↓
   Command Router（规则路由，无 LLM）
   Permission Manager（用户白名单）
         ↓
   Agent Client（httpx HTTP 调度）
      ↓          ↓
  VPS 1        VPS 2
  Agent        Agent
(FastAPI)    (FastAPI)
```

---

## 快速开始

### 前置要求

| 组件 | 说明 |
|------|------|
| 树莓派 4B | Ubuntu 22.04 ARM64，Docker 已安装 |
| VPS × N | 已有 systemd，Python 3.10+ |
| Telegram Bot | 从 @BotFather 获取 token |
| QQ 后端 | NapCat / LLOneBot / Lagrange（任选一） |

### 1. 配置

```bash
# 克隆项目
git clone <repo> ops-bot && cd ops-bot

# 复制并填写密钥
cp .env.example .env
nano .env          # 填入 TELEGRAM_BOT_TOKEN、QQ_WS_URL 等

# 按需修改主配置（用户白名单、节点 IP、服务列表）
nano config/config.yaml
```

### 2. 在目标 VPS 部署 Agent

```bash
# 在本地（树莓派）执行
scp -r agent/ root@VPS_IP:/opt/ops-agent

# SSH 到 VPS 运行安装脚本
ssh root@VPS_IP "cd /opt/ops-agent && bash deploy/install.sh"

# 验证
curl -H "Authorization: Bearer <your_token>" http://VPS_IP:9000/health
```

详细说明见 [agent/deploy/README.md](agent/deploy/README.md)。

### 3. 在树莓派启动 Bot

```bash
# 构建镜像并启动
make build
make up

# 查看日志
make logs
```

---

## 命令参考

| 命令 | 说明 | 示例 |
|------|------|------|
| `/status` | CPU / 内存 / 磁盘 / 运行时间 | `/status` |
| `/docker` | 容器列表及状态 | `/docker` |
| `/log <service>` | 最近 50 行日志 | `/log nginx` |
| `/restart <service>` | 重启白名单服务 | `/restart trilium` |
| `/services` | 所有服务运行状态 | `/services` |
| `ai <内容>` | AI 分析（调用 LLM） | `ai 分析最近日志` |

**可管理的服务**（在 `config.yaml` 中配置）：`nginx` / `trilium` / `x-ui`

---

## 配置说明

### config/config.yaml

```yaml
# 用户白名单（str 格式的 ID）
allowed_users:
  telegram:
    - "123456789"   # Telegram user_id（找 @userinfobot 获取）
  qq:
    - "987654321"   # QQ 号

# 允许重启的服务（白名单外的一律拒绝）
allowed_services:
  - nginx
  - trilium
  - x-ui

# 目标 VPS 节点
nodes:
  vps1:
    host: "1.2.3.4"
    port: 9000
    token: "${VPS1_AGENT_TOKEN}"   # 引用 .env 中的变量
    label: "主 VPS"
    services: [nginx, trilium, x-ui]

# AI 配置（仅 ai 命令使用）
ai:
  provider: anthropic
  model: claude-haiku-4-5-20251001
  max_tokens: 1000
```

### .env

| 变量 | 说明 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | BotFather 颁发的 token |
| `QQ_WS_URL` | OneBot 后端 WebSocket 地址，如 `ws://localhost:3001` |
| `QQ_HTTP_URL` | OneBot 后端 HTTP 地址，如 `http://localhost:3000` |
| `QQ_ACCESS_TOKEN` | OneBot 后端 access token（未设置则留空） |
| `VPS1_AGENT_TOKEN` | VPS1 Agent 的认证 token（自定义随机字符串） |
| `VPS2_AGENT_TOKEN` | VPS2 Agent 的认证 token |
| `ANTHROPIC_API_KEY` | Anthropic API Key（仅 ai 命令需要） |

---

## QQ 后端配置（以 NapCat 为例）

1. 在运行 QQ 的机器上安装 NapCat
2. 配置反向 WebSocket：
   ```json
   {
     "reverseWs": {
       "enable": true,
       "url": "ws://RASPI_IP:3001",
       "token": "your_access_token"
     },
     "httpServer": {
       "enable": true,
       "port": 3000,
       "token": "your_access_token"
     }
   }
   ```
3. 在树莓派 `.env` 中填入：
   ```
   QQ_WS_URL=ws://localhost:3001
   QQ_HTTP_URL=http://localhost:3000
   QQ_ACCESS_TOKEN=your_access_token
   ```

---

## 扩展指南 — 添加新命令（5 步）

以新增 `/disk` 命令为例：

**Step 1** — 在 Agent 添加数据接口（`agent/handlers.py`）：
```python
def get_disk_detail() -> dict:
    ...
```

**Step 2** — 在 Agent 添加路由（`agent/main.py`）：
```python
@app.get("/disk")
async def disk(_=Depends(verify_token)):
    return handlers.get_disk_detail()
```

**Step 3** — 在 AgentClient 添加调用方法（`bot/client/agent_client.py`）：
```python
async def call_disk(self) -> str:
    data = await self._get("/disk")
    return f"💾 磁盘详情 [{self._label}]\n..."
```

**Step 4** — 创建 handler（`bot/handlers/disk_handler.py`）：
```python
async def handle(args: str, node: str = "vps1") -> str:
    from bot.config import get_config
    from bot.client.agent_client import AgentClient
    return await AgentClient(get_config().get_node(node)).call_disk()
```

**Step 5** — 在 Router 注册（`bot/router.py`）：
```python
if cmd == "/disk":
    from bot.handlers import disk_handler
    return await disk_handler.handle("", node)
```

---

## Phase 2 规划

- [ ] **告警**：CPU / 内存 / 磁盘超阈值时主动推送（`config/alerts.yaml`）
- [ ] **服务监控**：定期检查服务存活，宕机自动告警
- [ ] **网站监控**：HTTP 可用性检查，响应时间告警
- [ ] **AI 增强**：拉取真实日志后交给 LLM 分析，带上下文

---

## 开发

```bash
# 运行测试
pytest tests/ -v

# 单独运行路由测试
python3 bot/router.py

# 单独运行权限测试
python3 bot/permissions.py
```
