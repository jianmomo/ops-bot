import asyncio
import logging
import sys
from pathlib import Path
from typing import Coroutine, Any

# 将项目根目录加入 sys.path，使 `from bot.xxx import` 可用
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import Config, get_config
from bot.router import CommandRouter, ROUTE_TABLE


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def print_config_summary(cfg: Config) -> None:
    log = logging.getLogger(__name__)
    log.info("========== 配置摘要 ==========")
    tg_users = cfg._data["allowed_users"]["telegram"]
    qq_users = cfg._data["allowed_users"]["qq"]
    log.info("Telegram 白名单: %d 个用户", len(tg_users))
    log.info("QQ 白名单:       %d 个用户", len(qq_users))
    log.info("允许重启服务:    %s", cfg.allowed_services)
    for name, node in cfg.nodes.items():
        token_hint = "已设置" if node.get("token") else "未设置"
        log.info(
            "节点 [%s] %s  %s:%s  token=%s",
            name, node["label"], node["host"], node["port"], token_hint,
        )
    log.info("日志行数: %d", cfg.log_lines)
    ai = cfg.ai_config
    if ai:
        log.info(
            "AI: %s / %s  max_tokens=%s",
            ai.get("provider"), ai.get("model"), ai.get("max_tokens"),
        )
    log.info("==============================")


def print_route_table() -> None:
    log = logging.getLogger(__name__)
    log.info("========== 路由表 ==========")
    for cmd, handler in ROUTE_TABLE:
        log.info("  %-22s → %s", cmd, handler)
    log.info("============================")


async def _run_guarded(coro: Coroutine[Any, Any, None], name: str) -> None:
    """
    包装 bot 协程：CancelledError 正常传播（用于 Ctrl+C 关闭），
    其他异常只打 log，不中断 asyncio.gather 里的其他任务。
    """
    log = logging.getLogger(__name__)
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("%s 意外崩溃，其他 Bot 继续运行", name)


async def main() -> None:
    setup_logging()
    log = logging.getLogger(__name__)
    log.info("Personal Ops Bot 启动中...")

    cfg = get_config()
    print_config_summary(cfg)

    router = CommandRouter()
    print_route_table()

    coros: list[Coroutine] = []

    # ── Telegram Bot ────────────────────────────────────────────────
    tg_token = cfg.telegram_token
    if not tg_token:
        log.warning("TELEGRAM_BOT_TOKEN 未设置，跳过 Telegram Bot")
    else:
        from bot.platforms.telegram_bot import TelegramPlatform
        tg = TelegramPlatform(tg_token, router)
        coros.append(_run_guarded(tg.start_polling(), "Telegram Bot"))
        log.info("Telegram Bot 已就绪")

    # ── QQ Bot（OneBot V11 直连）────────────────────────────────────
    qq_ws = cfg.qq_ws_url
    if not qq_ws:
        log.warning("QQ_WS_URL 未设置，跳过 QQ Bot")
    else:
        qq_http = cfg.qq_http_url
        if not qq_http:
            log.warning("QQ_HTTP_URL 未设置，QQ Bot 无法发送消息，跳过")
        else:
            from bot.platforms.qq_bot import QQPlatform
            qq = QQPlatform(qq_ws, qq_http, router, cfg.qq_access_token)
            coros.append(_run_guarded(qq.start_polling(), "QQ Bot"))
            log.info("QQ Bot 已就绪，连接目标: %s", qq_ws)

    if not coros:
        log.warning("没有可用 Bot（所有 token / URL 均未设置），正常退出")
        return

    log.info("共启动 %d 个 Bot，按 Ctrl+C 退出", len(coros))
    try:
        await asyncio.gather(*coros)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("所有 Bot 已停止")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("收到 Ctrl+C，退出")
