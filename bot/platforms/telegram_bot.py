"""
Telegram Bot 平台实现。

支持 Inline 键盘交互，通过 _pending 跟踪多步操作状态（/log、/restart）。
单步命令（/status、/docker、/services、/info）点击节点后直接返回结果。
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.platforms.base import MessagePlatform

logger = logging.getLogger(__name__)

_TG_MAX_LEN = 4096   # Telegram 单条消息字符上限
_PENDING_TTL = 300   # pending 状态超时（秒）


# ── 多步操作状态 ────────────────────────────────────────────────

@dataclass
class _PendingState:
    """记录用户当前正在执行的多步操作"""
    cmd: str            # 命令名（log / restart / speedtest 等）
    step: int           # 当前步骤（从 1 开始）
    params: dict        # 已收集的参数（如 node、svc）
    ts: float = field(default_factory=time.time)  # 创建时间戳


# ── 常量 ────────────────────────────────────────────────────────

_WELCOME = (
    "👋 欢迎使用 Personal Ops Bot！\n\n"
    "📋 可用命令：\n"
    "  /status    — 📊 服务器状态\n"
    "  /docker    — 🐳 容器状态\n"
    "  /services  — ⚙️  服务状态\n"
    "  /info      — ℹ️  节点信息\n"
    "  /log       — 📋 查看日志\n"
    "  /restart   — 🔄 重启服务\n"
    "  /speedtest — 📶 网络测速\n\n"
    "💡 发送命令后，通过按钮选择节点和服务。\n"
    "💡 也支持直接输入：/status vps2  /log xray vps1"
)

# 向 BotFather 注册的命令列表
_BOT_COMMANDS = [
    BotCommand("status",    "📊 服务器状态"),
    BotCommand("docker",    "🐳 容器状态"),
    BotCommand("services",  "⚙️ 服务状态"),
    BotCommand("info",      "ℹ️ 节点信息"),
    BotCommand("log",       "📋 查看日志"),
    BotCommand("restart",   "🔄 重启服务"),
    BotCommand("speedtest", "📶 网络测速"),
    BotCommand("start",     "🤖 帮助"),
]

# 无参数时展示键盘的命令：cmd_word → (base_cmd, include_all, icon)
_KBD_CMDS: dict[str, tuple[str, bool, str]] = {
    "/status":    ("status",    True,  "🖥"),
    "/docker":    ("docker",    True,  "🖥"),
    "/services":  ("services",  True,  "⚙️"),
    "/info":      ("info",      True,  "ℹ️"),
    "/speedtest": ("speedtest", False, "📶"),
    "/log":       ("log",       False, "🖥"),
    "/restart":   ("restart",   False, "🖥"),
}


# ── 辅助函数 ────────────────────────────────────────────────────

def _split_message(text: str) -> list[str]:
    """超过 Telegram 单条上限时切分"""
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


# ── 主类 ────────────────────────────────────────────────────────

class TelegramPlatform(MessagePlatform):

    def __init__(self, bot_token: str, router) -> None:
        self._token = bot_token
        self._router = router
        self._app: Application = self._build_app(bot_token)
        # 用户多步操作状态：user_id(str) → _PendingState
        self._pending: dict[str, _PendingState] = {}
        self._setup_handlers()

    @staticmethod
    def _build_app(token: str) -> Application:
        # xray 代理建立隧道可能需要 >10s，调大超时避免 getUpdates/sendMessage 失败
        return (
            Application.builder()
            .token(token)
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .get_updates_connect_timeout(30.0)
            .get_updates_read_timeout(30.0)
            .get_updates_write_timeout(30.0)
            .build()
        )

    def _setup_handlers(self) -> None:
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        self._app.add_handler(MessageHandler(filters.TEXT, self._on_message))

    # ── MessagePlatform 接口 ────────────────────────────────────

    @property
    def platform_name(self) -> str:
        return "telegram"

    async def send_message(self, chat_id: str, text: str) -> None:
        """发送纯文本，自动分段。代理断线时等 3s 重试一次。"""
        for chunk in _split_message(text):
            for attempt in range(2):
                try:
                    await self._app.bot.send_message(chat_id=chat_id, text=chunk)
                    break
                except Exception as exc:
                    if attempt == 0:
                        logger.warning("send_message 首次失败 (%s)，3s 后重试...", exc)
                        await asyncio.sleep(3)
                    else:
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

    # ── 权限校验 ────────────────────────────────────────────────

    def _is_allowed(self, user_id: str) -> bool:
        from bot.permissions import get_checker
        return get_checker().check("telegram", user_id)

    # ── Pending 状态管理 ─────────────────────────────────────────

    def _set_pending(self, user_id: str, cmd: str, step: int,
                     params: dict | None = None) -> None:
        self._pending[user_id] = _PendingState(
            cmd=cmd, step=step, params=params or {}
        )

    def _get_pending(self, user_id: str) -> _PendingState | None:
        """返回未超时的 pending 状态，已超时则删除并返回 None"""
        state = self._pending.get(user_id)
        if state is None:
            return None
        if time.time() - state.ts > _PENDING_TTL:
            del self._pending[user_id]
            return None
        return state

    def _clear_pending(self, user_id: str) -> None:
        self._pending.pop(user_id, None)

    def _cleanup_pending(self) -> None:
        """清理所有超过 5 分钟的过期状态（每次 callback 入口调用）"""
        now = time.time()
        expired = [uid for uid, s in list(self._pending.items())
                   if now - s.ts > _PENDING_TTL]
        for uid in expired:
            del self._pending[uid]

    # ── 键盘构建 ─────────────────────────────────────────────────

    def _node_kbd(self, cmd: str, include_all: bool = True,
                  icon: str = "🖥") -> InlineKeyboardMarkup:
        """
        生成节点选择键盘。
        include_all=True 时首行添加"全部汇总"按钮。
        callback_data 格式："{cmd}:{node_name}" 或 "{cmd}:all"
        """
        from bot.config import get_config
        cfg = get_config()
        buttons: list[list[InlineKeyboardButton]] = []

        if include_all:
            buttons.append([
                InlineKeyboardButton("📊 全部汇总", callback_data=f"{cmd}:all")
            ])

        # 所有节点排一行
        row = [
            InlineKeyboardButton(
                f"{icon} {node_cfg.get('label', name)}",
                callback_data=f"{cmd}:{name}",
            )
            for name, node_cfg in cfg.nodes.items()
        ]
        buttons.append(row)
        return InlineKeyboardMarkup(buttons)

    def _service_kbd(self, cmd: str, node: str) -> InlineKeyboardMarkup:
        """
        从 config 读取该节点的 services 字段，生成服务选择键盘。
        callback_data 格式："{cmd}:{node}:{svc}"
        """
        from bot.config import get_config
        services = get_config().nodes.get(node, {}).get("services", [])
        buttons = [
            [InlineKeyboardButton(f"📦 {svc}", callback_data=f"{cmd}:{node}:{svc}")]
            for svc in services
        ]
        return InlineKeyboardMarkup(buttons)

    def _confirm_restart_kbd(self, node: str, svc: str) -> InlineKeyboardMarkup:
        """
        重启确认键盘（第三步）。
        callback_data：确认="restart:{node}:{svc}:confirm"，取消="restart:cancel"
        """
        from bot.config import get_config
        label = get_config().nodes.get(node, {}).get("label", node)
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"✅ 确认重启 {svc}@{label}",
                callback_data=f"restart:{node}:{svc}:confirm",
            ),
            InlineKeyboardButton("❌ 取消", callback_data="restart:cancel"),
        ]])

    # ── 消息处理器（入口）───────────────────────────────────────

    async def _on_start(self, update: Update,
                        context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat:
            return
        await self.send_message(str(update.effective_chat.id), _WELCOME)

    async def _on_message(self, update: Update,
                          context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)
        text = update.message.text.strip()
        logger.info("TG 消息: user=%s text=%r", user_id, text[:60])

        # 权限校验
        if not self._is_allowed(user_id):
            from bot.permissions import DENIED_MSG
            await self.reply(chat_id, DENIED_MSG)
            return

        parts = text.split()
        cmd_word = parts[0].lower()
        has_args = len(parts) > 1

        # 无参数的已知命令 → 展示 Inline 键盘
        if not has_args and cmd_word in _KBD_CMDS:
            base_cmd, include_all, icon = _KBD_CMDS[cmd_word]
            self._set_pending(user_id, base_cmd, 1)

            if base_cmd == "speedtest":
                note = "\n⚠️ 测速会消耗约 0.5–1 GB 流量"
                kbd = self._node_kbd(base_cmd, include_all=False, icon=icon)
                await update.message.reply_text(
                    f"📶 请选择测速节点：{note}", reply_markup=kbd
                )
            elif base_cmd == "log":
                kbd = self._node_kbd(base_cmd, include_all=False, icon=icon)
                await update.message.reply_text("📋 请选择节点：", reply_markup=kbd)
            elif base_cmd == "restart":
                kbd = self._node_kbd(base_cmd, include_all=False, icon=icon)
                await update.message.reply_text("🔄 请选择节点：", reply_markup=kbd)
            else:
                kbd = self._node_kbd(base_cmd, include_all=include_all, icon=icon)
                await update.message.reply_text("🖥 请选择节点：", reply_markup=kbd)
            return

        # 有参数或未知命令 → router（支持文本方式如 /status vps2）
        try:
            result = await self._router.route("telegram", user_id, text)
            await self.reply(chat_id, result)
        except Exception:
            logger.exception("路由处理异常: user=%s", user_id)

    # ── Callback 处理器（按钮点击）─────────────────────────────

    async def _on_callback(self, update: Update,
                           context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user or not query.message:
            return

        user_id = str(query.from_user.id)

        # 权限校验（在 answer 之前，才能使用 show_alert）
        if not self._is_allowed(user_id):
            await query.answer("⛔ 无权限", show_alert=True)
            return

        await query.answer()  # 先 ack，消除按钮转圈

        # 清理全局过期 pending 状态
        self._cleanup_pending()

        # 检查当前用户是否有有效 pending
        state = self._get_pending(user_id)
        if state is None:
            await query.edit_message_text("⏰ 操作超时，请重新发送命令")
            return

        # 解析 callback_data 格式："cmd:p1:p2:..."
        data = query.data or ""
        raw_parts = data.split(":")
        cmd = raw_parts[0]
        args = raw_parts[1:]

        # cmd 与 pending.cmd 不符，说明用户点击了过期按钮
        is_cancel = cmd == "restart" and args == ["cancel"]
        if cmd != state.cmd and not is_cancel:
            await query.edit_message_text("⏰ 操作超时，请重新发送命令")
            self._clear_pending(user_id)
            return

        logger.info("TG callback: user=%s cmd=%s args=%s step=%d",
                    user_id, cmd, args, state.step)
        await self._dispatch_callback(query, user_id, cmd, args, state)

    async def _dispatch_callback(
        self,
        query,
        user_id: str,
        cmd: str,
        args: list[str],
        state: _PendingState,
    ) -> None:
        """根据 cmd 分发到对应的 callback 处理方法"""

        # 取消操作
        if cmd == "restart" and args == ["cancel"]:
            self._clear_pending(user_id)
            await query.edit_message_text("❌ 已取消重启操作")
            return

        # 单节点命令（status / docker / services / info）
        if cmd in ("status", "docker", "services", "info"):
            await self._cb_node_single(query, user_id, cmd, args)
            return

        if cmd == "speedtest":
            await self._cb_speedtest(query, user_id, args)
            return

        if cmd == "log":
            await self._cb_log(query, user_id, args, state)
            return

        if cmd == "restart":
            await self._cb_restart(query, user_id, args, state)
            return

    # ── 各命令的 callback 处理 ──────────────────────────────────

    async def _cb_node_single(self, query, user_id: str,
                               cmd: str, args: list[str]) -> None:
        """
        处理 status / docker / services / info 的节点回调（单步）。
        点击按钮后直接编辑消息为查询结果。
        """
        node_or_all = args[0] if args else "vps1"
        self._clear_pending(user_id)

        if node_or_all == "all":
            text = await self._fetch_all_nodes(cmd)
        else:
            text = await self._fetch_node(cmd, node_or_all)

        await query.edit_message_text(text[:_TG_MAX_LEN])

    async def _cb_speedtest(self, query, user_id: str,
                             args: list[str]) -> None:
        """
        处理 speedtest 节点回调：
        先编辑为"正在测速"提示，完成后更新为结果。
        """
        node = args[0] if args else "vps1"
        self._clear_pending(user_id)
        await query.edit_message_text("📶 正在测速，请稍候（约 30 秒）...")
        try:
            from bot.handlers import speedtest_handler
            result = await speedtest_handler.handle("", node)
        except ImportError:
            result = "⚠️ speedtest 功能尚未安装，请完成第三步部署"
        except Exception as e:
            result = f"❌ 测速失败：{e}"
        await query.edit_message_text(result[:_TG_MAX_LEN])

    async def _cb_log(self, query, user_id: str, args: list[str],
                      state: _PendingState) -> None:
        """
        处理 log 两步流程：
        Step 1：已选节点 → 展示服务选择键盘
        Step 2：已选服务 → 执行日志查询并展示
        """
        from bot.config import get_config

        if state.step == 1:
            node = args[0] if args else "vps1"
            label = get_config().nodes.get(node, {}).get("label", node)
            self._set_pending(user_id, "log", 2, {"node": node})
            kbd = self._service_kbd("log", node)
            await query.edit_message_text(
                f"📋 请选择服务（{label}）：", reply_markup=kbd
            )

        elif state.step == 2:
            node = args[0] if len(args) > 0 else state.params.get("node", "vps1")
            svc  = args[1] if len(args) > 1 else ""
            self._clear_pending(user_id)
            from bot.handlers import log_handler
            result = await log_handler.handle(svc, node)
            await query.edit_message_text(result[:_TG_MAX_LEN])

    async def _cb_restart(self, query, user_id: str, args: list[str],
                          state: _PendingState) -> None:
        """
        处理 restart 三步流程：
        Step 1：已选节点 → 展示服务选择键盘
        Step 2：已选服务 → 展示⚠️确认键盘
        Step 3：已确认  → 执行重启并展示结果
        """
        from bot.config import get_config

        if state.step == 1:
            node  = args[0] if args else "vps1"
            label = get_config().nodes.get(node, {}).get("label", node)
            self._set_pending(user_id, "restart", 2, {"node": node})
            kbd = self._service_kbd("restart", node)
            await query.edit_message_text(
                f"🔄 请选择服务（{label}）：", reply_markup=kbd
            )

        elif state.step == 2:
            node  = args[0] if len(args) > 0 else state.params.get("node", "vps1")
            svc   = args[1] if len(args) > 1 else ""
            label = get_config().nodes.get(node, {}).get("label", node)
            self._set_pending(user_id, "restart", 3, {"node": node, "svc": svc})
            kbd = self._confirm_restart_kbd(node, svc)
            await query.edit_message_text(
                f"⚠️ 确认重启？\n\n"
                f"  服务：{svc}\n"
                f"  节点：{label}\n\n"
                f"此操作会中断该服务，请确认。",
                reply_markup=kbd,
            )

        elif state.step == 3:
            # args = [node, svc, "confirm"]
            if len(args) >= 3 and args[2] == "confirm":
                node = args[0]
                svc  = args[1]
                self._clear_pending(user_id)
                from bot.handlers import restart_handler
                result = await restart_handler.handle(svc, node)
                await query.edit_message_text(result[:_TG_MAX_LEN])

    # ── 数据获取辅助 ─────────────────────────────────────────────

    async def _fetch_node(self, cmd: str, node: str) -> str:
        """调用对应 handler 获取单节点数据"""
        if cmd == "status":
            from bot.handlers import status
            return await status.handle("", node)
        if cmd == "docker":
            from bot.handlers import docker_handler
            return await docker_handler.handle("", node)
        if cmd == "services":
            from bot.handlers import services_handler
            return await services_handler.handle("", node)
        if cmd == "info":
            try:
                from bot.handlers import info_handler
                return await info_handler.handle("", node)
            except ImportError:
                return "ℹ️ /info 功能将在后续步骤部署"
        return "❌ 未知命令"

    async def _fetch_all_nodes(self, cmd: str) -> str:
        """并行查询所有节点并合并结果（用于"全部汇总"按钮）"""
        from bot.config import get_config
        cfg = get_config()
        node_names = list(cfg.nodes.keys())
        results = await asyncio.gather(
            *[self._fetch_node(cmd, name) for name in node_names],
            return_exceptions=True,
        )
        parts: list[str] = []
        for name, result in zip(node_names, results):
            if isinstance(result, Exception):
                label = cfg.nodes[name].get("label", name)
                parts.append(f"❌ {label}：{result}")
            else:
                parts.append(result)
        return "\n\n".join(parts)

    # ── 命令注册 ─────────────────────────────────────────────────

    async def _register_commands(self) -> None:
        """启动时向 BotFather 注册命令列表"""
        try:
            await self._app.bot.set_my_commands(_BOT_COMMANDS)
            logger.info("Telegram 命令列表已向 BotFather 注册")
        except Exception as e:
            logger.warning("注册命令列表失败: %s", e)

    # ── 启动 ─────────────────────────────────────────────────────

    async def start_polling(self) -> None:
        """
        异步协程，供 main.py 的 asyncio.gather() 调用。
        连接失败时每 15 秒重试，CancelledError 时优雅退出。
        """
        _RECONNECT_DELAY = 15
        while True:
            try:
                logger.info("Telegram Bot 开始 polling...")
                await self._app.initialize()
                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                )
                await self._app.start()
                await self._register_commands()
                try:
                    await asyncio.Event().wait()  # 阻塞直到外部 cancel
                except asyncio.CancelledError:
                    logger.info("Telegram Bot 收到停止信号")
                    return
                finally:
                    await self._app.stop()
                    await self._app.updater.stop()
                    await self._app.shutdown()
                    logger.info("Telegram Bot 已停止")
                return

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Telegram Bot 连接失败 (%s)，%d 秒后重试...",
                               e, _RECONNECT_DELAY)

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
            self._app = self._build_app(self._token)
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
