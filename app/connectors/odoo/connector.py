import logging
from typing import Any
from app.connectors.base import BaseERPConnector
from app.connectors.odoo.client import OdooAsyncClient
from app.core.config import get_settings

logger = logging.getLogger(__name__)


from app.security.circuit_breaker import odoo_circuit_breaker


class OdooConnector(BaseERPConnector):
    """Triển khai cụ thể của BaseERPConnector cho Odoo Cloud có bảo vệ bởi Circuit Breaker."""

    def __init__(self, client: OdooAsyncClient | None = None):
        if client is None:
            settings = get_settings()
            self.client = OdooAsyncClient(
                base_url=settings.ODOO_URL,
                db=settings.ODOO_DB,
                username=settings.ODOO_ADMIN_USERNAME,
                api_key=settings.ODOO_API_KEY,
            )
        else:
            self.client = client

    async def _safe_execute_kw(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
        return await odoo_circuit_breaker.execute(
            self.client.execute_kw,
            model=model,
            method=method,
            args=args,
            kwargs=kwargs,
        )

    async def authenticate(self) -> bool:
        try:
            uid = await self.client.authenticate()
            return uid is not None and uid > 0
        except Exception as e:
            logger.error(f"OdooConnector authentication failed: {e}")
            return False

    async def search_read(
        self,
        model: str,
        domain: list[list[Any]] | None = None,
        fields: list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if fields:
            kwargs["fields"] = fields
        if order:
            kwargs["order"] = order

        result = await self._safe_execute_kw(
            model=model,
            method="search_read",
            args=[domain or []],
            kwargs=kwargs,
        )
        return result or []

    async def create_record(self, model: str, values: dict[str, Any]) -> int:
        result = await self._safe_execute_kw(
            model=model,
            method="create",
            args=[values],
        )
        return int(result)

    async def update_record(self, model: str, record_id: int, values: dict[str, Any]) -> bool:
        result = await self._safe_execute_kw(
            model=model,
            method="write",
            args=[[record_id], values],
        )
        return bool(result)

    async def call_model_method(
        self,
        model: str,
        method: str,
        record_ids: list[int],
        *args,
        **kwargs,
    ) -> Any:
        return await self.client.execute_kw(
            model=model,
            method=method,
            args=[record_ids, *args],
            kwargs=kwargs,
        )


# Singleton instance
default_odoo_connector = OdooConnector()


def get_odoo_connector(context=None) -> OdooConnector:
    if context is None:
        return default_odoo_connector
    from app.services.identity_store import identity_store
    from app.connectors.odoo.client import OdooAccessDeniedException
    linked = identity_store.get(context.telegram_chat_id)
    if not linked or str(linked["profile"]["id"]) != str(context.employee_id):
        raise OdooAccessDeniedException("Login required for this user")
    settings = get_settings()
    return OdooConnector(OdooAsyncClient(settings.ODOO_URL, settings.ODOO_DB, linked["login"], linked["credential"]))
