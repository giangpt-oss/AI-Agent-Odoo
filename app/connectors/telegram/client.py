import logging
from typing import Any
import httpx
from app.core.config import get_settings

logger = logging.getLogger(__name__)


_SHARED_TELEGRAM_CLIENT: httpx.AsyncClient | None = None


def get_shared_telegram_http_client(timeout: float = 10.0) -> httpx.AsyncClient:
    global _SHARED_TELEGRAM_CLIENT
    if _SHARED_TELEGRAM_CLIENT is None or _SHARED_TELEGRAM_CLIENT.is_closed:
        _SHARED_TELEGRAM_CLIENT = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _SHARED_TELEGRAM_CLIENT


class TelegramConnector:
    """Connector giao tiếp với Telegram Bot API qua HTTP với kết nối tái sử dụng."""

    def __init__(self, bot_token: str | None = None):
        self.settings = get_settings()
        self.bot_token = bot_token or self.settings.TELEGRAM_BOT_TOKEN
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str = "Markdown",
    ) -> bool:
        """Gửi tin nhắn văn bản về cho người dùng."""
        if not self.bot_token:
            logger.info(f"[Mock Telegram] To {chat_id}: {text}")
            return True

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        client = get_shared_telegram_http_client(timeout=10.0)
        try:
            resp = await client.post(f"{self.api_url}/sendMessage", json=payload)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message to {chat_id}: {e}")
            return False

    async def close(self) -> None:
        """Đóng kết nối HTTP nếu cần giải phóng tài nguyên."""
        global _SHARED_TELEGRAM_CLIENT
        if _SHARED_TELEGRAM_CLIENT and not _SHARED_TELEGRAM_CLIENT.is_closed:
            await _SHARED_TELEGRAM_CLIENT.aclose()
            _SHARED_TELEGRAM_CLIENT = None


telegram_connector = TelegramConnector()


def get_telegram_connector() -> TelegramConnector:
    return telegram_connector
