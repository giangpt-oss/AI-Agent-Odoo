from abc import ABC, abstractmethod
from typing import List, Optional
from app.memory.models import MemoryRecord, MemoryCandidate

class MemoryStore(ABC):
    
    @abstractmethod
    def create(self, memory: MemoryRecord) -> None:
        pass
        
    @abstractmethod
    def get(self, memory_id: str, user_id: str) -> Optional[MemoryRecord]:
        pass
        
    @abstractmethod
    def get_by_key(self, key: str, user_id: str, workspace_id: Optional[str] = None) -> Optional[MemoryRecord]:
        pass
        
    @abstractmethod
    def search(self, query: str, user_id: str, workspace_id: Optional[str] = None) -> List[MemoryRecord]:
        pass
        
    @abstractmethod
    def list(self, user_id: str, workspace_id: Optional[str] = None) -> List[MemoryRecord]:
        pass
        
    @abstractmethod
    def supersede(self, old_memory_id: str, new_memory: MemoryRecord) -> None:
        pass
        
    @abstractmethod
    def delete(self, memory_id: str, user_id: str) -> bool:
        pass
        
    @abstractmethod
    def expire_old_memories(self) -> int:
        pass
