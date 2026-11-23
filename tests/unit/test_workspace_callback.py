import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_application


CALLBACK_SECRET = "workspace-callback-gateway-fixture-secret"


@pytest.fixture
def client_with_agent_secret():
    settings = Settings(
        _env_file=None,
        INTEGRATION_MODE="gateway",
        GATEWAY_TO_AGENT_SECRET=CALLBACK_SECRET,
        AGENT_TO_GATEWAY_SECRET="workspace-callback-agent-fixture-secret",
    )
    return TestClient(create_application(settings))


def test_workspace_job_completed_webhook_auth_failure(client_with_agent_secret):
    resp = client_with_agent_secret.post(
        "/internal/v1/workspace/job-completed",
        json={"job_id": "job_1", "user_id": 1, "request_id": "req_1", "status": "completed", "service": "drive", "operation": "files.list"},
        headers={"Authorization": "Bearer wrong_secret"},
    )
    assert resp.status_code == 401


def test_workspace_job_completed_webhook_success(client_with_agent_secret):
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
            headers={"Authorization": f"Bearer {CALLBACK_SECRET}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        assert resp.json()["job_id"] == "job_success_123"
        mock_append.assert_called_once()
        assert mock_append.call_args.kwargs["event_id"] == "workspace:job_success_123"
        assert resp.json()["messages"]



def test_drive_background_callback_answers_from_saved_evidence(client_with_agent_secret, monkeypatch, tmp_path):
    import base64
    from app.knowledge import drive
    from app.services.conversation_history_store import ConversationHistoryStore
    from app.api.v1 import internal

    identity = {"user_id": 1, "telegram_binding_id": "bound-drive", "tenant": "test", "database": "test"}
    monkeypatch.setattr(internal.gateway_client, "resolve_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(drive.gateway_client, "drive_knowledge", AsyncMock(side_effect=AssertionError("Downloaded again")))
    async def generate(question, evidence):
        assert question == "PN22 còn bao nhiêu bộ?"
        assert evidence[0].content == "Tồn kho PN22 là 15 bộ."
        return "PN22 còn 15 bộ [Nguồn [1]: Kho.txt].", evidence
    monkeypatch.setattr(drive.rag_service, "answer_from_evidence", generate)
    store = ConversationHistoryStore(str(tmp_path / "history.db"))
    monkeypatch.setattr(internal, "conversation_history_store", store)
    payload = {"job_id": "drive-bg", "user_id": 1, "request_id": "drive-turn", "status": "completed",
               "service": "drive", "operation": "knowledge.search", "telegram_chat_id": 7,
               "telegram_user_id": 7, "telegram_binding_id": "bound-drive",
               "result": {"status": "OK", "question": "PN22 còn bao nhiêu bộ?", "files": [
                   {"id": "fixture", "name": "Kho.txt", "suffix": ".txt",
                    "content_base64": base64.b64encode("Tồn kho PN22 là 15 bộ.".encode()).decode()}
               ]}}
    for _ in range(2):
        response = client_with_agent_secret.post("/internal/v1/workspace/job-completed", json=payload,
                                                headers={"Authorization": f"Bearer {CALLBACK_SECRET}"})
        assert response.status_code == 200
        assert "https://drive.google.com/open?id=fixture" in response.json()["messages"][0]
    assert len(store.get_session("test", 1, 7, "bound-drive", "test")["history"]) == 1


@pytest.mark.parametrize("link_checks, expected", [
    ([True, True], "accepted"),
    ([False], "withheld"),
    ([True, False], "withheld"),
    ([], "withheld"),
])
def test_indexed_drive_callback_guards_synthesis_and_delivery(
    client_with_agent_secret, monkeypatch, link_checks, expected,
):
    from app.api.v1 import internal
    from app.knowledge import drive

    identity = {"user_id": 1, "telegram_binding_id": "bound-drive", "tenant": "test", "database": "test"}
    monkeypatch.setattr(internal.gateway_client, "resolve_identity", AsyncMock(return_value=identity))
    check = AsyncMock(side_effect=link_checks)
    monkeypatch.setattr(internal.gateway_client, "drive_link_is_current", check, raising=False)
    generate = AsyncMock(side_effect=lambda query, evidence: (
        "PN22 còn 15 bộ [Nguồn [1]: Kho.txt - Mục tồn kho].", evidence,
    ))
    monkeypatch.setattr(drive.rag_service, "answer_from_evidence", generate)
    monkeypatch.setattr(drive, "extract_evidence", MagicMock(side_effect=AssertionError("Raw parsing")))
    append = MagicMock()
    monkeypatch.setattr(internal.conversation_history_store, "append_turn", append)
    payload = {
        "job_id": "drive-indexed", "user_id": 1, "request_id": "drive-turn", "status": "completed",
        "service": "drive", "operation": "knowledge.search", "telegram_chat_id": 7,
        "telegram_user_id": 7, "telegram_binding_id": "bound-drive",
        "result": {
            "format": "indexed_drive_v1", "status": "ok", "question": "PN22 còn bao nhiêu bộ?",
            "link": {"google_sub": "old-google-account", "link_version": 2},
            "verification": [{"file_id": "stock-file", "version": "3", "drive_id": None}],
            "chunks": [{"file_id": "stock-file", "name": "Kho.txt", "page_or_section": "Mục tồn kho",
                        "content": "Tồn kho PN22 là 15 bộ.", "score": 0.8}],
            "sources": [{"file_id": "stock-file", "name": "Kho.txt", "url": "https://drive.google.com/open?id=stock-file"}],
        },
    }
    verification = payload["result"]["verification"]
    if not link_checks:
        payload["result"].pop("verification")
    response = client_with_agent_secret.post(
        "/internal/v1/workspace/job-completed", json=payload,
        headers={"Authorization": f"Bearer {CALLBACK_SECRET}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == expected
    assert check.await_count == len(link_checks)
    assert all(call.args == (1, "old-google-account", 2, verification) for call in check.await_args_list)
    append.assert_not_called()
    if not link_checks or not link_checks[0]:
        generate.assert_not_awaited()
    else:
        assert generate.await_args.args[1][0].content == "Tồn kho PN22 là 15 bộ."
    if expected == "accepted":
        assert "Mục tồn kho" in data["messages"][0]
        assert "https://drive.google.com/open?id=stock-file" in data["messages"][0]
    else:
        assert data["messages"] == []
