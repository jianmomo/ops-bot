"""
/info 处理器：节点元数据 + 实时系统状态合并展示。
"""
import asyncio
import logging
from datetime import date

from bot.config import get_config
from bot.client.agent_client import AgentClient

logger = logging.getLogger(__name__)


async def handle(args: str, node: str = "vps1") -> str:
    cfg = get_config()
    try:
        node_cfg = cfg.get_node(node)
    except KeyError as e:
        return f"❌ {e}"

    client = AgentClient(node_cfg)
    label = node_cfg.get("label", node)
    monthly_traffic_gb = int(node_cfg.get("monthly_traffic_gb", 0))

    # 并行获取：状态（必须）、服务列表（降级）、流量（降级，仅有限额时请求）
    coros = [client._get("/status"), client._get("/services")]
    if monthly_traffic_gb > 0:
        coros.append(client._get("/traffic", timeout=10.0))

    results = await asyncio.gather(*coros, return_exceptions=True)

    status = results[0]
    if isinstance(status, Exception):
        return f"❌ 无法连接节点 {label}：{status}"

    services = results[1] if not isinstance(results[1], Exception) else []
    traffic_data = None
    if monthly_traffic_gb > 0 and len(results) > 2 and not isinstance(results[2], Exception):
        traffic_data = results[2]

    lines = [f"ℹ️  {label} 基础信息", "─" * 17]

    location  = node_cfg.get("location", "")
    purpose   = node_cfg.get("purpose", "")
    expire_str = str(node_cfg.get("expire_date", "")).strip()

    if location:
        lines.append(f"📍 位置：{location}")
    if purpose:
        lines.append(f"🏷  用途：{purpose}")

    if expire_str:
        try:
            expire_date = date.fromisoformat(expire_str)
            days_left = (expire_date - date.today()).days
            lines.append(f"📅 到期：{expire_str}（还有 {days_left} 天）")
        except ValueError:
            lines.append(f"📅 到期：{expire_str}")

    if monthly_traffic_gb > 0:
        if traffic_data is not None:
            used_gb = float(traffic_data.get("used_gb", 0))
            pct = used_gb / monthly_traffic_gb * 100
            lines.append(
                f"📊 本月流量：{used_gb:.1f} GB / {monthly_traffic_gb} GB（{pct:.0f}%）"
            )
        else:
            lines.append("📊 本月流量：（/traffic 接口待部署）")

    cpu     = status.get("cpu_percent", 0)
    mem_pct = status.get("memory", {}).get("percent", 0)
    disk_pct = status.get("disk", {}).get("percent", 0)
    uptime  = status.get("uptime", "N/A")
    lines.append(f"🖥  系统：CPU {cpu}% | 内存 {mem_pct}% | 磁盘 {disk_pct}%")
    lines.append(f"⏱  运行：{uptime}")
    lines.append("─" * 17)

    if services:
        svc_parts = [
            f"{s['service']} {'✅' if s.get('active') else '❌'}"
            for s in services
        ]
        lines.append("服务：" + " | ".join(svc_parts))

    return "\n".join(lines)
