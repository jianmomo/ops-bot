import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.platforms.base import MessagePlatform

logger = logging.getLogger(__name__)

_TG_MAX_LEN = 4096  # Telegram 单条消息字符上限

_WELCOME = (
    "👋 欢迎使用 Personal Ops Bot！\n\n"
    "📋 可用命令：\n"
    "  /status              — 系统状态\n"
    "  /docker              — 容器列表\n"
    "  /log <service>       — 最近日志\n"
    "  /restart <service>   — 重启服务\n"
    "  /services            — 服务状态\n"
    "  ai <内容>            — AI 分析\n\n"
    "🔒 服务范围：nginx / trilium / x-ui"
)


def _split_message(text: str) -> list[str]:
    """超过 Telegram 单条上限时切分，避免 API 报错"""
    if len(text) <= _TG_MAX_LEN:
        return [text]
    chunks: list[str] = []
    while text:
        chunks.append(text[:_TG_MAX_LEN])
        text = text[_TG_MAX_LEN:]
    return chunks


def _escape_code_content(text: str) -> str:
    """MarkdownV2 代码块内部只需转义 \\ 和 `"""
    return text.replace("\\", "\\\\").replace("`", "\\`")


class TelegramPlatform(MessagePlatform):

    def __init__(self, bot_token: str, router) -> None:
        self._token = bot_token
        self._router = router  # CommandRouter
        self._app: Application = Application.builder().token(bot_token).build()
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        # /start 不走权限校验，单独处理
        self._app.add_handler(CommandHandler("start", self._on_start))
        # 所有文本消息（包含 /command 格式）均通过 router 处理
        self._app.add_handler(MessageHandler(filters.TEXT, self._on_message))

    # ── MessagePlatform 接口实现 ────────────────────────────────────

    @property
    def platform_name(self) -> str:
        return "telegram"

    async def send_message(self, chat_id: str, text: str) -> None:
        """发送纯文本，自动分段（每段 ≤ 4096 字符）"""
        for chunk in _split_message(text):
            try:
                await self._app.bot.send_message(chat_id=chat_id, text=chunk)
            except Exception:
                logger.exception("send_message 失败 chat_id=%s", chat_id)

    async def send_code_block(self, chat_id: str, code: str, lang: str = "") -> None:
        """用 MarkdownV2 代码块发送，失败时降级为纯文本"""
        escaped = _escape_code_content(code)
        formatted = f"```{lang}\n{escaped}\n```"
        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=formatted,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception:
            logger.exception("send_code_block 失败，降级为纯文本 chat_id=%s", chat_id)
            await self.send_message(chat_id, f"[代码/{lang}]\n{code}")

    # ── 消息处理器 ─────────────────────────────────────────────────

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat:
            return
        await self.send_message(str(update.effective_chat.id), _WELCOME)

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # 忽略非文本消息（图片、贴纸等）
        if not update.message or not update.message.text:
            return

        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)
        text = update.message.text.strip()

        logger.info("TG 消息: user=%s text=%r", user_id, text[:60])
        try:
            result = await self._router.route("telegram", user_id, text)
            await self.reply(chat_id, result)
        except Exception:
            logger.exception("路由处理异常: user=%s", user_id)

    # ── 启动方法 ───────────────────────────────────────────────────

    async def start_polling(self) -> None:
        """
        异步协程，供 main.py 的 asyncio.gather() 调用。
        连接失败（代理未就绪、网络抖动）时每 15 秒重试，CancelledError 时优雅退出。
        """
        _RECONNECT_DELAY = 15
        while True:
            try:
                logger.info("Telegram Bot 开始 polling...")
                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                )
                try:
                    await asyncio.Event().wait()  # 阻塞直到外部 cancel
                except asyncio.CancelledError:
                    logger.info("Telegram Bot 收到停止信号")
                    return
                finally:
                    await self._app.updater.stop()
                    await self._app.stop()
                    await self._app.shutdown()
                    logger.info("Telegram Bot 已停止")
                return  # 正常退出
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Telegram Bot 连接失败 (%s)，%d 秒后重试...", e, _RECONNECT_DELAY)

            # 清理残留状态，重建 Application 以便重连
            for cleanup in (
                lambda: self._app.updater.stop(),
                lambda: self._app.stop(),
                lambda: self._app.shutdown(),
            ):
                try:
                    await cleanup()
                except Exception:
                    pass
            from telegram.ext import Application as _App
            self._app = _App.builder().token(self._token).build()
            self._setup_handlers()

            try:
                await asyncio.sleep(_RECONNECT_DELAY)
            except asyncio.CancelledError:
                logger.info("Telegram Bot 重连等待中收到停止信号，退出")
                return

    def run(self) -> None:
        """同步启动方式，用于单独调试"""
        logger.info("Telegram Bot 启动（同步模式）...")
        self._app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
