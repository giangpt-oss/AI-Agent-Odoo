import pytest
from unittest.mock import MagicMock, patch
import httpx
from app.services.gateway_client import GatewayClient, GatewayContractIncompatibleError
from app.domain.gateway_context import build_gateway_user_context

@pytest.mark.asyncio
async def test_agent_rejects_legacy_contract_v1():
    client = GatewayClient(base_url="http://test-gw", secret="test_secret_32_chars_minimum_len_12")
    legacy_payload = {
        "status": "OK",
        "user_id": 1,
        "tenant": "https://erp.com",
        "database": "db",
        "odoo_uid": 10,
        "name": "User",
        "login": "user@com",
        "tz": "Asia/Bangkok",
    }
    mock_resp = httpx.Response(status_code=200, json=legacy_payload, request=httpx.Request("POST", "http://test-gw"))
    with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
        with pytest.raises(GatewayContractIncompatibleError):
            await client.resolve_identity(telegram_user_id=123, chat_id=123, contract_version=2)

@pytest.mark.asyncio
async def test_agent_accepts_contract_v2():
    client = GatewayClient(base_url="http://test-gw", secret="test_secret_32_chars_minimum_len_12")
    v2_payload = {
        "contract_version": 2,
        "status": "OK",
        "user_id": 1,
        "tenant": "https://erp.com",
        "database": "db",
        "odoo_uid": 10,
        "name": "User",
        "login": "user@com",
        "tz": "Asia/Bangkok",
        "telegram_binding_id": "tg_bind_12345",
        "job_title": "Kỹ sư",
        "department": "Kỹ thuật",
        "manager_name": "Sếp B",
        "permissions": ["tasks.read", "tasks.write"],
        "authorization_checked_at": "2026-10-26T08:00:00Z",
    }
    mock_resp = httpx.Response(status_code=200, json=v2_payload, request=httpx.Request("POST", "http://test-gw"))
    with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
        res = await client.resolve_identity(telegram_user_id=123, chat_id=123, contract_version=2)
        assert res["contract_version"] == 2
        assert res["telegram_binding_id"] == "tg_bind_12345"

        ctx = build_gateway_user_context(res)
        assert ctx.telegram_binding_id == "tg_bind_12345"
        assert ctx.job_title == "Kỹ sư"
        assert ctx.department == "Kỹ thuật"
        assert ctx.manager_name == "Sếp B"
        assert ctx.has_permission("tasks.read")
