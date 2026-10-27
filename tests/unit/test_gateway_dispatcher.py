import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.task_identity import build_owner_key
from app.main import create_application


@pytest.fixture
def gateway_settings():
    return Settings(
        INTEGRATION_MODE="gateway",
        GATEWAY_TO_AGENT_SECRET="gateway_secret_for_dispatcher_tests_min_32",
        AGENT_TO_GATEWAY_SECRET="agent_secret_for_dispatcher_tests_min_32",
    )


@pytest.fixture
def client(gateway_settings):
    app = create_application(settings=gateway_settings)
    return TestClient(app)


MOCK_USER = {
    "user_id": 42,
    "tenant": "https://erp.mycompany.com",
    "database": "prod_db",
    "odoo_uid": 105,
    "name": "Lê Văn C",
    "login": "c.le@mycompany.com",
    "tz": "Asia/Ho_Chi_Minh",
    "job_title": "Trưởng phòng Kinh doanh",
    "department": "Kinh doanh",
    "permissions": ["tasks.read", "calendar.read", "gmail.read"],
}


@pytest.mark.asyncio
async def test_dispatcher_greetings_and_help(client, gateway_settings):
    """Test that greetings/help return deterministic response without calling assistant."""
    headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-greet-1",
        "gateway_user_id": 42,
        "telegram_user_id": 777,
        "chat_id": 777,
        "text": "Xin chào bot",
    }

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = MOCK_USER
        with patch("app.assistant.company_assistant.CompanyAssistant.handle_user_turn") as mock_turn:
            resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["request_id"] == "req-greet-1"
            assert isinstance(data["messages"], list)
            msg = data["messages"][0]
            assert "Xin chào" in msg
            assert "Điểm tin" in msg
            assert "Thông tin" in msg or "Hồ sơ" in msg
            mock_turn.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_whoami(client, gateway_settings):
    """Test that 'Tôi là ai?' presents real verified identity without calling assistant."""
    headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-whoami-1",
        "gateway_user_id": 42,
        "telegram_user_id": 777,
        "chat_id": 777,
        "text": "Tôi là ai?",
    }

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = MOCK_USER
        with patch("app.assistant.company_assistant.CompanyAssistant.handle_user_turn") as mock_turn:
            resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            msg = data["messages"][0]
            assert "Lê Văn C" in msg
            assert "c.le@mycompany.com" in msg
            assert "105" in msg
            assert "prod_db" in msg
            assert "https://erp.mycompany.com" in msg
            mock_turn.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_connection_status(client, gateway_settings):
    """Test that connection status queries gateway connections API."""
    headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-conn-1",
        "gateway_user_id": 42,
        "telegram_user_id": 777,
        "chat_id": 777,
        "text": "Kiểm tra trạng thái kết nối",
    }

    mock_connections = {
        "status": "OK",
        "user_id": 42,
        "google": {
            "status": "OK",
            "email": "c.le@mycompany.com",
            "scopes": [
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
        },
        "odoo": {"status": "OK", "login": "c.le@mycompany.com", "odoo_uid": 101},
        "telegram": {"status": "OK", "telegram_user_id": 777, "username": "cle"},
    }

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = MOCK_USER
        with patch("app.services.gateway_client.gateway_client.get_user_connections", new_callable=AsyncMock) as mock_conns:
            mock_conns.return_value = mock_connections
            with patch("app.assistant.company_assistant.CompanyAssistant.handle_user_turn") as mock_turn:
                resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
                assert resp.status_code == 200
                data = resp.json()
                msg = data["messages"][0]
                assert "Kết nối" in msg or "Google" in msg
                assert "✅ Lịch Google" in msg
                assert "✅ Gmail (đọc)" in msg
                mock_conns.assert_called_once_with(42)
                mock_turn.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_personal_tasks_uses_owner_key(client, gateway_settings):
    """Test that personal tasks queries use verified owner_key and format list."""
    headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-tasks-1",
        "gateway_user_id": 42,
        "telegram_user_id": 777,
        "chat_id": 777,
        "text": "Xem danh sách công việc của tôi",
    }

    expected_owner_key = build_owner_key(MOCK_USER["tenant"], MOCK_USER["database"], MOCK_USER["odoo_uid"])
    mock_tasks = [
        {
            "id": "task_1",
            "title": "Soạn báo cáo tháng 10",
            "status": "pending",
            "priority": "high",
            "due_at": "2026-10-26T17:00:00+07:00",
            "owner_key": expected_owner_key,
        }
    ]

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = MOCK_USER
        with patch("app.providers.tasks.local.LocalTaskProvider.list_tasks", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_tasks
            with patch("app.assistant.company_assistant.CompanyAssistant.handle_user_turn") as mock_turn:
                resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
                assert resp.status_code == 200
                data = resp.json()
                msg = data["messages"][0]
                assert "Soạn báo cáo tháng 10" in msg
                mock_list.assert_called_once()
                call_kwargs = mock_list.call_args[1]
                assert call_kwargs.get("owner_key") == expected_owner_key
                mock_turn.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_blocks_unsupported_intents_before_execution(client, gateway_settings):
    """Test that write-oriented and unsupported intents are blocked before execution
    without calling Assistant or generating side effects or fallback sample data.
    """
    headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-unsupp-1",
        "gateway_user_id": 42,
        "telegram_user_id": 777,
        "chat_id": 777,
        "text": "Soạn email gửi đối tác về hợp đồng mới",
    }

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = MOCK_USER
        with patch("app.assistant.company_assistant.CompanyAssistant.handle_user_turn") as mock_turn:
            resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            msg = data["messages"][0]
            assert "chưa được hỗ trợ qua Gateway" in msg
            mock_turn.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_missing_owner_identity_refuses_tasks(client, gateway_settings):
    """Test that missing tenant, database, or odoo_uid refuses reading tasks,
    never falling back into legacy task scope.
    """
    headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-missing-id",
        "gateway_user_id": 42,
        "telegram_user_id": 777,
        "chat_id": 777,
        "text": "Xem việc của tôi",
    }

    user_without_uid = dict(MOCK_USER)
    user_without_uid["odoo_uid"] = None

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = user_without_uid
        with patch("app.providers.tasks.local.LocalTaskProvider.list_tasks", new_callable=AsyncMock) as mock_list:
            resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            msg = data["messages"][0]
            assert "Không thể tải danh sách công việc" in msg
            assert "Thông tin định danh không đầy đủ" in msg
            # Did not call list_tasks with None!
            mock_list.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_contract_request_id_and_messages(client, gateway_settings):
    """Test that response strictly adheres to contract: request_id matches and messages is list[str]."""
    headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
    payload = {
        "request_id": "req-contract-strict-99",
        "gateway_user_id": 42,
        "telegram_user_id": 777,
        "chat_id": 777,
        "text": "/help",
    }

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = MOCK_USER
        resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["request_id"] == "req-contract-strict-99"
        assert isinstance(data["messages"], list)
        assert len(data["messages"]) >= 1
        assert all(isinstance(m, str) for m in data["messages"])

