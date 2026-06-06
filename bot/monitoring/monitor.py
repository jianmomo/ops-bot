"""
告警监控模块 — Phase 3

每 interval 秒轮询各节点 /status，超阈值时推送告警，恢复后推送恢复通知。
同一告警在 cooldown 秒内不重复推送。
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from bot.client.agent_client import AgentClient
from bot.platforms.base import MessagePlatform

logger = logging.getLogger(__name__)

# 监控的指标：(alerts.yaml 键, 中文名, status 响应取值路径)
_METRICS: list[tuple[str, str, list[str]]] = [
    ("cpu_percent",  "CPU",  ["cpu_percent"]),
    ("mem_percent",  "内存", ["memory", "percent"]),
    ("disk_percent", "磁盘", ["disk", "percent"]),
]


def _get_nested(data: dict, keys: list[str]) -> float:
    """从嵌套 dict 中按路径取值"""
    val: Any = data
    for k in keys:
        val = val[k]
    return float(val)


@dataclass
class _AlertState:
    alerting: bool = False
    started_at: datetime | None = None     # 告警开始时间
    last_notified_at: datetime | None = None  # 最近一次推送时间


class AlertMonitor:
    """
    告警监控。

    platforms: list of (MessagePlatform, user_ids) — 广播目标
    alerts_config_path: alerts.yaml 路径
    node_configs: {node_name: node_config_dict} — 来自 Config.nodes
    """

    def __init__(
        self,
        platforms: list[tuple[MessagePlatform, list[str]]],
        node_configs: dict[str, dict[str, Any]],
        alerts_config_path: str = "config/alerts.yaml",
    ) -> None:
        self._platforms = platforms
        self._node_configs = node_configs
        self._cfg = self._load_alerts_config(alerts_config_path)

        # 状态表：(node_name, metric_key) → _AlertState
        self._states: dict[tuple[str, str], _AlertState] = {}

    # ── 配置加载 ───────────────────────────────────────────────────────

    @staticmethod
    def _load_alerts_config(path: str) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            logger.warning("alerts.yaml 不存在 (%s)，使用默认配置", path)
            return {
                "enabled": False,
                "interval": 60,
                "cooldown": 1800,
                "thresholds": {"cpu_percent": 80, "mem_percent": 80, "disk_percent": 80},
                "nodes": [],
            }
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f)

    # ── 入口 ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._cfg.get("enabled", True):
            logger.info("告警系统已禁用 (enabled: false)")
            return

        interval: int = int(self._cfg.get("interval", 60))
        monitored: list[str] = self._cfg.get("nodes", [])
        logger.info(
            "告警监控启动 — 节点: %s | 间隔: %ds | 冷却: %ds",
            monitored, interval, int(self._cfg.get("cooldown", 1800)),
        )

        while True:
            try:
                await asyncio.sleep(interval)
                await self._poll_all(monitored)
            except asyncio.CancelledError:
                logger.info("告警监控收到停止信号，退出")
                return
            except Exception:
                logger.exception("告警轮询异常，%ds 后重试", interval)

    # ── 轮询 ───────────────────────────────────────────────────────────

    async def _poll_all(self, monitored_nodes: list[str]) -> None:
        for node_name in monitored_nodes:
            if node_name not in self._node_configs:
                logger.warning("告警配置引用了未知节点: %s", node_name)
                continue
            await self._check_node(node_name)

    async def _check_node(self, node_name: str) -> None:
        node_cfg = self._node_configs[node_name]
        label = node_cfg.get("label", node_name)
        client = AgentClient(node_cfg)

        try:
            status = await client._get("/status", timeout=10.0)
        except Exception as e:
            logger.warning("节点 %s /status 请求失败: %s", node_name, e)
            await self._handle_metric(node_name, "offline", label, offline=True)
            return

        # 节点在线 → 检查是否从离线状态恢复
        await self._handle_metric(node_name, "offline", label, offline=False)

        thresholds: dict = self._cfg.get("thresholds", {})
        for cfg_key, display_name, path in _METRICS:
            threshold = float(thresholds.get(cfg_key, 80))
            try:
                current = _get_nested(status, path)
            except (KeyError, TypeError) as exc:
                logger.debug("节点 %s 取指标 %s 失败: %s", node_name, cfg_key, exc)
                continue
            await self._handle_metric(
                node_name, cfg_key, label,
                offline=False,
                current=current,
                threshold=threshold,
                display_name=display_name,
            )

    # ── 状态机 ─────────────────────────────────────────────────────────

    async def _handle_metric(
        self,
        node_name: str,
        metric_key: str,
        label: str,
        offline: bool,
        current: float = 0.0,
        threshold: float = 80.0,
        display_name: str = "",
    ) -> None:
        key = (node_name, metric_key)
        state = self._states.setdefault(key, _AlertState())
        now = datetime.now()
        cooldown = timedelta(seconds=int(self._cfg.get("cooldown", 1800)))

        if offline or (metric_key != "offline" and current > threshold):
            # ── 进入或持续告警状态 ─────────────────────────────────
            if not state.alerting:
                state.alerting = True
                state.started_at = now
                state.last_notified_at = None  # 重置，确保立刻推送

            should_notify = (
                state.last_notified_at is None
                or (now - state.last_notified_at) >= cooldown
            )
            if should_notify:
                msg = self._fmt_alert(label, metric_key, display_name, current, threshold, now)
                await self._broadcast(msg)
                state.last_notified_at = now

        else:
            # ── 恢复正常 ───────────────────────────────────────────
            if state.alerting:
                duration = int((now - state.started_at).total_seconds() // 60)
                msg = self._fmt_recovery(label, metric_key, display_name, current, duration)
                await self._broadcast(msg)
                state.alerting = False
                state.started_at = None
                state.last_notified_at = None

    # ── 消息格式化 ─────────────────────────────────────────────────────

    @staticmethod
    def _fmt_alert(
        label: str,
        metric_key: str,
        display_name: str,
        current: float,
        threshold: float,
        now: datetime,
    ) -> str:
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        if metric_key == "offline":
            return f"🔴 [{label}] 节点离线\n时间：{ts}"
        icon = "⚠️"
        return (
            f"{icon} [{label}] {display_name}告警\n"
            f"当前：{current:.1f}% | 阈值：{threshold:.0f}%\n"
            f"时间：{ts}"
        )

    @staticmethod
    def _fmt_recovery(
        label: str,
        metric_key: str,
        display_name: str,
        current: float,
        duration_min: int,
    ) -> str:
        if metric_key == "offline":
            return f"✅ [{label}] 节点恢复在线\n持续离线：{duration_min}分钟"
        return (
            f"✅ [{label}] {display_name}恢复正常\n"
            f"当前：{current:.1f}% | 持续异常：{duration_min}分钟"
        )

    # ── 广播 ───────────────────────────────────────────────────────────

    async def _broadcast(self, text: str) -> None:
        logger.info("告警推送: %s", text.replace("\n", " | "))
        for platform, user_ids in self._platforms:
            try:
                await platform.broadcast(text, user_ids)
            except Exception:
                logger.exception("向平台 %s 推送失败", platform.platform_name)
