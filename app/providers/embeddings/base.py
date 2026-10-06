from abc import ABC, abstractmethod
from typing import List

class EmbeddingProvider(ABC):
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        pass
        
    @property
    @abstractmethod
    def dimension(self) -> int:
        pass
        
    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        """Embed a single piece of text."""
        pass
        
    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts."""
        pass
