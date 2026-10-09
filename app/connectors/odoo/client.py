import logging
from typing import Any
import httpx

logger = logging.getLogger(__name__)


class OdooAPIException(Exception):
    """Lỗi chung từ Odoo API."""
    pass


class OdooAccessDeniedException(OdooAPIException):
    """Lỗi vi phạm phân quyền Odoo (Layer 2 Security - AccessError / 403)."""
    pass


_SHARED_ODOO_CLIENTS: dict[float, httpx.AsyncClient] = {}


class ExpiringLRUCache:
    """Bộ nhớ đệm LRU có TTL tự động dọn dẹp các key hết hạn và giới hạn dung lượng tối đa."""

    def __init__(self, maxsize: int = 500, default_ttl: float = 180.0):
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        import time
        if key not in self._data:
            return None
        created_at, value = self._data[key]
        if time.time() - created_at > self.default_ttl:
            self._data.pop(key, None)
            return None
        # Cập nhật thứ tự truy cập (True LRU - đưa key vừa đọc về cuối hàng đợi)
        self._data.pop(key)
        self._data[key] = (created_at, value)
        return value

    def set(self, key: str, value: Any) -> None:
        import time
        now = time.time()
        # Dọn dẹp các key hết hạn nếu vượt dung lượng
        if len(self._data) >= self.maxsize:
            expired = [k for k, (t, _) in self._data.items() if now - t > self.default_ttl]
            for k in expired:
                self._data.pop(k, None)
            # Nếu vẫn vượt maxsize, đẩy bớt key cũ nhất (FIFO/LRU)
            while len(self._data) >= self.maxsize:
                first_key = next(iter(self._data))
                self._data.pop(first_key, None)
        self._data[key] = (now, value)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


_GLOBAL_ODOO_READ_CACHE = ExpiringLRUCache(maxsize=500, default_ttl=180.0)


def get_shared_odoo_http_client(timeout: float = 15.0) -> httpx.AsyncClient:
    """Tái sử dụng HTTP connection pool với Keep-Alive thay vì tạo mới liên tục."""
    global _SHARED_ODOO_CLIENTS
    client = _SHARED_ODOO_CLIENTS.get(timeout)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
        )
        _SHARED_ODOO_CLIENTS[timeout] = client
    return client


class OdooAsyncClient:
    """Async HTTP Client giao tiếp với Odoo Cloud qua JSON-RPC / External API."""

    def __init__(
        self,
        base_url: str,
        db: str,
        username: str,
        api_key: str,
        timeout: float = 15.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.endpoint = f"{self.base_url}/jsonrpc"
        self.db = db
        self.username = username
        self.api_key = api_key
        self.timeout = timeout
        self.uid: int | None = None
        self._request_counter = 0

    def _next_id(self) -> int:
        self._request_counter += 1
        return self._request_counter

    async def call_jsonrpc(self, service: str, method: str, *args, **kwargs) -> Any:
        """Gửi JSON-RPC payload tới Odoo endpoint dùng Connection Pool tái sử dụng."""
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": service,
                "method": method,
                "args": list(args),
            },
            "id": self._next_id(),
        }

        client = get_shared_odoo_http_client(self.timeout)
        try:
            response = await client.post(
                self.endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise OdooAPIException("CODE_429_RATE_LIMITED: Odoo Cloud đang bị quá tải.")
            raise OdooAPIException(f"HTTP Error từ Odoo: {e.response.status_code}")
        except httpx.RequestError as e:
            raise OdooAPIException(f"Lỗi kết nối tới Odoo Cloud: {str(e)}")

        if "error" in data:
            err = data["error"]
            err_data = err.get("data", {})
            err_name = err_data.get("name", "")
            err_message = err_data.get("message", "") or err.get("message", "Lỗi Odoo không xác định")

            # Layer 2 Security: Bắt lỗi phân quyền Odoo
            if "AccessError" in err_name or "AccessDenied" in err_name or "access right" in err_message.lower():
                logger.warning(f"Odoo Layer 2 Access Denied: {err_message}")
                raise OdooAccessDeniedException(f"Odoo từ chối quyền: {err_message}")

            logger.error(f"Odoo RPC Error [{err_name}]: {err_message}")
            raise OdooAPIException(f"Odoo Error: {err_message}")

        return data.get("result")

    async def authenticate(self) -> int:
        """Xác thực tài khoản hoặc API Key với Odoo, trả về UID."""
        result = await self.call_jsonrpc(
            "common",
            "authenticate",
            self.db,
            self.username,
            self.api_key,
            {},
        )
        if not result:
            raise OdooAccessDeniedException("Xác thực Odoo thất bại. Kiểm tra lại DB, Username hoặc API Key.")
        self.uid = int(result)
        return self.uid

    async def execute_kw(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
        """Gọi execute_kw trên Model của Odoo với TTL caching tự động cho search_read / search_count."""
        if self.uid is None:
            await self.authenticate()

        # Cache layer cho các truy vấn đọc (TTL 3 phút)
        is_cacheable = method in ("search_read", "search_count")
        cache_key = None
        if is_cacheable:
            import hashlib
            key_raw = f"{self.db}:{self.uid}:{model}:{method}:{args}:{kwargs}"
            cache_key = hashlib.md5(key_raw.encode("utf-8", errors="ignore")).hexdigest()
            cached_data = _GLOBAL_ODOO_READ_CACHE.get(cache_key)
            if cached_data is not None:
                logger.debug(f"⚡ [CACHE HIT] {model}.{method} (trả kết quả tức thì)")
                return cached_data

        result = await self.call_jsonrpc(
            "object",
            "execute_kw",
            self.db,
            self.uid,
            self.api_key,
            model,
            method,
            args,
            kwargs or {},
        )

        if is_cacheable and cache_key:
            _GLOBAL_ODOO_READ_CACHE.set(cache_key, result)

        return result

