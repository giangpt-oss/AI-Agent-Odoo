import os
import asyncio
from typing import List
from google import genai
from google.genai import types
from app.providers.embeddings.base import EmbeddingProvider

class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model: str = "gemini-embedding-001", api_key: str = None):
        self._model = model
        self._api_key = api_key
        self._client = None

    @property
    def client(self):
        if self._client is None:
            key = self._api_key or os.getenv("GEMINI_API_KEY")
            if not key:
                raise ValueError("GEMINI_API_KEY is missing.")
            self._client = genai.Client(api_key=key)
        return self._client
        
    @property
    def model_name(self) -> str:
        return self._model
        
    @property
    def dimension(self) -> int:
        return 768
        
    async def embed_text(self, text: str) -> List[float]:
        def _sync_embed():
            result = self.client.models.embed_content(
                model=self._model,
                contents=text,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            return result.embeddings[0].values
            
        return await asyncio.to_thread(_sync_embed)
        
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        def _sync_embed():
            result = self.client.models.embed_content(
                model=self._model,
                contents=texts,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            return [e.values for e in result.embeddings]
            
        return await asyncio.to_thread(_sync_embed)

default_embedding_provider = GeminiEmbeddingProvider()
