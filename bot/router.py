import asyncio
import logging

logger = logging.getLogger(__name__)

# 路由表（仅用于展示，实际匹配逻辑在 route() 中）
ROUTE_TABLE = [
    ("/status",            "StatusHandler"),
    ("/docker",            "DockerHandler"),
    ("/log <service>",     "LogHandler"),
    ("/restart <service>", "RestartHandler"),
    ("/services",          "ServicesHandler"),
    ("ai <query>",         "AIAnalyzer"),
]

HELP_TEXT = """\
📋 可用命令：
  /status              — 系统状态（CPU/内存/磁盘/运行时间）
  /docker              — 容器列表
  /log <service>       — 最近日志（nginx / trilium / x-ui）
  /restart <service>   — 重启服务（nginx / trilium / x-ui）
  /services            — 所有服务运行状态
  ai <内容>            — AI 分析（如：ai 分析最近日志）"""


class CommandRouter:

    def __init__(self) -> None:
        from bot.permissions import get_checker, DENIED_MSG
        from bot.config import get_config
        self._check = get_checker().check
        self._denied = DENIED_MSG
        self._cfg = get_config()

    async def route(
        self, platform: str, user_id: str, text: str, node: str = "vps1"
    ) -> str:
        # ── 1. 权限校验 ──────────────────────────────────────────────
        if not self._check(platform, str(user_id)):
            return self._denied

        # ── 2. 预处理 ────────────────────────────────────────────────
        text = text.strip()
        cmd = text.lower()

        # ── 3. 路由匹配（按顺序，startswith，不用正则）────────────────

        if cmd == "/status":
            from bot.handlers import status
            return await status.handle("", node)

        if cmd == "/docker":
            from bot.handlers import docker_handler
            return await docker_handler.handle("", node)

        if cmd == "/services":
            from bot.handlers import services_handler
            return await services_handler.handle("", node)

        if cmd.startswith("/log"):
            parts = text.split(maxsplit=1)
            service = parts[1].lower() if len(parts) > 1 else ""
            if not service:
                return "❌ 请指定服务名，如：/log nginx"
            if not self._cfg.is_allowed_service(service):
                available = "/".join(self._cfg.allowed_services)
                return f"❌ 不支持的服务: {service}，可用: {available}"
            from bot.handlers import log_handler
            return await log_handler.handle(service, node)

        if cmd.startswith("/restart"):
            parts = text.split(maxsplit=1)
            service = parts[1].lower() if len(parts) > 1 else ""
            if not service:
                return "❌ 请指定服务名，如：/restart nginx"
            if not self._cfg.is_allowed_service(service):
                available = "/".join(self._cfg.allowed_services)
                return f"❌ 不支持的服务: {service}，可用: {available}"
            from bot.handlers import restart_handler
            return await restart_handler.handle(service, node)

        if cmd.startswith("ai "):
            query = text[3:].strip()
            if not query:
                return "❌ 请输入分析内容，如：ai 分析最近日志"
            from bot.ai import analyzer
            return await analyzer.analyze(query, node)

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
        ("⑥ ai 查询",              "telegram", "111", "ai 分析最近日志", "vps1"),
        ("⑦ 无权限用户",           "telegram", "999", "/status",         "vps1"),
        ("⑧ 非法 service",         "telegram", "111", "/log badservice", "vps1"),
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
