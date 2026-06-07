"""
/speedtest 处理器：调用 Agent POST /speedtest 并格式化结果。
"""
import logging

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
    return await client.call_speedtest()
