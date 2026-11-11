import os
from typing import List
from google import genai
from google.genai import types
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.gemini_retry import call_gemini, GeminiRequestError

class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model: str = "gemini-embedding-001", api_key: str = None):
        self._model = model
        self._api_key = api_key
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from app.core.config import get_settings
            settings = get_settings()
            key = self._api_key or getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
            if not key:
                raise GeminiRequestError("configuration")
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=8000, retry_options=types.HttpRetryOptions(attempts=1)))
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
            
        return await call_gemini("embedding", _sync_embed)
        
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        def _sync_embed():
            result = self.client.models.embed_content(
                model=self._model,
                contents=texts,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            return [e.values for e in result.embeddings]
            
        return await call_gemini("embedding", _sync_embed)

default_embedding_provider = GeminiEmbeddingProvider()
