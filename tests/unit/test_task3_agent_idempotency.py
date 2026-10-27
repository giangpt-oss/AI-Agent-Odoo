import pytest
import hashlib
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import create_application
from app.services.turn_ledger import TurnLedger
from app.services.gateway_client import GatewayServiceError


@pytest.fixture
def agent_client(tmp_path):
    db_path = tmp_path / "turn_ledger_test.db"
    test_ledger = TurnLedger(db_path=db_path)

    from unittest.mock import MagicMock
    mock_settings = MagicMock()
    mock_settings.INTEGRATION_MODE = "gateway"
    mock_settings.GATEWAY_TO_AGENT_SECRET = "test_gw_secret_32_chars_long_12345"
    mock_settings.APP_NAME = "Test Agent"
    mock_settings.APP_ENV = "test"
    mock_settings.DEBUG = True

    test_app = create_application(settings=mock_settings)
    client = TestClient(test_app)

    mock_identity = {
        "user_id": 1,
        "name": "Test User",
        "login": "user@example.com",
        "telegram_binding_id": "bind_1",
        "permissions": [],
    }

    with patch("app.api.v1.internal.turn_ledger", test_ledger), \
         patch("app.api.v1.internal.get_settings", return_value=mock_settings), \
         patch("app.services.gateway_client.gateway_client.resolve_identity", AsyncMock(return_value=mock_identity)):
        yield client, test_ledger


def test_turn_in_progress_returns_503(agent_client):
    """Khi turn đang được xử lý, request lặp cùng request_id phải trả về HTTP 503 để gateway retry."""
    client, ledger = agent_client
    headers = {"Authorization": "Bearer test_gw_secret_32_chars_long_12345"}

    # Đánh dấu turn đang in_progress
    p_hash = hashlib.sha256("Xin chào".encode()).hexdigest()
    ledger.record_turn_start(
        request_id="req_in_progress_1",
        user_id=1,
        telegram_user_id=123,
        chat_id=123,
        telegram_binding_id="bind_1",
        payload_hash=p_hash,
    )

    resp = client.post(
        "/internal/v1/telegram/turn",
        json={
            "request_id": "req_in_progress_1",
            "gateway_user_id": 1,
            "telegram_user_id": 123,
            "chat_id": 123,
            "text": "Xin chào",
        },
        headers=headers,
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "TURN_IN_PROGRESS"


def test_turn_payload_mismatch_returns_409(agent_client):
    """Cùng request_id nhưng payload text khác nhau phải bị từ chối HTTP 409 Conflict."""
    client, ledger = agent_client
    headers = {"Authorization": "Bearer test_gw_secret_32_chars_long_12345"}

    # Turn hoàn tất với nội dung "Xin chào"
    p_hash1 = hashlib.sha256("Xin chào".encode()).hexdigest()
    ledger.record_turn_start("req_mismatch_1", 1, 123, 123, "bind_1", p_hash1)
    ledger.record_turn_completed("req_mismatch_1", ["Chào bạn!"])

    # Gửi lại cùng request_id nhưng nội dung khác "Tạo việc mới"
    resp = client.post(
        "/internal/v1/telegram/turn",
        json={
            "request_id": "req_mismatch_1",
            "gateway_user_id": 1,
            "telegram_user_id": 123,
            "chat_id": 123,
            "text": "Tạo việc mới",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PAYLOAD_MISMATCH"


def test_transient_error_not_cached_as_permanent(agent_client):
    """Lỗi mạng tạm thời không được lưu thành kết quả vĩnh viễn trong turn_ledger."""
    client, ledger = agent_client
    headers = {"Authorization": "Bearer test_gw_secret_32_chars_long_12345"}

    with patch("app.services.gateway_client.gateway_client.resolve_identity", side_effect=GatewayServiceError("Gateway 503 Unavailable")):
        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_transient_1",
                "gateway_user_id": 1,
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": "/whoami",
            },
            headers=headers,
        )
        # Vì lỗi mạng tạm thời, trả về 503 hoặc thông báo lỗi nhưng KHÔNG lưu cached turn completed
        assert resp.status_code in [503, 200]

    # Kiểm tra turn_records: không được đánh dấu là completed
    cached = ledger.get_turn("req_transient_1")
    assert cached is None, "Transient network error should not be cached permanently as completed turn"
