import re
from typing import List, Optional, Dict, Any
from app.memory.models import MemoryRecord, MemoryStatus, MemorySourceType
from app.providers.memory.sqlite import sqlite_memory_store

class MemoryException(Exception):
    pass

class SensitiveMemoryError(MemoryException):
    pass

class MemoryService:
    # Basic regex for sensitive data
    SENSITIVE_PATTERNS = [
        re.compile(r"(?i)password\s*['\"]?\s*[:=]\s*['\"]?\w+"),
        re.compile(r"(?i)api_key\s*['\"]?\s*[:=]\s*['\"]?\w+"),
        re.compile(r"(?i)bearer\s+[A-Za-z0-9\-\._~\+\/]+=*"),
        re.compile(r"(?i)sk-[a-zA-Z0-9]{20,}"), # typical secret keys
    ]

    def _contains_sensitive_data(self, text: str) -> bool:
        for pattern in self.SENSITIVE_PATTERNS:
            if pattern.search(text):
                return True
        return False

    def save_memory(self, memory: MemoryRecord) -> MemoryRecord:
        # 1. Provenance check
        if not memory.source_type:
            raise MemoryException("Memory must have a source_type.")
            
        # 2. Sensitive check
        value_str = str(memory.value)
        if self._contains_sensitive_data(value_str) or self._contains_sensitive_data(memory.summary):
            raise SensitiveMemoryError("Cannot store sensitive information in Memory.")

        # 3. Deduplication / Conflict Resolution
        # If there is already an ACTIVE memory with the same key, user_id, workspace_id, we supersede it.
        existing = sqlite_memory_store.get_by_key(memory.key, memory.user_id, memory.workspace_id)
        
        if existing:
            # Check if it's literally the same value, then we just update timestamp
            if existing.value == memory.value and existing.category == memory.category:
                # Deduplicate: just return existing
                return existing
                
            # Otherwise, supersede
            memory.previous_memory_id = existing.memory_id
            sqlite_memory_store.supersede(existing.memory_id, memory)
        else:
            sqlite_memory_store.create(memory)
            
        return memory

    def get_memory(self, memory_id: str, user_id: str) -> Optional[MemoryRecord]:
        return sqlite_memory_store.get(memory_id, user_id)

    def search_memories(self, query: str, user_id: str, workspace_id: Optional[str] = None) -> List[MemoryRecord]:
        # Expire old memories before searching
        sqlite_memory_store.expire_old_memories()
        return sqlite_memory_store.search(query, user_id, workspace_id)

    def list_memories(self, user_id: str, workspace_id: Optional[str] = None) -> List[MemoryRecord]:
        sqlite_memory_store.expire_old_memories()
        return sqlite_memory_store.list(user_id, workspace_id)

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        return sqlite_memory_store.delete(memory_id, user_id)

memory_service = MemoryService()
