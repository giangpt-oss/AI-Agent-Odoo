from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class MeetingRepository(ABC):
    @abstractmethod
    def create_meeting(self, title: str, start_at: Optional[str], end_at: Optional[str], timezone: str, participants: List[str], agenda: List[str], source_type: str, source_ids: List[str]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_meeting(self, meeting_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def list_meetings(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def update_meeting(self, meeting_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        pass
