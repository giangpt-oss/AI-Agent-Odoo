import logging
from typing import Any
import httpx
from app.connectors.google.client import (
    GOOGLE_GMAIL_API_URL,
    GOOGLE_CALENDAR_API_URL,
    GoogleOAuthService,
)

logger = logging.getLogger(__name__)


class GoogleConnector:
    """Connector giao tiếp với Gmail và Calendar API của Google."""

    def __init__(self, oauth_service: GoogleOAuthService | None = None):
        self.oauth_service = oauth_service or GoogleOAuthService()
        self._http_client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=10.0,
                limits=httpx.Limits(max_keepalive_connections=15, max_connections=30)
            )
        return self._http_client

    async def search_gmail_messages(
        self,
        access_token: str,
        query: str,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Tìm kiếm danh sách email theo từ khóa với concurrent fetching (asyncio.gather)."""
        import asyncio
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"q": query, "maxResults": max_results}
        client = self._get_client()

        resp = await client.get(f"{GOOGLE_GMAIL_API_URL}/messages", headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("messages", [])
        if not items:
            return []

        # Tải chi tiết các email song song cùng lúc thay vì tuần tự
        async def _fetch_single_message(item_dict: dict[str, Any]) -> dict[str, Any] | None:
            msg_id = item_dict.get("id")
            try:
                detail_resp = await client.get(
                    f"{GOOGLE_GMAIL_API_URL}/messages/{msg_id}",
                    headers=headers,
                    params={"format": "metadata"},
                )
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
                    headers_list = detail.get("payload", {}).get("headers", [])
                    subject = next((h["value"] for h in headers_list if h["name"].lower() == "subject"), "(Không có tiêu đề)")
                    sender = next((h["value"] for h in headers_list if h["name"].lower() == "from"), "Unknown")
                    return {
                        "id": msg_id,
                        "subject": subject,
                        "from": sender,
                        "snippet": detail.get("snippet", ""),
                    }
            except Exception as e:
                logger.warning(f"Error fetching detail for message {msg_id}: {e}")
            return None

        tasks = [_fetch_single_message(item) for item in items]
        raw_results = await asyncio.gather(*tasks)
        return [msg for msg in raw_results if msg is not None]

    async def get_calendar_events(
        self,
        access_token: str,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Lấy danh sách các sự kiện lịch sắp tới dùng client tái sử dụng."""
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        client = self._get_client()
        resp = await client.get(
            f"{GOOGLE_CALENDAR_API_URL}/calendars/primary/events",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

        events = []
        for ev in items:
            events.append({
                "id": ev.get("id"),
                "summary": ev.get("summary", "(Không có tiêu đề)"),
                "start": ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date"),
                "end": ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date"),
            })
        return events


default_google_connector = GoogleConnector()


def get_google_connector() -> GoogleConnector:
    return default_google_connector
