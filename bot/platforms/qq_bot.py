"""
QQ Bot — 直接对接 OneBot V11 协议（WebSocket 收发，单连接）
不引入 NoneBot2 框架，与现有 asyncio 任务池无缝共存。

后端支持：NapCat / LLOneBot / Lagrange / go-cqhttp
"""
import asyncio
import json
import logging
import re
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from bot.platforms.base import MessagePlatform

logger = logging.getLogger(__name__)

_QQ_MAX_LEN = 4000       # QQ 单条消息字符上限（官方约 4096，保守取 4000）
_RECONNECT_DELAY = 10    # 断线重连等待秒数


def _extract_text(data: dict[str, Any]) -> str:
    """从 OneBot V11 消息中提取纯文本，过滤 CQ 码"""
    # raw_message 是 CQ 码格式字符串，去掉非文本部分
    raw = data.get("raw_message", "")
    text = re.sub(r"\[CQ:[^\]]+\]", "", raw).strip()
    if text:
        return text
    # 降级：从 message segments 数组中拼接 text 片段
    parts = [
        seg.get("data", {}).get("text", "")
        for seg in data.get("message", [])
        if isinstance(seg, dict) and seg.get("type") == "text"
    ]
    return "".join(parts).strip()


def _split_message(text: str) -> list[str]:
    if len(text) <= _QQ_MAX_LEN:
        return [text]
    chunks: list[str] = []
    while text:
        chunks.append(text[:_QQ_MAX_LEN])
        text = text[_QQ_MAX_LEN:]
    return chunks


class QQPlatform(MessagePlatform):

    def __init__(self, ws_url: str, http_url: str, router, access_token: str = "") -> None:
        self._ws_url = ws_url
        self._router = router  # CommandRouter
        self._access_token = access_token
        # Authorization header（OneBot 实现均支持 Bearer token）
        self._headers: dict[str, str] = (
            {"Authorization": f"Bearer {access_token}"} if access_token else {}
        )
        self._ws = None          # 当前 WebSocket 连接（发消息用）
        self._ws_lock: asyncio.Lock | None = None

    def _lock(self) -> asyncio.Lock:
        if self._ws_lock is None:
            self._ws_lock = asyncio.Lock()
        return self._ws_lock

    # ── MessagePlatform 接口 ────────────────────────────────────────

    @property
    def platform_name(self) -> str:
        return "qq"

    async def broadcast(self, text: str, user_ids: list[str]) -> None:
        """QQ 私聊需要 private_{QQ_ID} 格式"""
        for uid in user_ids:
            await self.reply(f"private_{uid}", text)

    async def send_message(self, chat_id: str, text: str) -> None:
        """
        chat_id 格式：
          "private_<QQ号>"  → 私聊
          "group_<群号>"    → 群聊

        发送走 WebSocket（与接收共用同一条连接），无需 HTTP API。
        """
        for chunk in _split_message(text):
            if chat_id.startswith("private_"):
                action = "send_private_msg"
                params: dict[str, Any] = {
                    "user_id": int(chat_id[len("private_"):]),
                    "message": chunk,
                }
            elif chat_id.startswith("group_"):
                action = "send_group_msg"
                params = {
                    "group_id": int(chat_id[len("group_"):]),
                    "message": chunk,
                }
            else:
                logger.warning("未知 chat_id 格式: %s", chat_id)
                return

            try:
                await self._ws_call(action, params, chat_id)
            except Exception:
                logger.exception("send_message 失败 chat_id=%s", chat_id)

    async def _ws_call(self, action: str, params: dict[str, Any], chat_id: str) -> None:
        """通过已连接的 WebSocket 调用 OneBot V11 API"""
        ws = self._ws
        if ws is None:
            logger.error("WebSocket 未连接，无法发送 chat_id=%s", chat_id)
            return
        payload = json.dumps({"action": action, "params": params})
        async with self._lock():
            await ws.send(payload)

    async def send_code_block(self, chat_id: str, code: str, lang: str = "") -> None:
        # QQ 不支持 Markdown，降级为带标记的纯文本
        prefix = f"[代码/{lang}]\n" if lang else "[代码]\n"
        await self.send_message(chat_id, prefix + code)

    # ── 内部消息处理 ───────────────────────────────────────────────

    async def _handle_event(self, raw: str) -> None:
        """解析单条 OneBot V11 事件，非消息事件静默忽略"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # 只处理消息事件
        if data.get("post_type") != "message":
            return

        msg_type = data.get("message_type")
        if msg_type not in ("private", "group"):
            return

        user_id = str(data.get("user_id", ""))
        text = _extract_text(data)
        if not text:
            return

        if msg_type == "private":
            chat_id = f"private_{user_id}"
        else:
            chat_id = f"group_{data.get('group_id', '')}"

        logger.info("QQ 消息: user=%s type=%s text=%r", user_id, msg_type, text[:60])
        try:
            result = await self._router.route("qq", user_id, text)
            await self.reply(chat_id, result)
        except Exception:
            logger.exception("QQ 路由处理异常: user=%s", user_id)

    async def _listen(self, ws) -> None:
        """持续接收 WebSocket 消息，同时持有连接引用供发消息使用"""
        self._ws = ws
        try:
            async for raw in ws:
                await self._handle_event(raw)
        finally:
            self._ws = None

    # ── 启动（含断线重连）─────────────────────────────────────────

    async def start_polling(self) -> None:
        """
        异步协程，供 main.py 的 asyncio.gather() 调用。
        连接失败时每 10 秒重试，CancelledError 时优雅退出。
        """
        if not self._ws_url:
            logger.info("QQ_WS_URL 未配置，跳过 QQ Bot 启动")
            return
        logger.info("QQ Bot 连接 WebSocket: %s", self._ws_url)
        while True:
            try:
                async with websockets.connect(
                    self._ws_url,
                    extra_headers=self._headers,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    logger.info("QQ Bot WebSocket 已连接")
                    await self._listen(ws)
                    logger.info("QQ Bot WebSocket 连接关闭，准备重连...")
            except asyncio.CancelledError:
                logger.info("QQ Bot 收到停止信号，退出")
                return
            except ConnectionClosed as e:
                logger.warning("QQ Bot 连接断开 (%s)，%d 秒后重试...", e, _RECONNECT_DELAY)
            except OSError as e:
                logger.warning("QQ Bot 无法连接 (%s)，%d 秒后重试...", e, _RECONNECT_DELAY)
            except Exception:
                logger.exception("QQ Bot 意外异常，%d 秒后重试...", _RECONNECT_DELAY)

            try:
                await asyncio.sleep(_RECONNECT_DELAY)
            except asyncio.CancelledError:
                logger.info("QQ Bot 重连等待中收到停止信号，退出")
                return
