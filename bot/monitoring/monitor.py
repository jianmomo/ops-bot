from __future__ import annotations
"""
告警监控模块 — Phase 3

每 interval 秒轮询各节点，检测：
  - CPU / 内存 / 磁盘超阈值
  - 节点离线
  - 到期提醒（expire_remind_days 天前推送）
  - 月流量预警（需 Agent /traffic 接口）
  - DDoS / 入站流量异常（需 Agent /network 接口）
  - GFW 封锁检测（端口 443 TCP 可达性）每 gfw_check_interval 秒
  - 网站可用性监控   每 websites.interval 秒
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import date as _date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml

from bot.client.agent_client import AgentClient
from bot.platforms.base import MessagePlatform

logger = logging.getLogger(__name__)

# 轮询的系统指标：(alerts.yaml 阈值键, 中文名, status 响应取值路径)
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
    started_at: datetime | None = None
    last_notified_at: datetime | None = None


class AlertMonitor:
    """
    告警监控。

    platforms: list of (MessagePlatform, user_ids) — 广播目标
    node_configs: {node_name: node_config_dict} — 来自 Config.nodes
    alerts_config_path: alerts.yaml 路径
    """

    def __init__(
        self,
        platforms: list[tuple[MessagePlatform, list[str]]],
        node_configs: dict[str, dict[str, Any]],
        alerts_config_path: str = "config/alerts.yaml",
    ) -> None:
        self._platforms    = platforms
        self._node_configs = node_configs
        self._cfg          = self._load_alerts_config(alerts_config_path)

        # (node_name, metric_key) → _AlertState
        self._states: dict[tuple[str, str], _AlertState] = {}
        # 到期已推送的 (node_name, days_left) 组合，避免同一天重复推
        self._expire_notified: set[tuple[str, int]] = set()

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

        interval    = int(self._cfg.get("interval", 60))
        monitored:  list[str] = self._cfg.get("nodes", [])
        gfw_interval = int(self._cfg.get("gfw_check_interval", 3600))
        web_cfg     = self._cfg.get("websites", {})
        web_interval = int(web_cfg.get("interval", interval))

        logger.info(
            "告警监控启动 — 节点: %s | 间隔: %ds | 冷却: %ds",
            monitored, interval, int(self._cfg.get("cooldown", 1800)),
        )

        loop        = asyncio.get_event_loop()
        gfw_next    = loop.time() + gfw_interval  # GFW 首次检测延后一个周期
        web_next    = loop.time()                  # 网站监控随主循环立即起算

        while True:
            try:
                await asyncio.sleep(interval)
                now_ts = loop.time()

                # 主轮询：CPU / 内存 / 磁盘 / 离线 / 到期 / 流量 / DDoS
                await self._poll_all(monitored)

                # 网站可用性
                if web_cfg.get("enabled") and now_ts >= web_next:
                    await self._check_websites()
                    web_next = now_ts + web_interval

                # GFW 封锁检测
                if now_ts >= gfw_next:
                    await self._check_gfw_all(monitored)
                    gfw_next = now_ts + gfw_interval

            except asyncio.CancelledError:
                logger.info("告警监控收到停止信号，退出")
                return
            except Exception:
                logger.exception("告警轮询异常，%ds 后重试", interval)

    # ── 主轮询 ─────────────────────────────────────────────────────────

    async def _poll_all(self, monitored_nodes: list[str]) -> None:
        for node_name in monitored_nodes:
            if node_name not in self._node_configs:
                logger.warning("告警配置引用了未知节点: %s", node_name)
                continue
            await self._check_node(node_name)

    async def _check_node(self, node_name: str) -> None:
        node_cfg = self._node_configs[node_name]
        label    = node_cfg.get("label", node_name)
        client   = AgentClient(node_cfg)

        try:
            status = await client._get("/status", timeout=10.0)
        except Exception as e:
            logger.warning("节点 %s /status 请求失败: %s", node_name, e)
            await self._handle_metric(node_name, "offline", label, offline=True)
            return

        # 节点在线 → 检查离线恢复
        await self._handle_metric(node_name, "offline", label, offline=False)

        # CPU / 内存 / 磁盘
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
                offline=False, current=current,
                threshold=threshold, display_name=display_name,
            )

        # 到期提醒（读本地 config，无需 HTTP）
        await self._check_expire(node_name, label, node_cfg)

        # 月流量预警（需 /traffic；vnstat 未就绪时静默跳过）
        if int(node_cfg.get("monthly_traffic_gb", 0)) > 0:
            await self._check_traffic(node_name, label, node_cfg, client)

        # DDoS / 入站流量异常（需 /network；失败时静默跳过）
        await self._check_ddos(node_name, label, client)

    # ── 到期提醒 ───────────────────────────────────────────────────────

    async def _check_expire(self, node_name: str, label: str, node_cfg: dict) -> None:
        expire_str = str(node_cfg.get("expire_date", "")).strip()
        if not expire_str:
            return
        try:
            expire_dt = _date.fromisoformat(expire_str)
            days_left = (expire_dt - _date.today()).days
        except ValueError:
            return

        remind_days: list[int] = self._cfg.get("expire_remind_days", [7, 3, 1])

        if days_left <= 0:
            key = (node_name, 0)
            if key not in self._expire_notified:
                await self._broadcast(
                    f"❌ [{label}] 节点已过期\n"
                    f"到期日：{expire_str}（已过 {-days_left} 天）"
                )
                self._expire_notified.add(key)
        elif days_left in remind_days:
            key = (node_name, days_left)
            if key not in self._expire_notified:
                await self._broadcast(
                    f"📅 [{label}] 节点即将到期\n"
                    f"到期日：{expire_str}\n"
                    f"剩余：{days_left} 天"
                )
                self._expire_notified.add(key)

    # ── 月流量预警 ─────────────────────────────────────────────────────

    async def _check_traffic(
        self, node_name: str, label: str, node_cfg: dict, client: AgentClient
    ) -> None:
        monthly_gb       = int(node_cfg.get("monthly_traffic_gb", 0))
        traffic_warn_pct = float(self._cfg.get("traffic_warn_percent", 80))
        try:
            data = await client._get("/traffic", timeout=10.0)
        except Exception:
            return
        if data.get("error"):
            return  # vnstat 未就绪
        used_gb   = float(data.get("used_gb", 0))
        usage_pct = used_gb / monthly_gb * 100
        await self._handle_metric(
            node_name, "traffic", label,
            offline=False, current=usage_pct,
            threshold=traffic_warn_pct, display_name="流量",
            extra_info=f"已用 {used_gb:.1f}/{monthly_gb} GB",
        )

    # ── DDoS / 入站流量异常 ────────────────────────────────────────────

    async def _check_ddos(self, node_name: str, label: str, client: AgentClient) -> None:
        ddos_threshold = float(self._cfg.get("ddos_threshold_mbps", 100))
        try:
            data = await client._get("/network", timeout=5.0)
        except Exception:
            return
        rx_mbps = float(data.get("rx_mbps", 0))
        await self._handle_metric(
            node_name, "ddos", label,
            offline=False, current=rx_mbps,
            threshold=ddos_threshold, display_name="入站流量",
        )

    # ── GFW 封锁检测 ───────────────────────────────────────────────────

    async def _check_gfw_all(self, monitored: list[str]) -> None:
        gfw_timeout = float(self._cfg.get("gfw_check_timeout", 10))
        tasks = [
            self._check_gfw_node(n, gfw_timeout)
            for n in monitored if n in self._node_configs
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_gfw_node(self, node_name: str, timeout: float) -> None:
        node_cfg = self._node_configs[node_name]
        label    = node_cfg.get("label", node_name)
        host     = node_cfg["host"]
        blocked  = await self._tcp_blocked(host, 443, timeout)
        await self._handle_metric(
            node_name, "gfw", label, offline=blocked,
        )

    @staticmethod
    async def _tcp_blocked(host: str, port: int, timeout: float) -> bool:
        """
        True  = 连接超时（可能被 GFW 封锁）
        False = 正常连通 或 连接被拒绝（端口未开放但 IP 未封）
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return False
        except asyncio.TimeoutError:
            return True
        except OSError:
            return False  # connection refused → IP 可达，只是端口未监听

    # ── 网站可用性监控 ─────────────────────────────────────────────────

    async def _check_websites(self) -> None:
        web_cfg = self._cfg.get("websites", {})
        sites: list[dict] = web_cfg.get("sites", [])
        timeout = float(web_cfg.get("timeout", 10))
        tasks = [self._check_site(s, timeout) for s in sites]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_site(self, site: dict, timeout: float) -> None:
        url  = site.get("url", "")
        name = site.get("name", url)
        if not url:
            return
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, follow_redirects=True)
            ok = r.status_code < 500
        except Exception:
            ok = False
        await self._handle_metric(
            f"website:{url}", "website", name, offline=not ok,
        )

    # ── 通用状态机 ─────────────────────────────────────────────────────

    # 这些 metric_key 用 offline 语义（True=出问题）而非数值阈值比较
    _OFFLINE_STYLE = frozenset({"offline", "gfw", "website"})

    async def _handle_metric(
        self,
        node_name: str,
        metric_key: str,
        label: str,
        offline: bool,
        current: float = 0.0,
        threshold: float = 80.0,
        display_name: str = "",
        extra_info: str = "",
    ) -> None:
        key   = (node_name, metric_key)
        state = self._states.setdefault(key, _AlertState())
        now   = datetime.now()
        cooldown = timedelta(seconds=int(self._cfg.get("cooldown", 1800)))

        is_bad = offline if metric_key in self._OFFLINE_STYLE else current > threshold

        if is_bad:
            if not state.alerting:
                state.alerting       = True
                state.started_at     = now
                state.last_notified_at = None

            should_notify = (
                state.last_notified_at is None
                or (now - state.last_notified_at) >= cooldown
            )
            if should_notify:
                msg = self._fmt_alert(
                    label, metric_key, display_name, current, threshold, now, extra_info
                )
                await self._broadcast(msg)
                state.last_notified_at = now

        else:
            if state.alerting:
                duration = int((now - state.started_at).total_seconds() // 60)
                msg = self._fmt_recovery(label, metric_key, display_name, current, duration)
                await self._broadcast(msg)
                state.alerting         = False
                state.started_at       = None
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
        extra_info: str = "",
    ) -> str:
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        if metric_key == "offline":
            return f"🔴 [{label}] 节点离线\n时间：{ts}"
        if metric_key == "gfw":
            return f"🚧 [{label}] 可能被 GFW 封锁（端口 443 超时）\n时间：{ts}"
        if metric_key == "website":
            return f"🔴 [{label}] 网站不可用\n时间：{ts}"
        if metric_key == "ddos":
            return (
                f"🚨 [{label}] 入站流量异常（疑似 DDoS）\n"
                f"当前：{current:.1f} Mbps | 阈值：{threshold:.0f} Mbps\n"
                f"时间：{ts}"
            )
        if metric_key == "traffic":
            extra = f"\n{extra_info}" if extra_info else ""
            return (
                f"⚠️ [{label}] 月流量预警\n"
                f"使用率：{current:.1f}%{extra}\n"
                f"时间：{ts}"
            )
        return (
            f"⚠️ [{label}] {display_name}告警\n"
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
        if metric_key == "gfw":
            return f"✅ [{label}] 端口 443 恢复可达\n持续不可达：{duration_min}分钟"
        if metric_key == "website":
            return f"✅ [{label}] 网站恢复正常\n持续不可用：{duration_min}分钟"
        if metric_key == "ddos":
            return f"✅ [{label}] 入站流量恢复正常\n持续异常：{duration_min}分钟"
        if metric_key == "traffic":
            return (
                f"✅ [{label}] 月流量预警解除\n"
                f"当前使用率：{current:.1f}% | 持续：{duration_min}分钟"
            )
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
