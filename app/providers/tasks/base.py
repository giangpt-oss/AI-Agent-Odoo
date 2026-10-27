from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class TaskProvider(ABC):
    @abstractmethod
    def create_task(self, title: str, description: str, priority: str, due_at: Optional[str], timezone: str, project: Optional[str], tags: List[str], owner_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_task(self, task_id: str, owner_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def list_tasks(self, filters: Dict[str, Any], owner_key: Optional[str] = None) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def update_task(self, task_id: str, updates: Dict[str, Any], owner_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def complete_task(self, task_id: str, owner_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def delete_task(self, task_id: str, owner_key: Optional[str] = None) -> bool:
        pass
