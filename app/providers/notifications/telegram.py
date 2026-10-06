from app.providers.notifications.base import NotificationProvider
from app.connectors.telegram.client import get_telegram_connector
import logging

logger = logging.getLogger(__name__)

class TelegramNotificationProvider(NotificationProvider):
    async def send_notification(self, user_id: str, message: str) -> bool:
        # User ID is assumed to be the telegram chat_id in this context
        try:
            chat_id = int(user_id)
        except ValueError:
            logger.error(f"Invalid user_id for Telegram notification: {user_id}")
            return False
            
        connector = get_telegram_connector()
        return await connector.send_message(chat_id=chat_id, text=message)
