"""Comprehensive End-to-End (E2E) System Integration Tests."""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from app.main import app

pytestmark = pytest.mark.usefixtures("linked_employees")
from app.security.kill_switch import kill_switch


@pytest.mark.asyncio
async def test_e2e_scenario_1_authorized_read():
    """Kịch bản 1: Nhân viên Sales (999999) tra cứu 5 đơn hàng gần nhất qua Telegram."""
    transport = ASGITransport(app=app)
    payload = {
        "message": {
            "chat": {"id": 999999},
            "text": "Cho tôi xem 5 đơn hàng gần nhất của khách ABC",
        }
    }

    mock_odoo_records = [
        {"id": 101, "name": "SO00101", "partner_id": [1, "Công ty ABC"], "amount_total": 45000000, "state": "sale"}
    ]

    with patch("app.connectors.odoo.connector.OdooConnector.search_read", new_callable=AsyncMock) as mock_odoo:
        mock_odoo.return_value = mock_odoo_records

        with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_tg:
            mock_tg.return_value = True

            async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret", "X-Odoo-Webhook-Secret": "test-odoo-secret"}) as client:
                resp = await client.post("/api/v1/telegram/webhook", json=payload)
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "processed"
                assert data["intent"] == "get_sales_orders"

                mock_tg.assert_called_once()
                sent_text = mock_tg.call_args[0][1]
                assert "SO00101" in sent_text
                assert "Kết quả từ hệ thống" in sent_text
    print("\n[OK] E2E Scenario 1: Authorized Read Flow verified")


@pytest.mark.asyncio
async def test_e2e_scenario_2_unauthorized_action_auto_rejection():
    """Kịch bản 2: Nhân viên Kho (888888) cố tình yêu cầu tạo đơn hàng -> Tự động từ chối."""
    transport = ASGITransport(app=app)
    payload = {
        "message": {
            "chat": {"id": 888888},
            "text": "Tạo đơn hàng 100 triệu cho khách hàng VIP",
        }
    }

    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_tg:
        mock_tg.return_value = True

        async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret", "X-Odoo-Webhook-Secret": "test-odoo-secret"}) as client:
            resp = await client.post("/api/v1/telegram/webhook", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "processed"

            mock_tg.assert_called_once()
            sent_text = mock_tg.call_args[0][1]
            assert "Tự động từ chối" in sent_text
            assert "sales.order.write" in sent_text
    print("[OK] E2E Scenario 2: Unauthorized Action Auto-Rejection verified")


@pytest.mark.asyncio
async def test_e2e_scenario_3_write_action_with_user_self_confirmation():
    """Kịch bản 3: Quản lý Sales (777777) tạo đơn hàng -> Hỏi xác nhận -> Bấm Đồng ý -> Hoàn tất."""
    transport = ASGITransport(app=app)

    # Turn 1: Yêu cầu tạo đơn
    turn1_payload = {
        "message": {
            "chat": {"id": 777777},
            "text": "Tạo đơn hàng partner_id=42",
        }
    }

    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_tg:
        mock_tg.return_value = True

        async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret", "X-Odoo-Webhook-Secret": "test-odoo-secret"}) as client:
            resp1 = await client.post("/api/v1/telegram/webhook", json=turn1_payload)
            assert resp1.status_code == 200

            mock_tg.assert_called_once()
            confirm_prompt = mock_tg.call_args[0][1]
            assert "Xác nhận thực hiện hành động ghi dữ liệu" in confirm_prompt

    # Turn 2: Người dùng nhắn "Đồng ý" trên cùng chat_id
    turn2_payload = {
        "message": {
            "chat": {"id": 777777},
            "text": "Đồng ý",
        }
    }

    with patch("app.connectors.odoo.connector.OdooConnector.create_record", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = 7788

        with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_tg:
            mock_tg.return_value = True

            async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret", "X-Odoo-Webhook-Secret": "test-odoo-secret"}) as client:
                resp2 = await client.post("/api/v1/telegram/webhook", json=turn2_payload)
                assert resp2.status_code == 200

                mock_tg.assert_called_once()
                result_text = mock_tg.call_args[0][1]
                assert "Kết quả từ hệ thống" in result_text
    print("[OK] E2E Scenario 3: Two-step User Self-Confirmation Flow verified")


@pytest.mark.asyncio
async def test_e2e_scenario_4_kill_switch_active_protection():
    """Kịch bản 4: Khi Kill Switch bật -> Chặn đứng mọi tương tác Telegram."""
    transport = ASGITransport(app=app)
    await kill_switch.set_state(True)

    payload = {
        "message": {
            "chat": {"id": 999999},
            "text": "Xem đơn hàng",
        }
    }

    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_tg:
        mock_tg.return_value = True

        async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret", "X-Odoo-Webhook-Secret": "test-odoo-secret"}) as client:
            resp = await client.post("/api/v1/telegram/webhook", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "kill_switch_active"

            mock_tg.assert_called_once()
            sent_text = mock_tg.call_args[0][1]
            assert "HỆ THỐNG TẠM NGƯNG BẢO TRÌ KHẨN CẤP" in sent_text

    await kill_switch.set_state(False)
    print("[OK] E2E Scenario 4: Emergency Kill Switch Protection verified")
