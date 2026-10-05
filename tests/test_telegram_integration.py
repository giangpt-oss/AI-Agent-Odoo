"""Tests for Telegram Webhook, Identity Resolution, Security, and LangGraph Dispatch."""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_telegram_webhook_unregistered_user():
    """Người dùng lạ nhắn tin vào bot -> Báo chưa kích hoạt tài khoản."""
    transport = ASGITransport(app=app)
    payload = {
        "message": {
            "chat": {"id": 12345678},
            "text": "Xin chào bot",
        }
    }

    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/telegram/webhook", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "unregistered_user"

            mock_send.assert_called_once()
            sent_text = mock_send.call_args[0][1]
            assert "Tài khoản chưa được kích hoạt" in sent_text
    print("\n[OK] Unregistered Telegram user handled safely with instructions")


@pytest.mark.asyncio
async def test_telegram_webhook_authorized_user_read_flow():
    """Nhân viên kinh doanh (chat_id: 999999) hỏi xem đơn hàng -> LangGraph xử lý và trả kết quả."""
    transport = ASGITransport(app=app)
    payload = {
        "message": {
            "chat": {"id": 999999},
            "text": "Cho tôi xem 5 đơn hàng gần nhất",
        }
    }

    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/telegram/webhook", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "processed"
            assert data["intent"] == "get_sales_orders"

            mock_send.assert_called_once()
            sent_text = mock_send.call_args[0][1]
            assert "Kết quả từ hệ thống" in sent_text
    print("[OK] Authorized employee read flow from Telegram verified")


@pytest.mark.asyncio
async def test_telegram_webhook_unauthorized_action_auto_rejection():
    """Nhân viên kho (chat_id: 888888) yêu cầu tạo đơn hàng -> Tự động từ chối ngay lập tức."""
    transport = ASGITransport(app=app)
    payload = {
        "message": {
            "chat": {"id": 888888},
            "text": "Tạo đơn hàng 50 triệu cho khách XYZ",
        }
    }

    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/telegram/webhook", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "processed"

            mock_send.assert_called_once()
            sent_text = mock_send.call_args[0][1]
            # Đảm bảo tin nhắn gửi về Telegram chứa thông báo tự động từ chối
            assert "Tự động từ chối" in sent_text
    print("[OK] Unauthorized action from Telegram auto-rejected immediately")
