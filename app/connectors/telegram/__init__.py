"""Telegram Bot Connector package."""
from app.connectors.telegram.client import TelegramConnector, get_telegram_connector, telegram_connector

__all__ = ["TelegramConnector", "get_telegram_connector", "telegram_connector"]
