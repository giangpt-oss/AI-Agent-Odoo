from typing import Any
from app.services.gateway_client import GatewayClient, gateway_client


class GatewayGmailProvider:
    """Provider tra cứu và quản lý hộp thư Gmail qua Gateway."""

    def __init__(self, client: GatewayClient | None = None):
        self.client = client or gateway_client

    async def get_unread(self, user_id: int) -> dict[str, Any]:
        return await self.client.get_unread_emails(user_id=user_id)
