"""
AgentClient 单元测试（pytest 版本）
使用 AsyncMock 模拟 HTTP 调用，只测试格式化逻辑。
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from bot.client.agent_client import AgentClient

_NODE = {"host": "1.2.3.4", "port": 9000, "token": "tok", "label": "测试节点"}


def _client() -> AgentClient:
    return AgentClient(_NODE)


# ── call_status ───────────────────────────────────────────────────────

async def test_status_format():
    c = _client()
    data = {
        "cpu_percent": 45.2,
        "memory": {"used_gb": 2.1, "total_gb": 4.0, "percent": 52.5},
        "disk":   {"used_gb": 30.0, "total_gb": 80.0, "percent": 37.5},
        "uptime": "5天3小时20分钟",
    }
    with patch.object(c, "_get", new=AsyncMock(return_value=data)):
        result = await c.call_status()

    assert "测试节点" in result
    assert "45.2" in result
    assert "2.1 GB" in result
    assert "30.0 GB" in result
    assert "5天3小时" in result


# ── call_docker ───────────────────────────────────────────────────────

async def test_docker_format():
    c = _client()
    data = [
        {"id": "abc", "name": "nginx",   "status": "running", "image": "nginx:latest"},
        {"id": "def", "name": "trilium", "status": "running", "image": "zadam/trilium"},
    ]
    with patch.object(c, "_get", new=AsyncMock(return_value=data)):
        result = await c.call_docker()

    assert "测试节点" in result
    assert "nginx" in result
    assert "trilium" in result
    assert "nginx:latest" in result


async def test_docker_empty():
    c = _client()
    with patch.object(c, "_get", new=AsyncMock(return_value=[])):
        result = await c.call_docker()
    assert "无容器" in result


# ── call_services ─────────────────────────────────────────────────────

async def test_services_format():
    c = _client()
    data = [
        {"service": "nginx",   "active": True,  "status": "active"},
        {"service": "x-ui",    "active": False, "status": "inactive"},
    ]
    with patch.object(c, "_get", new=AsyncMock(return_value=data)):
        result = await c.call_services()

    assert "● 运行中" in result
    assert "○" in result
    assert "x-ui" in result


# ── call_logs ─────────────────────────────────────────────────────────

async def test_logs_format():
    c = _client()
    data = {"service": "nginx", "lines": 50, "logs": "Jun 05 GET / 200\n"}
    with patch.object(c, "_get", new=AsyncMock(return_value=data)):
        result = await c.call_logs("nginx", lines=50)

    assert "nginx" in result
    assert "50" in result
    assert "GET / 200" in result


async def test_logs_empty():
    c = _client()
    data = {"service": "nginx", "lines": 50, "logs": ""}
    with patch.object(c, "_get", new=AsyncMock(return_value=data)):
        result = await c.call_logs("nginx")
    assert "无日志输出" in result


# ── call_restart ──────────────────────────────────────────────────────

async def test_restart_success():
    c = _client()
    with patch.object(c, "_post", new=AsyncMock(return_value={"success": True, "message": "ok"})):
        result = await c.call_restart("nginx")
    assert "✅" in result
    assert "nginx" in result


async def test_restart_failure():
    c = _client()
    with patch.object(c, "_post", new=AsyncMock(return_value={"success": False, "message": "not found"})):
        result = await c.call_restart("nginx")
    assert "❌" in result
    assert "not found" in result


# ── 连接失败 ──────────────────────────────────────────────────────────

async def test_connection_error():
    c = _client()
    with patch.object(c, "_get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
        result = await c.call_status()
    assert "❌" in result
    assert "测试节点" in result
    assert "refused" in result
