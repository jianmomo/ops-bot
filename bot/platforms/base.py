from abc import ABC, abstractmethod

MAX_MESSAGE_LEN = 4000
TRUNCATION_SUFFIX = "\n...(已截断)"


class MessagePlatform(ABC):
    """平台无关的消息发送抽象接口"""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台标识，如 'telegram' 或 'qq'"""

    @abstractmethod
    async def send_message(self, chat_id: str, text: str) -> None:
        """发送纯文本消息"""

    @abstractmethod
    async def send_code_block(self, chat_id: str, code: str, lang: str = "") -> None:
        """发送代码块（各平台自行处理格式）"""

    async def reply(self, chat_id: str, text: str) -> None:
        """发送消息，超过 MAX_MESSAGE_LEN 时自动截断"""
        if len(text) > MAX_MESSAGE_LEN:
            text = text[: MAX_MESSAGE_LEN - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX
        await self.send_message(chat_id, text)
