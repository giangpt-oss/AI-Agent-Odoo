from abc import ABC, abstractmethod
from typing import List, Dict, Any
from app.knowledge.models import KnowledgeChunk

class VectorStore(ABC):
    
    @abstractmethod
    def upsert(self, chunks: List[KnowledgeChunk], embeddings: List[List[float]]) -> None:
        pass
        
    @abstractmethod
    def search(self, query_embedding: List[float], top_k: int = 5, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Return list of dict containing chunk info and distance/score."""
        pass
        
    @abstractmethod
    def delete_source(self, source_id: str) -> None:
        pass
        
    @abstractmethod
    def delete_chunks(self, chunk_ids: List[str]) -> None:
        pass
        
    @abstractmethod
    def count(self) -> int:
        pass
        
    @abstractmethod
    def health_check(self) -> bool:
        pass
