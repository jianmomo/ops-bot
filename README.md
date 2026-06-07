# Ops Bot — 轻量个人运维机器人

部署在树莓派 4B 上，同时支持 Telegram 和 QQ 两个平台，通过 HTTP Agent 管理多台 VPS 服务器。

**双 Bot 架构：ops-bot 负责规则路由命令（轻量、无 LLM），OpenClaw 负责 AI 分析（@mention 触发）。**

---

## 快速开始

### 环境要求

| 组件 | 说明 |
|------|------|
| 树莓派 4B | Ubuntu 22.04 ARM64，Docker 已安装 |
| VPS × N | 已有 systemd，Python 3.10+ |
| Telegram Bot | 从 @BotFather 获取 token |
| QQ 后端 | NapCat（本项目已内置 docker-compose 配置）|

---

## 一、首次部署

### 1. 克隆项目

```bash
git clone <repo-url> ops-bot
cd ops-bot
```

### 2. 配置环境变量

```bash
cp .env.example .env
nano .env
```

需要填写的变量：

| 变量 | 说明 | 获取方式 |
|------|------|----------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot token | @BotFather → /newbot |
| `VPS1_HOST` | VPS1 公网 IP | 服务商控制台 |
| `VPS2_HOST` | VPS2 公网 IP | 服务商控制台 |
| `VPS1_AGENT_TOKEN` | VPS1 Agent 认证 token | 自定义随机串，与 VPS 端保持一致 |
| `VPS2_AGENT_TOKEN` | VPS2 Agent 认证 token | 同上 |
| `HTTPS_PROXY` | 本机代理（可直连 Telegram 则留空）| 如 `http://127.0.0.1:10809` |

### 3. 配置白名单和节点

```bash
nano config/config.yaml
```

- `allowed_users.telegram` → 填你的 Telegram user_id（发 /start 后在 `make logs` 里找）
- `allowed_users.qq` → 填你的 QQ 号
- `nodes.vps1.label` → VPS 显示名称（随意）
- `nodes.vps1.expire_date` → 到期日，格式 `YYYY-MM-DD`（留空不提醒）

### 4. 在目标 VPS 部署 Agent

```bash
# 上传 agent 目录
scp -r agent/ root@YOUR_VPS_IP:/opt/ops-agent

# SSH 到 VPS 安装
ssh root@YOUR_VPS_IP
cd /opt/ops-agent && bash deploy/install.sh

# 验证（将 YOUR_TOKEN 替换为 .env 里的 VPS1_AGENT_TOKEN）
curl -H "Authorization: Bearer YOUR_TOKEN" http://YOUR_VPS_IP:9000/health
```

详细说明见 [agent/deploy/README.md](agent/deploy/README.md)

### 5. 启动

```bash
make build   # 构建镜像（首次或代码修改后执行）
make up      # 启动所有服务
make logs    # 查看实时日志
```

---

## 二、QQ Bot 配置（NapCat）

NapCat 以 Docker 容器方式运行，与 ops-bot 共用 docker-compose。

### 首次登录

```bash
make up
docker logs napcat 2>&1 | grep "WebUi Token"
```

1. 浏览器访问 `http://树莓派IP:6099`，输入上面获取的 token
2. 扫码登录 QQ 账号（建议使用专用小号）

### 配置 WebSocket

登录后进入 **网络配置**：

1. 点击「添加」→ 选择「WebSocket 服务端」
2. 填写：Host `0.0.0.0`，Port `3001`，Token 留空
3. 保存 → 重启 NapCat

```bash
make napcat-restart
make logs   # 看到 "QQ Bot WebSocket 已连接" 即成功
```

### 会话持久化

QQ 登录状态保存在 `napcat/qq_data/`（已 gitignore），重启容器无需重新扫码。若扫码失效：

```bash
make napcat-restart
docker logs napcat 2>&1 | grep "WebUi Token"  # 重新获取 token 后扫码
```

---

## 三、切换 AI API（make api）

ops-bot 本身不调用 AI；AI 分析由 OpenClaw 处理。`make api` 管理 OpenClaw 使用的 AI 后端。

