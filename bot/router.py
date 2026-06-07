import asyncio
import logging

logger = logging.getLogger(__name__)

# 路由表（仅用于展示，实际匹配逻辑在 route() 中）
ROUTE_TABLE = [
    ("/status [node]",            "StatusHandler"),
    ("/docker [node]",            "DockerHandler"),
    ("/services [node]",          "ServicesHandler"),
    ("/info [node]",              "InfoHandler"),
    ("/speedtest [node]",         "SpeedtestHandler"),
    ("/log <service> [node]",     "LogHandler"),
    ("/restart <service> [node]", "RestartHandler"),
]

HELP_TEXT = """📋 可用命令（末尾加节点名可切换，默认 vps1）：
  /status [node]             — 系统状态（CPU/内存/磁盘/运行时间）
  /docker [node]             — 容器列表
  /services [node]           — 所有服务运行状态
  /info [node]               — 节点详细信息
  /speedtest [node]          — 网络测速
  /log <service> [node]      — 最近日志
  /restart <service> [node]  — 重启服务

示例：/status vps2  /log xray vps2  /info vps1"""


class CommandRouter:

    def __init__(self) -> None:
        from bot.permissions import get_checker, DENIED_MSG
        from bot.config import get_config
        self._check = get_checker().check
        self._denied = DENIED_MSG
        self._cfg = get_config()

    def _parse_node(self, parts: list[str]) -> tuple[list[str], str]:
        """
        若最后一个词是已知节点名，弹出它并返回；否则返回默认 vps1。
        """
        known = set(self._cfg.nodes.keys())
        if parts and parts[-1].lower() in known:
            return parts[:-1], parts[-1].lower()
        return parts, "vps1"

    async def route(
        self, platform: str, user_id: str, text: str, node: str = "vps1"
    ) -> str:
        # ── 1. 权限校验 ──────────────────────────────────────────────
        if not self._check(platform, str(user_id)):
            return self._denied

        # ── 2. 预处理 ────────────────────────────────────────────────
        text = text.strip()
        parts = text.split()
        cmd = parts[0].lower() if parts else ""

        # ── 3. 路由匹配（按顺序，startswith，不用正则）────────────────

        if cmd == "/status":
            _, node = self._parse_node(parts[1:])
            from bot.handlers import status
            return await status.handle("", node)

        if cmd == "/docker":
            _, node = self._parse_node(parts[1:])
            from bot.handlers import docker_handler
            return await docker_handler.handle("", node)

        if cmd == "/services":
            _, node = self._parse_node(parts[1:])
            from bot.handlers import services_handler
            return await services_handler.handle("", node)

        if cmd == "/info":
            _, node = self._parse_node(parts[1:])
            try:
                from bot.handlers import info_handler
                return await info_handler.handle("", node)
            except ImportError:
                return "ℹ️ /info 功能将在后续步骤部署"

        if cmd == "/speedtest":
            _, node = self._parse_node(parts[1:])
            try:
                from bot.handlers import speedtest_handler
                return await speedtest_handler.handle("", node)
            except ImportError:
                return "📶 /speedtest 功能将在后续步骤部署"

        if cmd == "/log":
            rest, node = self._parse_node(parts[1:])
            service = rest[0].lower() if rest else ""
            if not service:
                return "❌ 请指定服务名，如：/log xray"
            if not self._cfg.is_allowed_service(service):
                available = "/".join(self._cfg.allowed_services)
                return f"❌ 不支持的服务: {service}，可用: {available}"
            from bot.handlers import log_handler
            return await log_handler.handle(service, node)

        if cmd == "/restart":
            rest, node = self._parse_node(parts[1:])
            service = rest[0].lower() if rest else ""
            if not service:
                return "❌ 请指定服务名，如：/restart xray"
            if not self._cfg.is_allowed_service(service):
                available = "/".join(self._cfg.allowed_services)
                return f"❌ 不支持的服务: {service}，可用: {available}"
            from bot.handlers import restart_handler
            return await restart_handler.handle(service, node)

        return HELP_TEXT


# ──────────────────────────────────────────────────────────────
# 路由测试（python3 bot/router.py）
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    # ── 注入测试 Config ──────────────────────────────────────────
    import bot.config as _cfg_mod
    import bot.permissions as _pm_mod

    class _TestConfig:
        _data = {
            "allowed_users": {"telegram": ["111"], "qq": ["333"]},
        }

        def is_allowed_user(self, platform: str, user_id: str) -> bool:
            return str(user_id) in self._data["allowed_users"].get(platform, [])

        def is_allowed_service(self, name: str) -> bool:
            return name in ("nginx", "trilium", "x-ui")

        @property
        def allowed_services(self):
            return ["nginx", "trilium", "x-ui"]

    _cfg_mod._instance = _TestConfig()   # type: ignore[assignment]
    _pm_mod._checker = None              # 重置 checker 让它重新从 config 读

    # ── 运行测试 ─────────────────────────────────────────────────
    router = CommandRouter()

    CASES = [
        # (label, platform, user_id, text, node)
        ("① /status",              "telegram", "111", "/status",         "vps1"),
        ("② /docker",              "telegram", "111", "/docker",         "vps1"),
        ("③ /log nginx",           "telegram", "111", "/log nginx",      "vps1"),
        ("④ /restart trilium",     "telegram", "111", "/restart trilium","vps1"),
        ("⑤ /services",            "telegram", "111", "/services",       "vps1"),
        ("⑥ 无权限用户",           "telegram", "999", "/status",         "vps1"),
        ("⑦ 非法 service",         "telegram", "111", "/log badservice", "vps1"),
    ]

    async def run():
        SEP = "─" * 50
        for label, platform, uid, text, node in CASES:
            result = await router.route(platform, uid, text, node)
            print(f"\n{SEP}")
            print(f"  {label}  ({platform} / {uid})  ▶  {text!r}")
            print(SEP)
            print(result)
        print(f"\n{'─' * 50}")

    asyncio.run(run())
