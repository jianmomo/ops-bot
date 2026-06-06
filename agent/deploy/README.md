# Ops Agent 部署说明

每台目标 VPS 独立部署，直接运行在宿主机（非 Docker）。

**不用 Docker 的原因**：Agent 需要调用宿主机的 `systemctl` 和 `docker`，
容器内访问宿主 systemd 需要特权模式，存在安全风险。

---

## 前提条件

- Python 3.10+
- systemd（Ubuntu 20.04+ / Debian 11+）
- 防火墙已将 9000 端口限制为**仅树莓派 IP** 可访问

---

## 三步部署

### 1. 上传文件到 VPS

```bash
# 在树莓派 / 开发机上执行
scp -r agent/ root@VPS_IP:/opt/ops-agent
```

### 2. 运行安装脚本

```bash
ssh root@VPS_IP
cd /opt/ops-agent
bash deploy/install.sh
```

脚本会询问：
- `AGENT_TOKEN`：自定义随机字符串（在树莓派 `.env` 中填入相同值）
- `ALLOWED_SERVICES`：允许操作的服务（默认 `nginx,trilium,x-ui`）
- 监听端口（默认 `9000`）

### 3. 验证连通性

```bash
# 在 VPS 本机验证
curl -H "Authorization: Bearer <your_token>" http://localhost:9000/health
# 返回：{"status":"ok","host":"your-hostname"}

# 在树莓派验证（确保端口已开放）
curl -H "Authorization: Bearer <your_token>" http://VPS_IP:9000/health
```

---

## 日常维护

```bash
# 查看实时日志
journalctl -u ops-agent -f

# 查看最近 50 行日志
journalctl -u ops-agent -n 50 --no-pager

# 重启服务
systemctl restart ops-agent

# 修改配置（token / 服务列表 / 端口）
nano /etc/ops-agent/.env
systemctl restart ops-agent
```

---

## 防火墙配置（ufw）

```bash
# 只允许树莓派 IP 访问 9000 端口
ufw allow from RASPI_IP to any port 9000 proto tcp
# 拒绝其他来源
ufw deny 9000
ufw reload
```
