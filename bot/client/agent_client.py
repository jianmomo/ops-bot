"""
Bot 侧 HTTP 客户端，调用各节点 Agent API 并格式化为聊天消息。
"""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT   = 15.0
_RESTART_TIMEOUT   = 30.0
_SPEEDTEST_TIMEOUT = 120.0


class AgentClient:

    def __init__(self, node_config: dict[str, Any]) -> None:
        host = node_config["host"]
        port = node_config["port"]
        self._base = f"http://{host}:{port}"
        self._label = node_config.get("label", host)
        self._headers = {"Authorization": f"Bearer {node_config['token']}"}

    def _err(self, exc: Exception) -> str:
        return f"❌ 无法连接节点 {self._label}：{exc}"

    # ── 内部 HTTP 工具 ────────────────────────────────────────────────

    async def _get(self, path: str, timeout: float = _DEFAULT_TIMEOUT, **params: Any) -> Any:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{self._base}{path}",
                headers=self._headers,
                params=params or None,
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, timeout: float = _DEFAULT_TIMEOUT) -> Any:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{self._base}{path}",
                headers=self._headers,
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()

    # ── 公共方法 ─────────────────────────────────────────────────────

    async def call_status(self) -> str:
        try:
            d = await self._get("/status")
            m, dk = d["memory"], d["disk"]
            return (
                f"🖥  系统状态 [{self._label}]\n"
                "──────────────────────\n"
                f"CPU:      {d['cpu_percent']} %\n"
                f"内存:     {m['used_gb']} GB / {m['total_gb']} GB  ({m['percent']}%)\n"
                f"磁盘:     {dk['used_gb']} GB / {dk['total_gb']} GB  ({dk['percent']}%)\n"
                f"运行时间: {d['uptime']}"
            )
        except Exception as e:
            return self._err(e)

    async def call_docker(self) -> str:
        try:
            containers: list[dict] = await self._get("/docker")
            if not containers:
                return f"🐳 [{self._label}] 无容器"
            lines = [f"🐳 容器列表 [{self._label}]", "─" * 44]
            for c in containers:
                lines.append(f"{c['name']:<18} {c['status']:<12} {c['image']}")
            return "\n".join(lines)
        except Exception as e:
            return self._err(e)

    async def call_services(self) -> str:
        try:
            items: list[dict] = await self._get("/services")
            lines = [f"⚙️  服务状态 [{self._label}]", "─" * 40]
            for s in items:
                icon = "●" if s["active"] else "○"
                state = "运行中" if s["active"] else s["status"]
                lines.append(f"{s['service']:<12} {icon} {state}")
            return "\n".join(lines)
        except Exception as e:
            return self._err(e)

    async def call_logs(self, service: str, lines: int = 50) -> str:
        try:
            d = await self._get(f"/logs/{service}", lines=lines)
            text = d.get("logs", "").strip()
            header = f"📋 [{self._label}] {service} 最近 {lines} 行日志\n{'─' * 42}\n"
            return header + (text if text else "(无日志输出)")
        except Exception as e:
            return self._err(e)

    async def call_restart(self, service: str) -> str:
        try:
            d = await self._post(f"/restart/{service}", timeout=_RESTART_TIMEOUT)
            if d.get("success"):
                return f"✅ [{self._label}] {service} 重启成功"
            return f"❌ [{self._label}] {service} 重启失败：{d.get('message', '未知错误')}"
        except Exception as e:
            return self._err(e)

    async def call_speedtest(self) -> str:
        try:
            d = await self._post("/speedtest", timeout=_SPEEDTEST_TIMEOUT)
            lines = [f"📶 {self._label} 网络测速", "─" * 17]

            ping = d.get("ping_results", {})
            if ping:
                lines.append("延迟（三网）：")
                for key, name in (("telecom", "电信"), ("unicom", "联通"), ("mobile", "移动")):
                    if key in ping:
                        lines.append(f"  📡 {name}：{ping[key]}ms")

            lines.append("速度：")
            dl     = d.get("download_mbps", 0)
            ul     = d.get("upload_mbps", 0)
            server = d.get("server", "N/A")
            lines.append(f"  ⬇️  下载：{dl:.1f} Mbps")
            lines.append(f"  ⬆️  上传：{ul:.1f} Mbps")
            lines.append(f"  🌐 测速节点：{server}")

            return "\n".join(lines)
        except Exception as e:
            return self._err(e)
