---
name: ops-agent
description: 查询 VPS 服务器状态、容器、日志、服务，或重启指定服务（支持 VPS1 和 VPS2）
user-invocable: true
metadata: |
  {
    "openclaw": {
      "requires": {
        "env": ["VPS1_AGENT_URL", "VPS1_AGENT_TOKEN", "VPS2_AGENT_URL", "VPS2_AGENT_TOKEN"],
        "bins": ["curl"]
      }
    }
  }
---

# ops-agent

管理两台 VPS 服务器，通过 HTTP API 完成所有操作，使用 `bash` 工具运行 `curl`。

## 节点信息

| 节点 | 标签 | Base URL | Token 变量 |
|------|------|----------|------------|
| vps1 | ACCK_JP_VPS | `$VPS1_AGENT_URL` | `$VPS1_AGENT_TOKEN` |
| vps2 | dedirock_LA_VPS | `$VPS2_AGENT_URL` | `$VPS2_AGENT_TOKEN` |

用户未指定节点时默认查询 **vps1**；提到"VPS2"、"LA"、"dedirock"时查询 **vps2**；提到"所有"或"两台"时并行查询两台。

## 认证

所有请求携带 Bearer Token，以 vps1 为例：

```
Authorization: Bearer $VPS1_AGENT_TOKEN
```

## 可用端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| GET | /status | CPU / 内存 / 磁盘 / 运行时间 |
| GET | /docker | 容器列表（含状态）|
| GET | /services | 服务运行状态 |
| GET | /logs/{service}?lines=50 | 最近日志（默认 50 行）|
| POST | /restart/{service} | 重启服务 |

## 服务白名单

| 节点 | 允许操作的服务 |
|------|--------------|
| vps1 | xray、hysteria-server |
| vps2 | nginx、xray、hysteria-server、codex |

操作白名单外的服务名时，直接回复"该服务不在白名单内"，不发出请求。

## 调用规则

1. curl 必须加 `--noproxy '*'`，禁止走系统代理（agent 只接受直连）
2. 用 `bash` 工具执行 curl
3. 查看日志默认 50 行，用户可指定行数
4. 重启服务前先确认服务名在白名单，再 POST /restart/{service}
5. 返回结果用中文说明，JSON 原文用代码块包裹
6. API 返回错误时，显示 HTTP 状态码和错误信息

## curl 示例

```bash
# 查询 vps1 状态
curl -s --noproxy '*' -H "Authorization: Bearer $VPS1_AGENT_TOKEN" "$VPS1_AGENT_URL/status"

# 查询 vps2 状态
curl -s --noproxy '*' -H "Authorization: Bearer $VPS2_AGENT_TOKEN" "$VPS2_AGENT_URL/status"

# 查看 vps2 的 xray 日志（100 行）
curl -s --noproxy '*' -H "Authorization: Bearer $VPS2_AGENT_TOKEN" "$VPS2_AGENT_URL/logs/xray?lines=100"

# 重启 vps1 的 hysteria-server
curl -s --noproxy '*' -X POST -H "Authorization: Bearer $VPS1_AGENT_TOKEN" "$VPS1_AGENT_URL/restart/hysteria-server"

# 查询两台容器列表（并行）
curl -s --noproxy '*' -H "Authorization: Bearer $VPS1_AGENT_TOKEN" "$VPS1_AGENT_URL/docker" &
curl -s --noproxy '*' -H "Authorization: Bearer $VPS2_AGENT_TOKEN" "$VPS2_AGENT_URL/docker" &
wait
```
