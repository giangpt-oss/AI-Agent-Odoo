"""Typed boundary to the authenticated Gateway business APIs."""
from typing import Any

import httpx

from app.services.gateway_client import gateway_client


def scope(context) -> dict[str, Any]:
    return {"telegram_user_id": context.telegram_user_id, "chat_id": context.chat_id,
            "telegram_binding_id": context.telegram_binding_id}


async def request(context, method: str, path: str, payload=None) -> dict[str, Any]:
    url = f"{gateway_client.base_url}/internal/v1/users/{context.user_id}/{path}"
    try:
        async with httpx.AsyncClient(timeout=45.0 if path.startswith('business/') else 12.0) as client:
            kwargs = {"headers": gateway_client._headers()}
            if method == "GET":
                kwargs["params"] = {**scope(context), **(payload or {})}
            else:
                kwargs["json"] = {**scope(context), **(payload or {})}
            response = await client.request(method, url, **kwargs)
            data = response.json()
            if response.is_error:
                detail = data.get("detail", data)
                message = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
                return {"status": "error", "message": message}
            return data
    except (httpx.HTTPError, ValueError):
        return {"status": "error", "message": "Không thể lấy phản hồi Gateway. Hãy kiểm tra kết nối; không tự gửi lại thao tác ghi."}
