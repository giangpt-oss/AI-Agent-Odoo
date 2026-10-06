import httpx
from typing import Optional, Dict, Any
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception, retry_if_exception_type

# Lỗi được retry
class HttpRetryableError(Exception):
    pass

def should_retry_error(exception: BaseException) -> bool:
    if isinstance(exception, (httpx.TimeoutException, httpx.NetworkError, HttpRetryableError)):
        return True
    return False

class RetryableHttpClient:
    """HTTP Client Wrapper với Exponential Backoff & Jitter."""
    
    def __init__(self, max_attempts: int = 4, timeout: float = 30.0):
        self.max_attempts = max_attempts
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=self.timeout)
        
    async def _execute_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        @retry(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception(should_retry_error),
            reraise=True
        )
        async def _do_request():
            try:
                response = await self._client.request(method, url, **kwargs)
                if response.status_code in (429, 502, 503, 504):
                    raise HttpRetryableError(f"Status {response.status_code}")
                # Không retry 400, 401, 403, 404
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 401, 403, 404):
                    raise e
                raise
        
        return await _do_request()

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self._execute_with_retry("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self._execute_with_retry("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        return await self._execute_with_retry("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        return await self._execute_with_retry("DELETE", url, **kwargs)

    async def close(self):
        await self._client.aclose()

retry_client = RetryableHttpClient()
