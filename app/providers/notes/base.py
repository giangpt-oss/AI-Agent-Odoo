from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class NoteRepository(ABC):
    @abstractmethod
    def create_note(self, title: str, content: str, note_type: str, tags: List[str], meeting_id: Optional[str], project: Optional[str]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_note(self, note_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def list_notes(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def update_note(self, note_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        pass
