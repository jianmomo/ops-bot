"""
AgentClient mock 测试：不启动真实 Agent，验证格式化输出。
运行：python3 test_agent_client.py
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from bot.client.agent_client import AgentClient

# ── 测试工具 ──────────────────────────────────────────────────────────

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results: list[bool] = []

def check(label: str, condition: bool, got: str = "") -> None:
    _results.append(condition)
    print(f"  [{PASS if condition else FAIL}] {label}")
    if not condition:
        print(f"         got: {got!r}")

_NODE = {"host": "1.2.3.4", "port": 9000, "token": "testtoken", "label": "测试节点"}

def make_client() -> AgentClient:
    return AgentClient(_NODE)

# ── 测试用例 ──────────────────────────────────────────────────────────

async def test_call_status():
    print("\n─── call_status() ───")
    client = make_client()
    mock_data = {
        "cpu_percent": 45.2,
        "memory": {"used_gb": 2.1, "total_gb": 4.0, "percent": 52.5},
        "disk":   {"used_gb": 30.0, "total_gb": 80.0, "percent": 37.5},
        "uptime": "5天3小时20分钟",
    }
    with patch.object(client, "_get", new=AsyncMock(return_value=mock_data)):
        result = await client.call_status()

    check("包含节点标签",    "测试节点" in result, result)
    check("包含 CPU 数值",   "45.2" in result,     result)
    check("包含内存信息",    "2.1 GB" in result,   result)
    check("包含磁盘信息",    "30.0 GB" in result,  result)
    check("包含运行时间",    "5天3小时" in result,  result)


async def test_call_docker():
    print("\n─── call_docker() ───")
    client = make_client()
    mock_data = [
        {"id": "abc123", "name": "nginx",   "status": "running", "image": "nginx:latest"},
        {"id": "def456", "name": "trilium", "status": "running", "image": "zadam/trilium:latest"},
    ]
    with patch.object(client, "_get", new=AsyncMock(return_value=mock_data)):
        result = await client.call_docker()

    check("包含节点标签",     "测试节点" in result, result)
    check("包含 nginx",       "nginx"   in result, result)
    check("包含 trilium",     "trilium" in result, result)
    check("包含镜像名",       "nginx:latest" in result, result)


async def test_call_docker_empty():
    print("\n─── call_docker() 空容器列表 ───")
    client = make_client()
    with patch.object(client, "_get", new=AsyncMock(return_value=[])):
        result = await client.call_docker()
    check("空列表提示", "无容器" in result, result)


async def test_call_services():
    print("\n─── call_services() ───")
    client = make_client()
    mock_data = [
        {"service": "nginx",   "active": True,  "status": "active"},
        {"service": "trilium", "active": True,  "status": "active"},
        {"service": "x-ui",    "active": False, "status": "inactive"},
    ]
    with patch.object(client, "_get", new=AsyncMock(return_value=mock_data)):
        result = await client.call_services()

    check("包含节点标签",    "测试节点" in result, result)
    check("运行中用●标记",   "● 运行中" in result, result)
    check("停止用○标记",     "○" in result,       result)
    check("包含 x-ui 状态", "x-ui" in result,    result)


async def test_call_logs():
    print("\n─── call_logs() ───")
    client = make_client()
    mock_data = {"service": "nginx", "lines": 50, "logs": "Jun 05 10:00:00 nginx[1234]: GET / 200\n"}
    with patch.object(client, "_get", new=AsyncMock(return_value=mock_data)):
        result = await client.call_logs("nginx", lines=50)

    check("包含节点标签",   "测试节点" in result, result)
    check("包含服务名",     "nginx" in result,   result)
    check("包含行数提示",   "50" in result,      result)
    check("包含日志内容",   "GET / 200" in result, result)


async def test_call_logs_empty():
    print("\n─── call_logs() 无日志 ───")
    client = make_client()
    mock_data = {"service": "nginx", "lines": 50, "logs": ""}
    with patch.object(client, "_get", new=AsyncMock(return_value=mock_data)):
        result = await client.call_logs("nginx")
    check("空日志提示", "无日志输出" in result, result)


async def test_call_restart_success():
    print("\n─── call_restart() 成功 ───")
    client = make_client()
    mock_data = {"service": "nginx", "success": True, "message": "重启成功"}
    with patch.object(client, "_post", new=AsyncMock(return_value=mock_data)):
        result = await client.call_restart("nginx")

    check("包含 ✅",       "✅" in result, result)
    check("包含服务名",    "nginx" in result, result)
    check("包含成功文案",  "重启成功" in result, result)


async def test_call_restart_failure():
    print("\n─── call_restart() 失败 ───")
    client = make_client()
    mock_data = {"service": "nginx", "success": False, "message": "Unit nginx.service not found"}
    with patch.object(client, "_post", new=AsyncMock(return_value=mock_data)):
        result = await client.call_restart("nginx")

    check("包含 ❌",       "❌" in result, result)
    check("包含失败原因",  "not found" in result, result)


async def test_connection_error():
    print("\n─── 连接失败 ───")
    client = make_client()
    with patch.object(client, "_get", new=AsyncMock(side_effect=httpx.ConnectError("Connection refused"))):
        result = await client.call_status()

    check("包含 ❌",       "❌" in result,    result)
    check("包含节点标签",  "测试节点" in result, result)
    check("包含错误信息",  "Connection refused" in result, result)


# ── 主入口 ────────────────────────────────────────────────────────────

async def main():
    await test_call_status()
    await test_call_docker()
    await test_call_docker_empty()
    await test_call_services()
    await test_call_logs()
    await test_call_logs_empty()
    await test_call_restart_success()
    await test_call_restart_failure()
    await test_connection_error()

    passed = sum(_results)
    total = len(_results)
    print(f"\n{'─'*44}")
    print(f"结果: {passed}/{total} 通过", "✓" if passed == total else "✗")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
