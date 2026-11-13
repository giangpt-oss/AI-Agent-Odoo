import logging
from typing import Any
import httpx
from app.core.config import get_settings
from app.services.turn_trace import request_id

logger = logging.getLogger(__name__)


class GatewayServiceError(Exception):
    """Lỗi dịch vụ phía Gateway (timeout, không thể kết nối hoặc HTTP 5xx)."""
    pass


class GatewayContractIncompatibleError(Exception):
    """Gateway trả về contract version không tương thích với Agent (cần contract v2)."""
    pass


class GatewayClient:
    """Client kết nối trực tiếp đến External Auth Gateway qua các endpoint nội bộ."""

    def __init__(self, base_url: str | None = None, secret: str | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.GATEWAY_BASE_URL).rstrip("/")
        self.secret = secret or settings.AGENT_TO_GATEWAY_SECRET

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.secret}",
            "Content-Type": "application/json",
        }
        correlation = request_id.get()
        if correlation and correlation.isascii() and correlation.isprintable() and len(correlation) <= 128:
            headers["X-Request-ID"] = correlation
        return headers

    async def resolve_identity(
        self, telegram_user_id: int, chat_id: int, contract_version: int = 2
    ) -> dict[str, Any] | None:
        """Xác minh danh tính nhân viên qua Telegram user ID và private chat ID."""
        url = f"{self.base_url}/internal/v1/identity/resolve"
        payload = {
            "telegram_user_id": telegram_user_id,
            "chat_id": chat_id,
            "contract_version": contract_version,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    if contract_version == 2 and data.get("contract_version") != 2:
                        raise GatewayContractIncompatibleError(
                            "Phiên bản hợp đồng danh tính Gateway không tương thích (yêu cầu v2)."
                        )
                    return data
                if resp.status_code in (401, 404):
                    logger.warning("Gateway identity unlinked (HTTP %s): %s", resp.status_code, resp.text)
                    return None
                if resp.status_code >= 500:
                    raise GatewayServiceError(f"Gateway returned HTTP {resp.status_code}: {resp.text}")
                logger.warning("Gateway identity resolve returned %s: %s", resp.status_code, resp.text)
                return None
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
            logger.error("Network error calling Gateway identity resolve: %s", e)
            raise GatewayServiceError(f"Không thể kết nối đến Gateway: {e}") from e
        except (GatewayServiceError, GatewayContractIncompatibleError):
            raise
        except Exception as e:
            logger.error("Error calling Gateway identity resolve: %s", e)
            return None

    async def get_user_connections(self, user_id: int) -> dict[str, Any]:
        """Lấy trạng thái kết nối Google, Telegram, Odoo của người dùng."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/connections"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "message": resp.text}
        except Exception as e:
            logger.error("Error calling Gateway user connections: %s", e)
            return {"status": "ERROR", "message": str(e)}

    async def _workspace_request(self, user_id: int, service: str, arguments: dict[str, Any], endpoint: str, timeout: float = 10.0) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.base_url}/internal/v1/users/{user_id}/workspace/{endpoint}", json={"service": service, **arguments}, headers=self._headers())
            if response.status_code == 200:
                return response.json()
            return {"status": "PERMISSION_DENIED" if response.status_code == 403 else "SERVICE_ERROR", "message": response.text[:1000]}
        except httpx.RequestError:
            return {"status": "SERVICE_ERROR", "message": "Không thể kết nối Gateway Workspace."}

    async def workspace_query(self, user_id: int, service: str, arguments: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        return await self._workspace_request(user_id, service, arguments, "query", timeout)

    async def drive_knowledge(self, context, terms, file_id):
        payload = {"telegram_user_id": context.telegram_user_id, "chat_id": context.chat_id,
                   "telegram_binding_id": context.telegram_binding_id, "terms": terms, "file_id": file_id}
        try:
            async with httpx.AsyncClient(timeout=28.0) as client:
                url = f"{self.base_url}/internal/v1/users/{context.user_id}/workspace/drive-knowledge"
                response = await client.post(url, json=payload, headers=self._headers())
                if response.status_code == 403:
                    return {"status": "PERMISSION_DENIED"}
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            return {"status": "SERVICE_ERROR"}

    async def workspace_prepare(self, user_id: int, service: str, arguments: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        return await self._workspace_request(user_id, service, arguments, "preview", timeout)

    async def create_workspace_job(
        self,
        user_id: int,
        request_id: str,
        service: str,
        operation: str,
        parameters: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        media: dict[str, Any] | None = None,
        is_write: bool = False,
        operation_id: str | None = None,
        wait_seconds: float = 2.0,
        deadline_seconds: int = 120,
        telegram_chat_id: int | None = None,
        telegram_user_id: int | None = None,
        telegram_binding_id: str | None = None,
    ) -> dict[str, Any]:
        """Tạo và theo dõi Workspace Job bất đồng bộ qua Gateway."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/workspace/jobs"
        payload = {
            "request_id": request_id,
            "operation_id": operation_id,
            "service": service,
            "operation": operation,
            "parameters": parameters or {},
            "body": body or {},
            "media": media,
            "is_write": is_write,
            "wait_seconds": wait_seconds,
            "deadline_seconds": deadline_seconds,
            "telegram_chat_id": telegram_chat_id,
            "telegram_user_id": telegram_user_id,
            "telegram_binding_id": telegram_binding_id,
        }
        try:
            async with httpx.AsyncClient(timeout=wait_seconds + 3.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                if resp.status_code in (200, 202):
                    return resp.json()
                return {"status": "ERROR", "message": resp.text}
        except httpx.RequestError as exc:
            return {"status": "SERVICE_ERROR", "message": f"Không thể kết nối Gateway Jobs: {exc}"}

    async def get_workspace_job(self, user_id: int, job_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/internal/v1/users/{user_id}/workspace/jobs/{job_id}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "message": resp.text}
        except httpx.RequestError as exc:
            return {"status": "SERVICE_ERROR", "message": str(exc)}

    async def get_calendar_events(self, user_id: int, time_min: str, time_max: str) -> dict[str, Any]:
        """Lấy danh sách sự kiện lịch trong khoảng thời gian ISO 8601."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/calendar/events"
        params = {"time_min": time_min, "time_max": time_max}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "events": [], "message": resp.text}
        except httpx.TimeoutException:
            logger.warning("Gateway calendar request timed out for user %s", user_id)
            return {"status": "TIMEOUT", "events": []}
        except Exception as e:
            logger.error("Error calling Gateway calendar events: %s", e)
            return {"status": "ERROR", "events": [], "message": str(e)}

    async def get_unread_emails(self, user_id: int) -> dict[str, Any]:
        """Lấy số lượng thư chưa đọc và tối đa 5 thư gần nhất qua Gmail."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/gmail/unread"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "total_unread": 0, "messages": [], "message": resp.text}
        except httpx.TimeoutException:
            logger.warning("Gateway gmail request timed out for user %s", user_id)
            return {"status": "TIMEOUT", "total_unread": 0, "messages": []}
        except Exception as e:
            logger.error("Error calling Gateway gmail unread: %s", e)
            return {"status": "ERROR", "total_unread": 0, "messages": [], "message": str(e)}

    async def get_crm_leads(
        self, user_id: int, search_term: str | None = None, limit: int = 5
    ) -> dict[str, Any]:
        """Tra cứu cơ hội CRM từ Odoo qua Gateway."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/crm/leads"
        params: dict[str, Any] = {"limit": limit}
        if search_term:
            params["search_term"] = search_term
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "leads": [], "message": resp.text}
        except httpx.TimeoutException:
            logger.warning("Gateway crm request timed out for user %s", user_id)
            return {"status": "TIMEOUT", "leads": []}
        except Exception as e:
            logger.error("Error calling Gateway crm leads: %s", e)
            return {"status": "ERROR", "leads": [], "message": str(e)}

    async def get_calendar_event(self, user_id: int, event_id: str) -> dict[str, Any]:
        """Lấy chi tiết một sự kiện lịch Google qua Gateway."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/calendar/events/{event_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "message": resp.text}
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

    async def get_crm_lead(self, user_id: int, lead_id: int) -> dict[str, Any]:
        """Lấy chi tiết một cơ hội CRM qua Gateway."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/crm/leads/{lead_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "ERROR", "message": resp.text}
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

    async def execute_action(
        self,
        user_id: int,
        operation_id: str,
        telegram_user_id: int,
        chat_id: int,
        telegram_binding_id: str,
        action: str,
        arguments: dict[str, Any],
        timeout: float = 12.0,
    ) -> dict[str, Any]:
        """Thực thi action ngoại vi qua Gateway với đối soát binding_id và dynamic timeout."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/actions"
        payload = {
            "operation_id": operation_id,
            "telegram_user_id": telegram_user_id,
            "chat_id": chat_id,
            "telegram_binding_id": telegram_binding_id,
            "action": action,
            "arguments": arguments,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    raw_status = str(data.get("status", ""))
                    if raw_status.upper() in ["UNKNOWN_RESULT", "TIMEOUT"]:
                        data["status"] = "unknown_result"
                    elif raw_status.upper() in ["IN_PROGRESS"]:
                        data["status"] = "in_progress"
                    elif raw_status.upper() in ["COMPLETED", "OK"]:
                        data["status"] = "completed"
                    return data
                elif resp.status_code == 409:
                    return {"status": "CONFLICT", "message": resp.text}
                elif resp.status_code == 403:
                    return {"status": "PERMISSION_DENIED", "message": resp.text}
                elif resp.status_code in (502, 503, 504):
                    return {"status": "unknown_result", "message": f"Cổng Gateway hoặc dịch vụ ngoài không phản hồi (HTTP {resp.status_code})."}
                return {"status": "SERVICE_ERROR", "message": resp.text}
        except httpx.TimeoutException:
            logger.warning("Gateway action execution timed out: %s", operation_id)
            return {"status": "unknown_result", "message": "Quá thời gian chờ phản hồi từ Gateway (chưa rõ kết quả)."}
        except (httpx.NetworkError, httpx.RequestError) as e:
            logger.error("Network error calling Gateway execute_action: %s", e)
            return {"status": "unknown_result", "message": f"Mất kết nối mạng tới Gateway sau khi gửi yêu cầu ({e})."}
        except Exception as e:
            logger.error("Error calling Gateway execute_action: %s", e)
            return {"status": "SERVICE_ERROR", "message": str(e)}

    async def get_action_status(
        self,
        user_id: int,
        operation_id: str,
        telegram_user_id: int,
        chat_id: int,
        telegram_binding_id: str,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Tra cứu trạng thái thao tác ghi đã thực hiện qua Gateway để đối soát."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/actions/{operation_id}"
        params = {
            "telegram_user_id": telegram_user_id,
            "chat_id": chat_id,
            "telegram_binding_id": telegram_binding_id,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, params=params, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 404:
                    return {"status": "not_found", "message": "Không tìm thấy thao tác trên ledger của Gateway."}
                elif resp.status_code == 403:
                    return {"status": "permission_denied", "message": "Không khớp quyền hoặc binding của người dùng."}
                return {"status": "unknown_result", "message": resp.text}
        except Exception as e:
            logger.error("Error calling Gateway get_action_status: %s", e)
            return {"status": "unknown_result", "message": str(e)}

    async def get_odoo_catalog(self) -> dict[str, Any]:
        """Lấy danh mục catalog models và fields được phép truy vấn từ Gateway."""
        url = f"{self.base_url}/internal/v1/odoo/catalog"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "SERVICE_ERROR", "catalog": [], "message": resp.text}
        except Exception as e:
            logger.error("Error calling Gateway get_odoo_catalog: %s", e)
            return {"status": "SERVICE_ERROR", "catalog": [], "message": str(e)}

    async def query_odoo(
        self,
        user_id: int,
        model: str,
        fields: list[str] | None = None,
        domain: list[Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        order: str | None = None,
        count_only: bool = False,
    ) -> dict[str, Any]:
        """Truy vấn dữ liệu Odoo an toàn qua Gateway Whitelist Catalog."""
        url = f"{self.base_url}/internal/v1/users/{user_id}/odoo/query"
        payload = {
            "model": model,
            "fields": fields,
            "domain": domain,
            "limit": limit,
            "offset": offset,
            "order": order,
            "count_only": count_only,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 400:
                    detail = resp.json().get("detail", {})
                    msg = detail.get("message", resp.text)
                    return {"status": "INVALID_QUERY", "message": msg, "items": []}
                elif resp.status_code == 403:
                    detail = resp.json().get("detail", {})
                    msg = detail.get("message", "Tài khoản không có quyền truy cập dữ liệu này trên Odoo.")
                    return {"status": "PERMISSION_DENIED", "message": msg, "items": []}
                elif resp.status_code == 503:
                    return {"status": "ODOO_UNAVAILABLE", "message": "Không thể kết nối đến hệ thống Odoo lúc này.", "items": []}
                return {"status": "SERVICE_ERROR", "message": resp.text, "items": []}
        except httpx.TimeoutException:
            logger.warning("Gateway query_odoo timed out for user %s, model %s", user_id, model)
            return {"status": "TIMEOUT", "message": "Quá thời gian chờ phản hồi từ Odoo.", "items": []}
        except Exception as e:
            logger.error("Error calling Gateway query_odoo: %s", e)
            return {"status": "SERVICE_ERROR", "message": str(e), "items": []}


gateway_client = GatewayClient()

