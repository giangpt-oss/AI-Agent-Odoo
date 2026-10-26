import logging
from typing import Any
import httpx
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class GatewayServiceError(Exception):
    """Lỗi dịch vụ phía Gateway (timeout, không thể kết nối hoặc HTTP 5xx)."""
    pass


class GatewayClient:
    """Client kết nối trực tiếp đến External Auth Gateway qua các endpoint nội bộ."""

    def __init__(self, base_url: str | None = None, secret: str | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.GATEWAY_BASE_URL).rstrip("/")
        self.secret = secret or settings.AGENT_TO_GATEWAY_SECRET

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secret}",
            "Content-Type": "application/json",
        }

    async def resolve_identity(self, telegram_user_id: int, chat_id: int) -> dict[str, Any] | None:
        """Xác minh danh tính nhân viên qua Telegram user ID và private chat ID."""
        url = f"{self.base_url}/internal/v1/identity/resolve"
        payload = {"telegram_user_id": telegram_user_id, "chat_id": chat_id}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (401, 404):
                    logger.warning("Gateway identity unlinked (HTTP %s): %s", resp.status_code, resp.text)
                    return None
                if resp.status_code >= 500:
                    raise GatewayServiceError(f"Gateway returned HTTP {resp.status_code}: {resp.text}")
                logger.warning("Gateway identity resolve returned %s: %s", resp.status_code, resp.text)
                return None
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
            logger.error("Network error calling Gateway identity resolve: %s", e)
            raise GatewayServiceError(f"Không thể kết nối đến Gateway: {e}") from e
        except GatewayServiceError:
            raise
        except Exception as e:
            logger.error("Error calling Gateway identity resolve: %s", e)
            return None

    async def get_user_connections(self, user_id: int) -> dict[str, Any]:
        """Lấy trạng thái kết nối Google, Telegram, Odoo của người dùng."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/connections"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "message": resp.text}
        except Exception as e:
            logger.error("Error calling Gateway user connections: %s", e)
            return {"status": "ERROR", "message": str(e)}

    async def get_calendar_events(self, user_id: int, time_min: str, time_max: str) -> dict[str, Any]:
        """Lấy danh sách sự kiện lịch trong khoảng thời gian ISO 8601."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/calendar/events"
        params = {"time_min": time_min, "time_max": time_max}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "events": [], "message": resp.text}
        except httpx.TimeoutException:
            logger.warning("Gateway calendar request timed out for user %s", user_id)
            return {"status": "TIMEOUT", "events": []}
        except Exception as e:
            logger.error("Error calling Gateway calendar events: %s", e)
            return {"status": "ERROR", "events": [], "message": str(e)}

    async def get_unread_emails(self, user_id: int) -> dict[str, Any]:
        """Lấy số lượng thư chưa đọc và tối đa 5 thư gần nhất qua Gmail."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/gmail/unread"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "total_unread": 0, "messages": [], "message": resp.text}
        except httpx.TimeoutException:
            logger.warning("Gateway gmail request timed out for user %s", user_id)
            return {"status": "TIMEOUT", "total_unread": 0, "messages": []}
        except Exception as e:
            logger.error("Error calling Gateway gmail unread: %s", e)
            return {"status": "ERROR", "total_unread": 0, "messages": [], "message": str(e)}


gateway_client = GatewayClient()
