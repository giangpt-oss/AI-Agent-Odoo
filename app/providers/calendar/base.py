from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime

class CalendarProvider(ABC):
    @abstractmethod
    def list_events(self, start_time: datetime, end_time: datetime, limit: int = 10, query: str = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_event(self, event_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def find_free_time(self, start_time: datetime, end_time: datetime, duration_minutes: int) -> List[Dict[str, datetime]]:
        pass

    @abstractmethod
    def create_event(self, title: str, start_time: datetime, end_time: datetime, timezone: str, location: str = None, description: str = None, attendees: List[str] = None) -> str:
        pass

    @abstractmethod
    def update_event(self, event_id: str, updates: Dict[str, Any]) -> str:
        pass

    @abstractmethod
    def cancel_event(self, event_id: str) -> bool:
        pass
