from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import uuid
from app.providers.calendar.base import CalendarProvider

class FakeCalendarProvider(CalendarProvider):
    def __init__(self):
        now = datetime.now()
        self.events = {
            "evt1": {"id": "evt1", "title": "Team Sync", "start": (now + timedelta(hours=1)).isoformat(), "end": (now + timedelta(hours=2)).isoformat(), "timezone": "Asia/Ho_Chi_Minh", "location": "Google Meet", "attendees": ["me@company.com", "team@company.com"]},
            "evt2": {"id": "evt2", "title": "Client Meeting", "start": (now + timedelta(days=1, hours=2)).isoformat(), "end": (now + timedelta(days=1, hours=3)).isoformat(), "timezone": "Asia/Ho_Chi_Minh", "location": "Zoom", "attendees": ["client@company.com"]}
        }
        self.cancelled = []

    def list_events(self, start_time: datetime, end_time: datetime, limit: int = 10, query: str = None) -> Dict[str, Any]:
        results = []
        for e in self.events.values():
            e_start = datetime.fromisoformat(e["start"])
            # Remove tzinfo for simple fake comparison if needed, or assume naive=local
            if e_start.tzinfo is None and start_time.tzinfo is not None:
                start_time = start_time.replace(tzinfo=None)
                end_time = end_time.replace(tzinfo=None)
                
            if start_time <= e_start <= end_time:
                if query:
                    if query.lower() in e["title"].lower():
                        results.append(e)
                else:
                    results.append(e)
        return {"events": results[:limit]}

    def get_event(self, event_id: str) -> Dict[str, Any]:
        if event_id in self.events:
            return self.events[event_id]
        raise Exception(f"Event {event_id} not found")

    def find_free_time(self, start_time: datetime, end_time: datetime, duration_minutes: int) -> List[Dict[str, datetime]]:
        # Simplified mock: just return the requested start_time + duration if it doesn't overlap exactly
        return [{"start": start_time.isoformat(), "end": (start_time + timedelta(minutes=duration_minutes)).isoformat()}]

    def create_event(self, title: str, start_time: datetime, end_time: datetime, timezone: str, location: str = None, description: str = None, attendees: List[str] = None) -> str:
        evt_id = f"evt_{uuid.uuid4().hex[:8]}"
        self.events[evt_id] = {
            "id": evt_id,
            "title": title,
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "timezone": timezone,
            "location": location or "",
            "description": description or "",
            "attendees": attendees or []
        }
        return evt_id

    def update_event(self, event_id: str, updates: Dict[str, Any]) -> str:
        if event_id not in self.events:
            raise Exception("Event not found")
        
        self.events[event_id].update(updates)
        return event_id

    def cancel_event(self, event_id: str) -> bool:
        if event_id in self.events:
            self.cancelled.append(event_id)
            del self.events[event_id]
            return True
        return False
