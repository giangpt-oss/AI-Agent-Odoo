import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app


@pytest.fixture
def client_with_agent_secret():
    client = TestClient(app)
    return client


def test_workspace_job_completed_webhook_auth_failure(client_with_agent_secret):
    resp = client_with_agent_secret.post(
        "/internal/v1/workspace/job-completed",
        json={"job_id": "job_1", "user_id": 1, "request_id": "req_1", "status": "completed", "service": "drive", "operation": "files.list"},
        headers={"Authorization": "Bearer wrong_secret"},
    )
    assert resp.status_code == 401


def test_workspace_job_completed_webhook_success(client_with_agent_secret):
    settings = app.state.settings if hasattr(app.state, "settings") and app.state.settings else None
    secret = getattr(settings, "GATEWAY_TO_AGENT_SECRET", None) or "hopita_gw_to_agent_secret_key_32chars_min_1234"
    
    payload = {
        "job_id": "job_success_123",
        "user_id": 1,
        "request_id": "req_123",
        "status": "completed",
        "service": "drive",
        "operation": "files.list",
        "result": {"files": [{"id": "f1", "name": "Report.docx"}]},
        "telegram_chat_id": 99999,
        "telegram_user_id": 88888,
        "telegram_binding_id": "bound-test",
    }
    
    with patch("app.services.conversation_history_store.conversation_history_store.append_turn") as mock_append, patch(
        "app.api.v1.internal.gateway_client.resolve_identity", new=AsyncMock(return_value={
            "user_id": 1, "telegram_binding_id": "bound-test", "tenant": "test", "database": "test",
        }),
    ):
        resp = client_with_agent_secret.post(
            "/internal/v1/workspace/job-completed",
            json=payload,
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        assert resp.json()["job_id"] == "job_success_123"
        mock_append.assert_called_once()
        assert mock_append.call_args.kwargs["event_id"] == "workspace:job_success_123"
        assert resp.json()["messages"]

