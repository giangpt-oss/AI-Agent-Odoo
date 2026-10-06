from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class EmailProvider(ABC):
    @abstractmethod
    def list_messages(self, limit: int = 10, page_token: str = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def search_messages(self, query: str, limit: int = 10) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_message(self, message_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def create_draft(self, to: List[str], subject: str, body: str, cc: List[str] = None, reply_to_message_id: str = None) -> str:
        pass

    @abstractmethod
    def send_message(self, to: List[str], subject: str, body: str, cc: List[str] = None, attachments: List[str] = None) -> str:
        pass

    @abstractmethod
    def reply_message(self, original_message_id: str, thread_id: str, body: str, attachments: List[str] = None) -> str:
        pass

    @abstractmethod
    def archive_message(self, message_id: str) -> bool:
        pass
