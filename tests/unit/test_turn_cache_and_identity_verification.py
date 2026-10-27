import pytest
import hashlib
import json
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.main import create_application
from app.services.turn_ledger import TurnLedger
from app.services.gateway_client import GatewayServiceError


@pytest.fixture
def turn_cache_env(tmp_path):
    db_path = tmp_path / "turn_cache_test.db"
    test_ledger = TurnLedger(db_path=db_path)

    mock_settings = MagicMock()
    mock_settings.INTEGRATION_MODE = "gateway"
    mock_settings.GATEWAY_TO_AGENT_SECRET = "secret_for_turn_cache_32_chars_12345"
    mock_settings.APP_NAME = "Test Agent"
    mock_settings.APP_ENV = "test"
    mock_settings.DEBUG = True

    test_app = create_application(settings=mock_settings)
    client = TestClient(test_app)

    with patch("app.api.v1.internal.turn_ledger", test_ledger), \
         patch("app.api.v1.internal.get_settings", return_value=mock_settings):
        yield client, test_ledger


def test_cached_turn_requires_active_identity_verification(turn_cache_env):
    """Khi tài khoản Telegram bị ngắt liên kết hoặc tài khoản Odoo bị khóa,
    Agent tuyệt đối không được trả cached response của turn cũ."""
    client, ledger = turn_cache_env
    headers = {"Authorization": "Bearer secret_for_turn_cache_32_chars_12345"}

    # Lưu một turn hoàn tất trong cache
    p_hash = hashlib.sha256("Xem hồ sơ".encode()).hexdigest()
    ledger.record_turn_start("req_cached_unlinked_1", 1, 123, 123, "bind_1", p_hash)
    ledger.record_turn_completed("req_cached_unlinked_1", ["Hồ sơ cá nhân bí mật"])

    # Giả lập tài khoản bị ngắt kết nối (resolve_identity trả về None)
    with patch("app.services.gateway_client.gateway_client.resolve_identity", AsyncMock(return_value=None)):
        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_cached_unlinked_1",
                "gateway_user_id": 1,
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": "Xem hồ sơ",
                "telegram_binding_id": "bind_1",
            },
            headers=headers,
        )
        # Phải từ chối, không được trả cached response
        assert resp.status_code in [403, 404]
        if resp.status_code == 200:
            assert "Hồ sơ cá nhân bí mật" not in resp.json()["messages"]


def test_cached_turn_rejected_when_binding_changed(turn_cache_env):
    """Khi người dùng hủy và liên kết lại Telegram (binding_id đổi từ bind_1 sang bind_2),
    request cũ với bind_1 phải bị từ chối, không trả nội dung cũ."""
    client, ledger = turn_cache_env
    headers = {"Authorization": "Bearer secret_for_turn_cache_32_chars_12345"}

    p_hash = hashlib.sha256("/today".encode()).hexdigest()
    ledger.record_turn_start("req_binding_changed_1", 1, 123, 123, "bind_1", p_hash)
    ledger.record_turn_completed("req_binding_changed_1", ["Lịch hôm nay..."])

    # Gateway resolve_identity trả về binding_id mới là bind_2
    mock_identity = {
        "user_id": 1,
        "name": "User A",
        "login": "user@example.com",
        "telegram_binding_id": "bind_2",
        "permissions": ["calendar.read"],
    }
    with patch("app.services.gateway_client.gateway_client.resolve_identity", AsyncMock(return_value=mock_identity)):
        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_binding_changed_1",
                "gateway_user_id": 1,
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": "/today",
                "telegram_binding_id": "bind_2",
            },
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "BINDING_CHANGED"


def test_legacy_turn_lacking_binding_or_hash_refuses_replay(turn_cache_env):
    """Turn cũ trong ledger thiếu binding_id hoặc payload_hash không được replay tự động,
    phải yêu cầu người dùng gửi tin nhắn mới."""
    client, ledger = turn_cache_env
    headers = {"Authorization": "Bearer secret_for_turn_cache_32_chars_12345"}

    # Ghi trực tiếp bản ghi cũ không có binding_id và payload_hash
    with ledger.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO turn_records (request_id, user_id, telegram_user_id, chat_id, telegram_binding_id, payload_hash, status, messages_json, created_at)
            VALUES (?, ?, ?, ?, NULL, NULL, 'completed', ?, datetime('now'))
            """,
            ("req_legacy_1", 1, 123, 123, json.dumps(["Kết quả cũ"])),
        )
        conn.commit()

    mock_identity = {
        "user_id": 1,
        "name": "User A",
        "login": "user@example.com",
        "telegram_binding_id": "bind_1",
        "permissions": [],
    }
    with patch("app.services.gateway_client.gateway_client.resolve_identity", AsyncMock(return_value=mock_identity)):
        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_legacy_1",
                "gateway_user_id": 1,
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": "Tin nhắn bất kỳ",
                "telegram_binding_id": "bind_1",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        # Không được trả "Kết quả cũ", phải thông báo gửi tin nhắn mới
        assert "Kết quả cũ" not in msgs
        assert any("gửi tin nhắn mới" in m for m in msgs)


def test_transient_identity_error_returns_503_for_retry(turn_cache_env):
    """Khi resolve_identity gặp lỗi mạng GatewayServiceError, phải trả HTTP 503 để Gateway retry."""
    client, ledger = turn_cache_env
    headers = {"Authorization": "Bearer secret_for_turn_cache_32_chars_12345"}

    with patch("app.services.gateway_client.gateway_client.resolve_identity", AsyncMock(side_effect=GatewayServiceError("Gateway 503 Timeout"))):
        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_gw_transient_1",
                "gateway_user_id": 1,
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": "Hello",
                "telegram_binding_id": "bind_1",
            },
            headers=headers,
        )
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "GATEWAY_SERVICE_UNAVAILABLE"


def test_valid_retry_returns_cached_turn_without_reexecuting(turn_cache_env):
    """Khi retry hợp lệ cùng request_id, user, binding và hash, trả về cached response thành công."""
    client, ledger = turn_cache_env
    headers = {"Authorization": "Bearer secret_for_turn_cache_32_chars_12345"}

    p_hash = hashlib.sha256("Chào bot".encode()).hexdigest()
    ledger.record_turn_start("req_valid_retry_1", 1, 123, 123, "bind_1", p_hash)
    ledger.record_turn_completed("req_valid_retry_1", ["Chào bạn, mình có thể giúp gì?"])

    mock_identity = {
        "user_id": 1,
        "name": "User A",
        "login": "user@example.com",
        "telegram_binding_id": "bind_1",
        "permissions": [],
    }
    with patch("app.services.gateway_client.gateway_client.resolve_identity", AsyncMock(return_value=mock_identity)), \
         patch("app.services.gateway_agent_dispatcher.gateway_agent_dispatcher.dispatch") as mock_dispatch:
        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_valid_retry_1",
                "gateway_user_id": 1,
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": "Chào bot",
                "telegram_binding_id": "bind_1",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["messages"] == ["Chào bạn, mình có thể giúp gì?"]
        # Không được chạy lại dispatcher
        mock_dispatch.assert_not_called()
