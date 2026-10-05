import logging
from redis.asyncio import Redis, ConnectionPool
from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Tạo connection pool cho Redis
redis_pool = ConnectionPool.from_url(
    settings.REDIS_URL,
    max_connections=20,
    decode_responses=True,
)


class RedisService:
    def __init__(self):
        self._client: Redis | None = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = Redis(connection_pool=redis_pool)
        return self._client

    async def get(self, key: str) -> str | None:
        try:
            return await self.client.get(key)
        except Exception as e:
            logger.warning(f"Redis GET failed for key {key}: {e}")
            return None

    async def set(self, key: str, value: str, expire_seconds: int | None = None) -> bool:
        try:
            return bool(await self.client.set(key, value, ex=expire_seconds))
        except Exception as e:
            logger.warning(f"Redis SET failed for key {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        try:
            return bool(await self.client.delete(key))
        except Exception as e:
            logger.warning(f"Redis DELETE failed for key {key}: {e}")
            return False

    async def is_connected(self) -> bool:
        try:
            return await self.client.ping()
        except Exception:
            return False

    async def close(self):
        if self._client is not None:
            await self._client.aclose()


redis_service = RedisService()
