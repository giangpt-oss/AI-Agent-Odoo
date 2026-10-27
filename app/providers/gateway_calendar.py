from typing import Any
from app.services.gateway_client import GatewayClient, gateway_client


class GatewayCalendarProvider:
    """Provider tra cứu và quản lý lịch Google Calendar qua Gateway."""

    def __init__(self, client: GatewayClient | None = None):
        self.client = client or gateway_client

    async def get_events(self, user_id: int, time_min: str, time_max: str) -> dict[str, Any]:
        return await self.client.get_calendar_events(user_id=user_id, time_min=time_min, time_max=time_max)
