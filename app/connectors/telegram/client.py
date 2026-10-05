import logging
from typing import Any
import httpx
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class TelegramConnector:
    """Connector giao tiếp với Telegram Bot API qua HTTP."""

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

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(f"{self.api_url}/sendMessage", json=payload)
                resp.raise_for_status()
                return True
            except Exception as e:
                logger.error(f"Failed to send Telegram message to {chat_id}: {e}")
                return False


telegram_connector = TelegramConnector()


def get_telegram_connector() -> TelegramConnector:
    return telegram_connector
