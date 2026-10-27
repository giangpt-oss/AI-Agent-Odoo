from typing import Any
from app.services.gateway_client import GatewayClient, gateway_client


class GatewayCRMProvider:
    """Provider tra cứu cơ hội CRM từ Odoo qua Gateway."""

    def __init__(self, client: GatewayClient | None = None):
        self.client = client or gateway_client

    async def search_leads(
        self, user_id: int, search_term: str | None = None, limit: int = 5
    ) -> dict[str, Any]:
        return await self.client.get_crm_leads(user_id=user_id, search_term=search_term, limit=limit)