### 首次配置

```bash
nano config/api_profiles.yaml
# 填入 default_api_key 和 default_base_url（中转站地址）
```

### 日常使用

```bash
make api
```

交互菜单说明：

| 选项 | 功能 |
|------|------|
| 数字（1-N）| 切换到预设 Profile，自动测试并重启 OpenClaw |
| `d` | 动态查询中转站所有可用模型，关键词过滤后选择 |
| `t` | 测试当前配置（连接检测 + 模型对话验证）|
| `e` | 修改接入点 URL（自动探测 /v1 格式并修正）|
| `k` | 修改 API Key（修改后自动验证连通性）|
| `0` | 退出 |

### 新增模型

编辑 `config/api_profiles.yaml`，在 `profiles:` 下添加：

```yaml
- name: "自定义模型"
  model: "model-name"   # 中转站文档里的名字
  provider: "openai"    # 中转站统一用 openai
```

---

## 四、命令参考

| 命令 | 说明 | 示例 |
|------|------|------|
| `/status` | CPU / 内存 / 磁盘 / 运行时间 | `/status vps2` |
| `/info` | 节点详情 + 流量用量 + 到期倒计时 | `/info vps2` |
| `/docker` | 容器列表及状态 | `/docker` |
| `/log <service>` | 最近 50 行日志 | `/log xray` |
| `/restart <service>` | 重启白名单内服务 | `/restart hysteria-server` |
| `/services` | 所有服务运行状态 | `/services` |
| `/speedtest` | 带宽测速（约 2 分钟）| `/speedtest vps2` |

所有命令均可在末尾加节点名（`vps1` / `vps2`），默认操作 vps1。

---

## 五、常用运维命令

```bash
make build          # 重新构建 ops-bot 镜像
make up             # 启动所有服务（镜像有变更时自动重建容器）
make down           # 停止所有服务
make logs           # 实时日志（ops-bot + napcat）
make restart        # 仅重启 ops-bot
make napcat-log     # NapCat 实时日志
make napcat-restart # 重启 NapCat（扫码失效时）
make install-skills # 安装/更新 OpenClaw Skills
make api            # 切换 AI API 配置
make deploy-agent   # 查看 VPS Agent 部署步骤
```

---

## 六、告警配置

编辑 `config/alerts.yaml`，修改后执行 `make restart` 重载：

```yaml
thresholds:
  cpu_percent: 80      # 超过此值推送告警
  mem_percent: 80
  disk_percent: 80

expire_remind_days: [7, 3, 1]   # 到期前几天提醒
traffic_warn_percent: 80         # 月流量用量超此比例告警

websites:
  interval: 1800      # 检测间隔（秒）
  sites:
    - name: "我的网站"
      url: "https://example.com"
```

---

## 七、项目结构

```
ops-bot/
├── bot/                   # ops-bot 主服务
│   ├── handlers/          # 各命令处理器（status/docker/log/restart/info/speedtest）
│   ├── platforms/         # Telegram / QQ 平台适配
│   ├── client/            # VPS Agent HTTP 客户端
│   └── monitoring/        # 告警监控（资源/到期/流量/GFW/网站）
├── agent/                 # VPS Agent（部署到每台 VPS）
│   └── deploy/            # 安装脚本和 systemd 服务文件
├── openclaw/skills/       # OpenClaw AI Skill（ops-agent）
├── config/
│   ├── config.yaml        # 主配置（白名单、节点、服务）
│   └── alerts.yaml        # 告警阈值和监控目标
├── scripts/
│   └── switch_api.py      # AI API 切换脚本（make api）
├── .env.example           # 环境变量模板（复制为 .env 后填写）
└── docker-compose.yml     # ops-bot + NapCat
```

---

## 安全说明

- `.env` 含所有密钥，已 gitignore，**不要提交**
- `config/config.yaml` 中敏感字段使用 `${VAR}` 引用 `.env`
- `config/api_profiles.yaml` 含 AI API Key，已 gitignore
- `napcat/qq_data/` 含 QQ 会话数据，已 gitignore
- VPS Agent token 生成建议：`openssl rand -hex 32`
