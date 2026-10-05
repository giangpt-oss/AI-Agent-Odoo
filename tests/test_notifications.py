"""Tests for Passive Notifications, Role Broadcast, and Odoo Webhooks."""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from app.services.notification import notification_service
from app.main import app


@pytest.mark.asyncio
async def test_notification_broadcast_to_roles():
    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True

        result = await notification_service.broadcast_to_roles(
            target_roles=["warehouse_user"],
            message="Kho chuẩn bị xuất đơn hàng SO100",
        )

        assert result["total_sent"] >= 1
        assert "warehouse_user" in result["target_roles"]
        mock_send.assert_called()
        sent_text = mock_send.call_args[1]["text"]
        assert "THÔNG BÁO HỆ THỐNG" in sent_text
        assert "Kho chuẩn bị xuất đơn hàng" in sent_text
    print("\n[OK] Role-based broadcast verified")


@pytest.mark.asyncio
async def test_odoo_passive_notification_event():
    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True

        payload = {
            "record_name": "SO00999",
            "amount": 25000000,
            "assigned_chat_id": 999999,
        }

        success = await notification_service.handle_odoo_event("sale_order_confirmed", payload)
        assert success is True
        mock_send.assert_called_once()
        sent_chat_id = mock_send.call_args[1]["chat_id"]
        sent_text = mock_send.call_args[1]["text"]

        assert sent_chat_id == 999999
        assert "SO00999" in sent_text
        assert "25,000,000 VNĐ" in sent_text
    print("[OK] Odoo passive event notification formatting & dispatch verified")


@pytest.mark.asyncio
async def test_api_odoo_webhook_endpoint():
    transport = ASGITransport(app=app)
    payload = {
        "event_type": "invoice_paid",
        "record_name": "INV/2026/0001",
        "model": "account.move",
        "assigned_chat_id": 777777,
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/webhooks/odoo", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["record_name"] == "INV/2026/0001"
    print("[OK] FastAPI Odoo Webhook receiver endpoint verified")
