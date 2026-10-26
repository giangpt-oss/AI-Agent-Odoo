import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import get_settings


@pytest.fixture
def client():
    return TestClient(app)


def test_internal_turn_auth_required(client):
    settings = get_settings()
    payload = {
        "request_id": "req-123",
        "gateway_user_id": 1,
        "telegram_user_id": 9999,
        "chat_id": 9999,
        "text": "Hôm nay tôi có gì?",
    }

    # Missing header -> 401
    resp = client.post("/internal/v1/telegram/turn", json=payload)
    assert resp.status_code == 401

    # Wrong token -> 401
    resp = client.post(
        "/internal/v1/telegram/turn",
        json=payload,
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_internal_turn_today_briefing(client):
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-xyz-789",
        "gateway_user_id": 5,
        "telegram_user_id": 12345,
        "chat_id": 12345,
        "text": "Hôm nay tôi có gì?",
    }

    mock_user = {
        "user_id": 5,
        "tenant": "https://test.odoo.com",
        "database": "test_db",
        "odoo_uid": 2,
        "name": "Nguyễn Văn A",
        "login": "a.nguyen@company.com",
        "tz": "Asia/Bangkok",
    }

    mock_cal = {
        "status": "OK",
        "events": [
            {
                "id": "e1",
                "summary": "Họp giao ban đầu tuần",
                "is_all_day": False,
                "start": "2026-10-26T09:00:00+07:00",
                "end": "2026-10-26T10:00:00+07:00",
                "location": "Phòng họp A",
                "link": "https://calendar.google.com/event1",
            }
        ],
    }

    mock_gmail = {
        "status": "OK",
        "total_unread": 3,
        "messages": [
            {
                "id": "m1",
                "from": "sếp@company.com",
                "subject": "Duyệt kế hoạch tuần",
                "link": "https://mail.google.com/mail/u/0/#inbox/m1",
            }
        ],
    }

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = mock_user
        with patch("app.services.gateway_client.gateway_client.get_calendar_events", new_callable=AsyncMock) as mock_get_cal:
            mock_get_cal.return_value = mock_cal
            with patch("app.services.gateway_client.gateway_client.get_unread_emails", new_callable=AsyncMock) as mock_get_mail:
                mock_get_mail.return_value = mock_gmail
                
                resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
                assert resp.status_code == 200
                data = resp.json()
                assert data["request_id"] == "req-xyz-789"
                assert len(data["messages"]) == 1
                msg = data["messages"][0]
                assert "Nguyễn Văn A" in msg
                assert "Họp giao ban đầu tuần" in msg
                assert "sếp@company.com" in msg
                assert "Tổng số thư chưa đọc: **3**" in msg


@pytest.mark.asyncio
async def test_internal_turn_identity_mismatch(client):
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-mismatch",
        "gateway_user_id": 999,  # Mismatch with resolved id 5
        "telegram_user_id": 12345,
        "chat_id": 12345,
        "text": "/today",
    }

    mock_user = {
        "user_id": 5,
        "tenant": "https://test.odoo.com",
        "database": "test_db",
        "odoo_uid": 2,
        "name": "Nguyễn Văn A",
    }

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = mock_user
        resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "sai lệch thông tin danh tính" in data["messages"][0]
