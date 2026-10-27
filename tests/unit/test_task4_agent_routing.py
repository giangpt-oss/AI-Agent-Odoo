import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.main import create_application
from app.domain.gateway_context import GatewayPermission


@pytest.fixture
def agent_client():
    mock_settings = MagicMock()
    mock_settings.INTEGRATION_MODE = "gateway"
    mock_settings.GATEWAY_TO_AGENT_SECRET = "test_gw_secret_32_chars_long_12345"
    mock_settings.APP_NAME = "Test Agent"
    mock_settings.APP_ENV = "test"
    mock_settings.DEBUG = True

    test_app = create_application(settings=mock_settings)
    client = TestClient(test_app)

    # Mock user identity với đầy đủ quyền
    mock_identity = {
        "status": "OK",
        "contract_version": "v2",
        "user_id": 10,
        "name": "Nguyễn Văn A",
        "login": "nguyenvana@example.com",
        "odoo_uid": 10,
        "tenant": "default",
        "database": "odoo_db",
        "timezone": "Asia/Ho_Chi_Minh",
        "permissions": [
            "tasks.read", "tasks.write",
            "calendar.read", "calendar.write",
            "gmail.read", "gmail.send",
            "crm.read", "crm.create", "crm.write",
        ],
        "telegram_binding_id": "bind_10",
    }

    mock_conns = {
        "status": "OK",
        "google": {"status": "OK", "email": "nguyenvana@gmail.com"},
        "odoo": {"status": "OK"},
    }

    mock_cal_event = {
        "status": "OK",
        "event": {
            "id": "evt_999",
            "summary": "Họp ban đầu",
            "start": "2026-10-27T08:00:00+07:00",
            "end": "2026-10-27T09:00:00+07:00",
            "etag": '"etag123"',
        },
    }

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock, return_value=mock_identity), \
         patch("app.services.gateway_client.gateway_client.get_user_connections", new_callable=AsyncMock, return_value=mock_conns), \
         patch("app.services.gateway_client.gateway_client.get_calendar_event", new_callable=AsyncMock, return_value=mock_cal_event), \
         patch("app.api.v1.internal.get_settings", return_value=mock_settings):
        yield client


def test_calendar_create_not_intercepted_by_view(agent_client):
    """Lệnh /calendar create không được bị bộ nhận diện đọc lịch chặn lại."""
    headers = {"Authorization": "Bearer test_gw_secret_32_chars_long_12345"}
    resp = agent_client.post(
        "/internal/v1/telegram/turn",
        json={
            "request_id": "req_cal_create_1",
            "gateway_user_id": 10,
            "telegram_user_id": 1234,
            "chat_id": 1234,
            "text": "/calendar create Họp giao ban đầu tuần | 2026-10-27T08:00:00 | 2026-10-27T09:00:00",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    msg = resp.json()["messages"][0]
    assert "Bản xem trước tạo sự kiện Lịch Google" in msg
    assert "/confirm" in msg
    assert "Họp giao ban đầu tuần" in msg
    assert "Lịch Google (7 ngày tới" not in msg


def test_calendar_update_returns_preview(agent_client):
    """Lệnh /calendar update phải trả về bản xem trước cập nhật lịch."""
    headers = {"Authorization": "Bearer test_gw_secret_32_chars_long_12345"}
    resp = agent_client.post(
        "/internal/v1/telegram/turn",
        json={
            "request_id": "req_cal_update_1",
            "gateway_user_id": 10,
            "telegram_user_id": 1234,
            "chat_id": 1234,
            "text": "/calendar update evt_999 | Họp giao ban phòng 2 | 2026-10-27T10:00:00 | 2026-10-27T11:00:00",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    msg = resp.json()["messages"][0]
    assert "Bản xem trước cập nhật sự kiện Lịch Google" in msg
    assert "/confirm" in msg
    assert "evt_999" in msg


def test_gmail_send_not_intercepted_by_keyword_reading(agent_client):
    """Lệnh /gmail send có chứa từ khóa 'calendar' hoặc 'crm' trong tiêu đề không được đổi intent thành đọc thư hay đọc lịch."""
    headers = {"Authorization": "Bearer test_gw_secret_32_chars_long_12345"}
    resp = agent_client.post(
        "/internal/v1/telegram/turn",
        json={
            "request_id": "req_gmail_send_1",
            "gateway_user_id": 10,
            "telegram_user_id": 1234,
            "chat_id": 1234,
            "text": "/gmail send partner@example.com | Báo cáo lịch calendar và CRM | Xin gửi lịch họp tuần",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    msg = resp.json()["messages"][0]
    assert "Bản xem trước gửi thư Gmail" in msg
    assert "/confirm" in msg
    assert "Thư Gmail chưa đọc" not in msg
    assert "Lịch Google" not in msg


def test_calendar_update_confirmation_execution(agent_client):
    """Xác nhận /confirm mã cho calendar.update phải gọi action gateway và trả về kết quả thành công."""
    import re
    headers = {"Authorization": "Bearer test_gw_secret_32_chars_long_12345"}

    # 1. Tạo preview
    resp_preview = agent_client.post(
        "/internal/v1/telegram/turn",
        json={
            "request_id": "req_cal_update_prev",
            "gateway_user_id": 10,
            "telegram_user_id": 1234,
            "chat_id": 1234,
            "text": "/calendar update evt_456 | Họp định kỳ tuần",
        },
        headers=headers,
    )
    assert resp_preview.status_code == 200
    msg = resp_preview.json()["messages"][0]
    match = re.search(r"/confirm\s+([a-zA-Z0-9_\-]+)", msg)
    assert match is not None
    conf_id = match.group(1)

    # 2. Mock execute_action thành công
    mock_exec_res = {
        "status": "completed",
        "result": {
            "id": "evt_456",
            "summary": "Họp định kỳ tuần",
            "start": {"dateTime": "2026-10-27T08:00:00+07:00"},
            "end": {"dateTime": "2026-10-27T09:00:00+07:00"},
        },
    }
    with patch("app.services.gateway_client.gateway_client.execute_action", new_callable=AsyncMock, return_value=mock_exec_res) as mock_exec:
        resp_confirm = agent_client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_cal_update_conf",
                "gateway_user_id": 10,
                "telegram_user_id": 1234,
                "chat_id": 1234,
                "text": f"/confirm {conf_id}",
            },
            headers=headers,
        )
        assert resp_confirm.status_code == 200
        conf_msg = resp_confirm.json()["messages"][0]
        assert "Đã cập nhật sự kiện Lịch Google thành công" in conf_msg
        assert "Họp định kỳ tuần" in conf_msg
        mock_exec.assert_called_once()

