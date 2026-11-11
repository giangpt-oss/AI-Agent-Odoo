import asyncio
import logging
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from google.genai.errors import APIError

logger = logging.getLogger(__name__)


class GeminiRequestError(RuntimeError):
    def __init__(self, category, status_code=None):
        self.category = category
        self.status_code = status_code
        super().__init__(category)


def retry_after(response):
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("Retry-After")
    if value is None:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


async def call_gemini(operation, call, *args, **kwargs):
    for attempt in range(3):
        response = None
        code = None
        try:
            return await asyncio.to_thread(call, *args, **kwargs)
        except APIError as exc:
            code = exc.code
            response = exc.response
            category = "rate_limit" if code == 429 else "authentication" if code in {401, 403} else "provider_error"
            transient = code in {429, 500, 502, 503, 504}
        except (httpx.TimeoutException, TimeoutError):
            category, transient = "timeout", True
        except httpx.NetworkError:
            category, transient = "network", True
        delay = max(retry_after(response), 0.5 * 2 ** attempt + random.uniform(0, 0.25))
        logger.warning("gemini_request_failed operation=%s category=%s status=%s attempt=%d", operation, category, code, attempt + 1)
        if not transient or attempt == 2 or delay > 5:
            raise GeminiRequestError(category, code)
        await asyncio.sleep(delay)
