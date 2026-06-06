from bot.config import get_config
from bot.client.agent_client import AgentClient


async def handle(args: str, node: str = "vps1") -> str:
    client = AgentClient(get_config().get_node(node))
    return await client.call_services()
