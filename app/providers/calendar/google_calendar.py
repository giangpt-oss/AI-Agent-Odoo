from typing import List, Dict, Any, Optional
import httpx
from datetime import datetime
from app.providers.calendar.base import CalendarProvider

class GoogleCalendarProvider(CalendarProvider):
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://www.googleapis.com/calendar/v3"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=15.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
        return self._client

    async def close(self) -> None:
        """Đóng kết nối HTTP nếu cần giải phóng tài nguyên."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def list_events(self, start_time: datetime, end_time: datetime, limit: int = 10, query: str = None) -> Dict[str, Any]:
        params = {
            "timeMin": start_time.isoformat(),
            "timeMax": end_time.isoformat(),
            "maxResults": limit,
            "singleEvents": True,
            "orderBy": "startTime"
        }
        if query:
            params["q"] = query
            
        client = self._get_client()
        resp = await client.get(f"{self.base_url}/calendars/primary/events", headers=self.headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_event(self, event_id: str) -> Dict[str, Any]:
        client = self._get_client()
        resp = await client.get(f"{self.base_url}/calendars/primary/events/{event_id}", headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def find_free_time(self, start_time: datetime, end_time: datetime, duration_minutes: int) -> List[Dict[str, datetime]]:
        payload = {
            "timeMin": start_time.isoformat(),
            "timeMax": end_time.isoformat(),
            "items": [{"id": "primary"}]
        }
        client = self._get_client()
        resp = await client.post(f"{self.base_url}/freeBusy", headers=self.headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
            
        # Simplified free time calculation: just returning the busy slots for the skill to process
        # A full find_free_time would invert the busy slots.
        return data.get("calendars", {}).get("primary", {}).get("busy", [])

    async def create_event(self, title: str, start_time: datetime, end_time: datetime, timezone: str, location: str = None, description: str = None, attendees: List[str] = None) -> str:
        payload = {
            "summary": title,
            "start": {"dateTime": start_time.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end_time.isoformat(), "timeZone": timezone},
        }
        if location: payload["location"] = location
        if description: payload["description"] = description
        if attendees: payload["attendees"] = [{"email": a} for a in attendees]
        
        client = self._get_client()
        resp = await client.post(f"{self.base_url}/calendars/primary/events", headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json().get("id")

    async def update_event(self, event_id: str, updates: Dict[str, Any]) -> str:
        client = self._get_client()
        resp = await client.patch(f"{self.base_url}/calendars/primary/events/{event_id}", headers=self.headers, json=updates)
        resp.raise_for_status()
        return resp.json().get("id")

    async def cancel_event(self, event_id: str) -> bool:
        client = self._get_client()
        resp = await client.delete(f"{self.base_url}/calendars/primary/events/{event_id}", headers=self.headers)
        resp.raise_for_status()
        return True
