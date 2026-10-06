from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class ReminderProvider(ABC):
    @abstractmethod
    def create_reminder(self, task_id: Optional[str], title: str, remind_at: str, timezone: str, recurrence: Optional[str]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_reminder(self, reminder_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def list_reminders(self, status: str = None) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def update_reminder(self, reminder_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def cancel_reminder(self, reminder_id: str) -> bool:
        pass
