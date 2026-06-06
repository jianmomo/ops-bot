"""
告警监控集成测试 (Phase 3)
mock AgentClient._get，验证告警/恢复/冷却逻辑。
"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bot.monitoring.monitor import AlertMonitor, _AlertState


# ── 测试用告警配置 ────────────────────────────────────────────────────

ALERTS_CFG = {
    "enabled": True,
    "interval": 60,
    "cooldown": 1800,
    "thresholds": {
        "cpu_percent": 80,
        "mem_percent": 80,
        "disk_percent": 80,
    },
    "nodes": ["vps1"],
}

NODE_CONFIGS = {
    "vps1": {
        "host": "10.0.0.1",
        "port": 9000,
        "token": "test-token",
        "label": "测试节点",
    }
}

STATUS_NORMAL = {
    "cpu_percent": 30.0,
    "memory": {"used_gb": 1.0, "total_gb": 4.0, "percent": 25.0},
    "disk": {"used_gb": 10.0, "total_gb": 100.0, "percent": 10.0},
    "uptime": "1天0小时0分钟",
}

STATUS_CPU_HIGH = {**STATUS_NORMAL, "cpu_percent": 90.0}
STATUS_MEM_HIGH = {
    **STATUS_NORMAL,
    "memory": {"used_gb": 3.5, "total_gb": 4.0, "percent": 87.5},
}
STATUS_DISK_HIGH = {
    **STATUS_NORMAL,
    "disk": {"used_gb": 85.0, "total_gb": 100.0, "percent": 85.0},
}


def _make_monitor(broadcast_calls: list[str]) -> AlertMonitor:
    """构造 AlertMonitor，用 mock platform 捕获广播内容"""
    mock_platform = MagicMock()
    mock_platform.platform_name = "mock"
    mock_platform.broadcast = AsyncMock(
        side_effect=lambda text, uids: broadcast_calls.append(text)
    )
    monitor = AlertMonitor(
        platforms=[(mock_platform, ["user1"])],
        node_configs=NODE_CONFIGS,
        alerts_config_path="/dev/null",  # 不读文件，手动注入
    )
    monitor._cfg = ALERTS_CFG
    return monitor


# ══════════════════════════════════════════════════════════════════════
# A. 消息格式
# ══════════════════════════════════════════════════════════════════════

class TestMessageFormat:

    def test_alert_cpu_format(self):
        now = datetime(2026, 6, 6, 20, 30, 0)
        msg = AlertMonitor._fmt_alert("主VPS", "cpu_percent", "CPU", 85.3, 80, now)
        assert "⚠️" in msg
        assert "[主VPS]" in msg
        assert "CPU告警" in msg
        assert "85.3%" in msg
        assert "80%" in msg
        assert "2026-06-06 20:30:00" in msg

    def test_recovery_mem_format(self):
        msg = AlertMonitor._fmt_recovery("主VPS", "mem_percent", "内存", 45.2, 12)
        assert "✅" in msg
        assert "[主VPS]" in msg
        assert "内存恢复正常" in msg
        assert "45.2%" in msg
        assert "12分钟" in msg

    def test_alert_offline_format(self):
        now = datetime(2026, 6, 6, 20, 30, 0)
        msg = AlertMonitor._fmt_alert("主VPS", "offline", "", 0, 0, now)
        assert "🔴" in msg
        assert "节点离线" in msg
        assert "2026-06-06 20:30:00" in msg

    def test_recovery_offline_format(self):
        msg = AlertMonitor._fmt_recovery("主VPS", "offline", "", 0, 5)
        assert "✅" in msg
        assert "节点恢复在线" in msg
        assert "5分钟" in msg


# ══════════════════════════════════════════════════════════════════════
# B. 告警触发
# ══════════════════════════════════════════════════════════════════════

class TestAlertTrigger:

    @pytest.mark.asyncio
    async def test_no_alert_when_normal(self):
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch.object(monitor, "_node_configs", NODE_CONFIGS):
            with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = STATUS_NORMAL
                await monitor._check_node("vps1")
        assert calls == []

    @pytest.mark.asyncio
    async def test_cpu_alert_triggered(self):
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_CPU_HIGH
            await monitor._check_node("vps1")
        assert len(calls) == 1
        assert "CPU告警" in calls[0]
        assert "90.0%" in calls[0]

    @pytest.mark.asyncio
    async def test_mem_alert_triggered(self):
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_MEM_HIGH
            await monitor._check_node("vps1")
        assert len(calls) == 1
        assert "内存告警" in calls[0]

    @pytest.mark.asyncio
    async def test_disk_alert_triggered(self):
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_DISK_HIGH
            await monitor._check_node("vps1")
        assert len(calls) == 1
        assert "磁盘告警" in calls[0]

    @pytest.mark.asyncio
    async def test_offline_alert_triggered(self):
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("connection refused")
            await monitor._check_node("vps1")
        assert len(calls) == 1
        assert "节点离线" in calls[0]


# ══════════════════════════════════════════════════════════════════════
# C. 冷却机制
# ══════════════════════════════════════════════════════════════════════

class TestCooldown:

    @pytest.mark.asyncio
    async def test_no_repeat_within_cooldown(self):
        """冷却期内第二次轮询不重复推送"""
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_CPU_HIGH
            await monitor._check_node("vps1")  # 第一次 → 推送
            await monitor._check_node("vps1")  # 第二次 → 冷却中，不推送
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_repeat_after_cooldown_expires(self):
        """冷却过期后再次推送"""
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_CPU_HIGH
            await monitor._check_node("vps1")  # 第一次 → 推送

        # 手动将 last_notified_at 回拨到冷却期之前
        key = ("vps1", "cpu_percent")
        monitor._states[key].last_notified_at = (
            datetime.now() - timedelta(seconds=ALERTS_CFG["cooldown"] + 1)
        )

        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_CPU_HIGH
            await monitor._check_node("vps1")  # 冷却到期 → 再次推送

        assert len(calls) == 2


# ══════════════════════════════════════════════════════════════════════
# D. 恢复通知
# ══════════════════════════════════════════════════════════════════════

class TestRecovery:

    @pytest.mark.asyncio
    async def test_recovery_notification_sent(self):
        """告警后恢复正常 → 发送恢复通知"""
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_CPU_HIGH
            await monitor._check_node("vps1")   # 告警

        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_NORMAL
            await monitor._check_node("vps1")   # 恢复

        assert len(calls) == 2
        assert "CPU告警" in calls[0]
        assert "CPU恢复正常" in calls[1]

    @pytest.mark.asyncio
    async def test_no_recovery_if_never_alerted(self):
        """从未告警过，恢复时不推送任何消息"""
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_NORMAL
            await monitor._check_node("vps1")
            await monitor._check_node("vps1")
        assert calls == []

    @pytest.mark.asyncio
    async def test_offline_then_online_recovery(self):
        """节点离线后重新上线 → 发送恢复通知"""
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("timeout")
            await monitor._check_node("vps1")   # 离线

        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_NORMAL
            await monitor._check_node("vps1")   # 上线

        assert len(calls) == 2
        assert "节点离线" in calls[0]
        assert "节点恢复在线" in calls[1]

    @pytest.mark.asyncio
    async def test_state_reset_after_recovery(self):
        """恢复后状态清空，若再次超阈值会重新告警"""
        calls: list[str] = []
        monitor = _make_monitor(calls)
        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_CPU_HIGH
            await monitor._check_node("vps1")   # 告警

        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_NORMAL
            await monitor._check_node("vps1")   # 恢复

        with patch("bot.client.agent_client.AgentClient._get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = STATUS_CPU_HIGH
            await monitor._check_node("vps1")   # 再次告警

        assert len(calls) == 3
        assert "CPU告警" in calls[2]


# ══════════════════════════════════════════════════════════════════════
# E. broadcast 平台路由
# ══════════════════════════════════════════════════════════════════════

class TestBroadcast:

    @pytest.mark.asyncio
    async def test_telegram_broadcast_uses_user_id(self):
        """Telegram broadcast 直接用 user_id 作 chat_id"""
        from bot.platforms.telegram_bot import TelegramPlatform
        platform = MagicMock(spec=TelegramPlatform)
        platform.reply = AsyncMock()
        platform.broadcast = TelegramPlatform.broadcast.__get__(platform)  # 绑定基类方法

        from bot.platforms.base import MessagePlatform
        platform.broadcast = MessagePlatform.broadcast.__get__(platform)
        await platform.broadcast("test msg", ["111", "222"])

        calls = [c.args for c in platform.reply.call_args_list]
        assert ("111", "test msg") in calls
        assert ("222", "test msg") in calls

    @pytest.mark.asyncio
    async def test_qq_broadcast_adds_private_prefix(self):
        """QQ broadcast 为每个 user_id 加 private_ 前缀"""
        from bot.platforms.qq_bot import QQPlatform
        platform = MagicMock(spec=QQPlatform)
        platform.reply = AsyncMock()
        platform.broadcast = QQPlatform.broadcast.__get__(platform)
        await platform.broadcast("test msg", ["200001", "200002"])

        calls = [c.args for c in platform.reply.call_args_list]
        assert ("private_200001", "test msg") in calls
        assert ("private_200002", "test msg") in calls
