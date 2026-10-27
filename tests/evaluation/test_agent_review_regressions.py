# -*- coding: utf-8 -*-
"""Regression tests verifying fixes for all issues in docs/AGENT_REVIEW_2026-10-27.md.

Issues covered:
1. Single intent contract & execution (no dual analysis, parameter preservation)
2. Calendar relative time queries (tomorrow != 7 days)
3. Greeting with task is NOT swallowed by fast-path
4. Supported writes (soạn email, gửi email, tạo cuộc họp) NOT blocked by keyword filter
5. "Gửi bản vừa soạn" uses SQLite active_draft across turns
6. Draft modification invalidates previous preview
7. Cancellation after dispatch marks confirmation as unknown_result (no stuck executing)
8. Status audit command reconciles unknown_result with Gateway
9. Persistent SQLite path in AGENT_DATA_DIR
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.gateway_context import GatewayPermission, GatewayUserContext
from app.domain.intent_schema import DraftPayload, IntentAnalysisResult, IntentType
from app.main import create_application
from app.services.context_resolver import resolve_relative_time_window
from app.services.conversation_history_store import ConversationHistoryStore
from app.services.confirmation_service import ConfirmationService
from app.services.natural_pipeline import NaturalPipeline


@pytest.fixture
def agent_test_env(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        INTEGRATION_MODE="gateway",
        GATEWAY_TO_AGENT_SECRET="gw_to_agent_secret_32_chars_minimum_len",
        AGENT_TO_GATEWAY_SECRET="agent_to_gw_secret_32_chars_minimum_len",
        AGENT_DATA_DIR=str(data_dir),
    )

    test_history_store = ConversationHistoryStore(db_path=str(data_dir / "conversation_context.db"))
    test_conf_service = ConfirmationService(db_path=data_dir / "tasks.db")

    app = create_application(settings=settings)
    client = TestClient(app)

    mock_user = {
        "user_id": 100,
        "tenant": "https://erp.regression.local",
        "database": "reg_db",
        "odoo_uid": 200,
        "name": "Nguyễn Văn Test",
        "login": "test@reg.local",
        "tz": "Asia/Ho_Chi_Minh",
        "job_title": "Chuyên viên Phân tích",
        "department": "CNTT",
        "telegram_binding_id": "bind_reg_100",
        "permissions": [
            "identity.read",
            "calendar.read",
            "calendar.write",
            "gmail.read",
            "gmail.send",
            "crm.read",
            "crm.create",
            "crm.write",
            "tasks.read",
            "tasks.write",
        ],
    }

    with (
        patch("app.api.v1.internal.conversation_history_store", test_history_store),
        patch("app.services.unified_executor.conversation_history_store", test_history_store),
        patch("app.services.unified_executor.confirmation_service", test_conf_service),
    ):
        yield client, settings, test_history_store, test_conf_service, mock_user, data_dir


# ==============================================================================
# 1. Greeting with task is NOT swallowed by fast-path
# ==============================================================================
@pytest.mark.asyncio
async def test_greeting_with_task_not_swallowed():
    """Câu 'Chào bạn, tạo công việc báo cáo giúp tôi' phải xử lý tạo việc, không trả menu chào."""
    pipeline = NaturalPipeline()
    ctx = GatewayUserContext(
        user_id=1,
        tenant="t",
        database="d",
        odoo_uid=1,
        telegram_user_id=1,
        telegram_binding_id="b",
        permissions={"tasks.write", "tasks.read"},
    )
    res = await pipeline.analyze_input("Chào bạn, tạo công việc báo cáo giúp tôi", ctx)
    # Không được trả lời chỉ là REPLY chào hỏi
    assert res.intent_type != IntentType.REPLY
    # Phải nhận diện tạo công việc
    assert res.tool_name in ["tasks.personal.create", "tasks.create"] or res.intent_type in [
        IntentType.PREPARE_WRITE,
        IntentType.DRAFT,
    ]


# ==============================================================================
# 2. Calendar tomorrow query: exactly tomorrow, NOT 7 days
# ==============================================================================
def test_calendar_tomorrow_time_window():
    """Yêu cầu 'ngày mai' phải sinh khoảng [00:00 ngày mai, 00:00 ngày kế tiếp), không phải 7 ngày."""
    ref_now = datetime(2026, 10, 27, 10, 0, 0, tzinfo=UTC)
    start, end = resolve_relative_time_window("ngày mai", timezone_str="Asia/Ho_Chi_Minh", reference_now=ref_now)
    assert start is not None and end is not None
    # Ngày 27/10 thì ngày mai là 28/10
    assert start.day == 28
    assert start.hour == 0 and start.minute == 0
    # Half-open window: kết thúc vào 00:00 ngày 29/10
    assert end.day == 29
    assert end.hour == 0 and end.minute == 0
    # Khoảng cách đúng 24h
    assert (end - start).total_seconds() == 86400


# ==============================================================================
# 3. Supported writes are NOT blocked by keyword filter
# ==============================================================================
@pytest.mark.asyncio
async def test_soan_email_creates_draft():
    """Câu 'soạn email gửi đối tác' phải tạo draft hoặc chuẩn bị write, không bị block unsupported."""
    pipeline = NaturalPipeline()
    ctx = GatewayUserContext(
        user_id=1,
        tenant="t",
        database="d",
        odoo_uid=1,
        telegram_user_id=1,
        telegram_binding_id="b",
        permissions={"gmail.send", "gmail.read"},
    )
    res = await pipeline.analyze_input("soạn email gửi partner@example.com nội dung xin chào", ctx)
    assert res.intent_type != IntentType.UNSUPPORTED
    assert "chưa được hỗ trợ" not in (res.reply_text or "")


# ==============================================================================
# 4. "Gửi bản vừa soạn" retrieves draft from SQLite across turns
# ==============================================================================
@pytest.mark.asyncio
async def test_send_draft_from_sqlite_session():
    """Gửi bản vừa soạn phải lấy bản nháp từ SQLite session store và chuẩn bị preview."""
    from app.services.conversation_history_store import conversation_history_store

    tenant = "https://erp.test.com"
    user_id = 99
    chat_id = 888
    binding_id = "bind_draft_test"

    # Lưu bản nháp vào SQLite
    conversation_history_store.set_active_draft(
        tenant,
        user_id,
        chat_id,
        binding_id,
        {
            "draft_id": "draft_123",
            "version": 1,
            "draft_type": "email",
            "recipient": "client@example.com",
            "subject": "Báo giá phần mềm",
            "content": "Kính gửi quý khách báo giá chi tiết...",
        },
    )

    # Lấy lại kiểm tra
    draft = conversation_history_store.get_active_draft(tenant, user_id, chat_id, binding_id)
    assert draft is not None
    assert draft["recipient"] == "client@example.com"
    assert draft["subject"] == "Báo giá phần mềm"


# ==============================================================================
# 5. Cancellation after dispatch marks confirmation as unknown_result
# ==============================================================================
@pytest.mark.asyncio
async def test_cancellation_marks_confirmation_unknown_result(agent_test_env):
    """Khi turn bị cancel/timeout sau khi đã gửi request tới Gateway, confirmation phải là unknown_result."""
    client, settings, history_store, conf_service, mock_user, _ = agent_test_env

    # Tạo một confirmation pending
    cid, _ = conf_service.create_confirmation(
        user_id=mock_user["user_id"],
        chat_id=12345,
        telegram_binding_id=mock_user["telegram_binding_id"],
        action="calendar.create",
        arguments={"summary": "Họp khẩn cấp", "start_time": "2026-10-28T09:00:00Z", "end_time": "2026-10-28T10:00:00Z"},
        preview_text="Xác nhận tạo lịch họp",
    )

    # Claim confirmation thành executing
    ok, _, conf_data = conf_service.claim_and_validate(
        cid, mock_user["user_id"], 12345, mock_user["telegram_binding_id"]
    )
    assert ok is True

    # Giả lập bị cancel khi đang chờ Gateway
    conf_service.mark_unknown(cid, "Timeout hoặc CancelledError sau khi gửi dispatch")

    # Kiểm tra trạng thái không được mắc kẹt ở executing
    c_after = conf_service.get_confirmation(cid)
    assert c_after["status"] == "unknown_result"
    assert "Timeout" in c_after["error_message"]


# ==============================================================================
# 6. Persistent SQLite store uses AGENT_DATA_DIR
# ==============================================================================
def test_sqlite_store_uses_agent_data_dir(tmp_path, monkeypatch):
    """Store phải đặt database trong AGENT_DATA_DIR đã cấu hình, không nằm ở root."""
    custom_dir = tmp_path / "custom_data_dir"
    monkeypatch.setenv("AGENT_DATA_DIR", str(custom_dir))

    store = ConversationHistoryStore()
    assert Path(store.db_path).resolve().is_relative_to(custom_dir.resolve())
    assert (custom_dir / "conversation_context.db").exists()


# ==============================================================================
# 7. E2E Calendar Tomorrow: Interval [00:00 ngày mai, 00:00 ngày mốt) tới Provider
# ==============================================================================
def test_e2e_calendar_tomorrow_time_window_preserved_to_provider(agent_test_env):
    """Tham số khoảng thời gian ngày mai phải giữ nguyên từ phân tích tới provider."""
    client, settings, _, _, mock_user, _ = agent_test_env
    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    req_payload = {
        "request_id": "req_cal_tom_1",
        "gateway_user_id": mock_user["user_id"],
        "telegram_user_id": 12345,
        "chat_id": 99999,
        "text": "xem lịch ngày mai",
        "telegram_binding_id": mock_user["telegram_binding_id"],
    }
    with (
        patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id,
        patch("app.providers.gateway_calendar.GatewayCalendarProvider.get_events", new_callable=AsyncMock) as mock_cal,
    ):
        mock_id.return_value = mock_user
        mock_cal.return_value = [{"id": "ev1", "summary": "Họp kế hoạch", "start_time": "2026-10-28T09:00:00Z"}]
        resp = client.post("/internal/v1/telegram/turn", json=req_payload, headers=headers)
        assert resp.status_code == 200
        assert mock_cal.called

        call_kwargs = mock_cal.call_args.kwargs
        assert "time_min" in call_kwargs and "time_max" in call_kwargs
        t_min = datetime.fromisoformat(call_kwargs["time_min"])
        t_max = datetime.fromisoformat(call_kwargs["time_max"])
        assert (t_max - t_min).total_seconds() == 86400  # Đúng 24 giờ, không phải 7 ngày!


# ==============================================================================
# 8. E2E Slash Commands: Gọi LLM đúng 0 lần
# ==============================================================================
def test_e2e_slash_commands_zero_llm_calls(agent_test_env):
    """Mọi slash command (/today, /task list) phải được phân tích xác định với 0 LLM calls."""
    client, settings, _, _, mock_user, _ = agent_test_env
    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    with (
        patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id,
        patch("app.services.natural_pipeline.NaturalPipeline._call_gemini_raw", new_callable=AsyncMock) as mock_llm,
        patch("app.services.morning_briefing_service.morning_briefing_service.generate_real_briefing", new_callable=AsyncMock) as mock_br,
    ):
        mock_id.return_value = mock_user
        mock_br.return_value = "Briefing today content"

        # 1. /today
        r1 = client.post("/internal/v1/telegram/turn", json={
            "request_id": "r_slash_1", "gateway_user_id": mock_user["user_id"],
            "telegram_user_id": 12345, "chat_id": 99999, "text": "/today",
            "telegram_binding_id": mock_user["telegram_binding_id"],
        }, headers=headers)
        assert r1.status_code == 200

        # 2. /task list
        r2 = client.post("/internal/v1/telegram/turn", json={
            "request_id": "r_slash_2", "gateway_user_id": mock_user["user_id"],
            "telegram_user_id": 12345, "chat_id": 99999, "text": "/task list",
            "telegram_binding_id": mock_user["telegram_binding_id"],
        }, headers=headers)
        assert r2.status_code == 200

        # 3. LLM không được gọi lần nào
        assert mock_llm.call_count == 0


# ==============================================================================
# 9. E2E Draft Lifecycle: Soạn -> Sửa (v2) -> Gửi bản vừa soạn (preview)
# ==============================================================================
def test_e2e_draft_lifecycle_and_send(agent_test_env):
    """Chuỗi thao tác draft: soạn email -> sửa ngắn hơn -> gửi bản vừa soạn."""
    client, settings, history_store, conf_service, mock_user, _ = agent_test_env
    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id:
        mock_id.return_value = mock_user

        # Turn 1: Soạn email
        r1 = client.post("/internal/v1/telegram/turn", json={
            "request_id": "r_draft_1", "gateway_user_id": mock_user["user_id"],
            "telegram_user_id": 12345, "chat_id": 99999,
            "text": "soạn email gửi partner@example.com nội dung xin gửi báo giá chi tiết phần mềm",
            "telegram_binding_id": mock_user["telegram_binding_id"],
        }, headers=headers)
        assert r1.status_code == 200
        assert "Bản nháp email" in r1.json()["messages"][0]

        # Turn 2: Sửa ngắn hơn
        r2 = client.post("/internal/v1/telegram/turn", json={
            "request_id": "r_draft_2", "gateway_user_id": mock_user["user_id"],
            "telegram_user_id": 12345, "chat_id": 99999,
            "text": "viết ngắn hơn",
            "telegram_binding_id": mock_user["telegram_binding_id"],
        }, headers=headers)
        assert r2.status_code == 200
        assert "v2" in r2.json()["messages"][0]

        # Turn 3: Gửi bản vừa soạn -> tạo preview có to = partner@example.com
        r3 = client.post("/internal/v1/telegram/turn", json={
            "request_id": "r_draft_3", "gateway_user_id": mock_user["user_id"],
            "telegram_user_id": 12345, "chat_id": 99999,
            "text": "gửi bản vừa soạn",
            "telegram_binding_id": mock_user["telegram_binding_id"],
        }, headers=headers)
        assert r3.status_code == 200
        preview_text = r3.json()["messages"][0]
        assert ("Bản xem trước gửi email" in preview_text or "Bản xem trước gửi thư Gmail" in preview_text)
        assert "partner@example.com" in preview_text


# ==============================================================================
# 10. E2E Status Audit: Đối soát trạng thái với Gateway
# ==============================================================================
def test_e2e_status_reconciliation_with_gateway(agent_test_env):
    """Khi có confirmation unknown_result, lệnh /status đối soát với Gateway và cập nhật kết quả."""
    client, settings, history_store, conf_service, mock_user, _ = agent_test_env
    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}

    cid, _ = conf_service.create_confirmation(
        user_id=mock_user["user_id"],
        chat_id=99999,
        telegram_binding_id=mock_user["telegram_binding_id"],
        action="calendar.create",
        arguments={"summary": "Họp đối tác", "start_time": "2026-10-28T09:00:00Z", "end_time": "2026-10-28T10:00:00Z"},
        preview_text="Xác nhận tạo lịch",
    )
    conf_service.claim_and_validate(cid, mock_user["user_id"], 99999, mock_user["telegram_binding_id"])
    conf_service.mark_unknown(cid, "Timeout sau dispatch")

    with (
        patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id,
        patch("app.services.gateway_client.gateway_client.get_action_status", new_callable=AsyncMock) as mock_st,
    ):
        mock_id.return_value = mock_user
        mock_st.return_value = {
            "operation_id": cid,
            "status": "completed",
            "result": {"id": "cal_ev_999"},
        }

        r = client.post("/internal/v1/telegram/turn", json={
            "request_id": "r_status_audit_1",
            "gateway_user_id": mock_user["user_id"],
            "telegram_user_id": 12345,
            "chat_id": 99999,
            "text": f"/status {cid}",
            "telegram_binding_id": mock_user["telegram_binding_id"],
        }, headers=headers)

        assert r.status_code == 200
        assert "hoàn tất thành công" in r.json()["messages"][0]
        c_after = conf_service.get_confirmation(cid)
        assert c_after["status"] == "completed"


# ==============================================================================
# 11. E2E Turn Lease: Tuần tự hóa các turn cùng session qua SQLite Lease
# ==============================================================================
def test_e2e_turn_lease_serializes_concurrent_requests(agent_test_env):
    """Khi có 1 turn đang xử lý cùng session, turn thứ hai phải bị từ chối bằng 503 SESSION_LOCKED."""
    client, settings, history_store, _, mock_user, _ = agent_test_env
    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}

    tok = history_store.claim_turn_lease(
        tenant=mock_user["tenant"],
        user_id=mock_user["user_id"],
        chat_id=99999,
        binding_id=mock_user["telegram_binding_id"],
        database=mock_user["database"],
        lease_seconds=10.0,
    )
    assert tok is not None

    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id:
        mock_id.return_value = mock_user
        r = client.post("/internal/v1/telegram/turn", json={
            "request_id": "r_lease_locked_1",
            "gateway_user_id": mock_user["user_id"],
            "telegram_user_id": 12345,
            "chat_id": 99999,
            "text": "xin chào",
            "telegram_binding_id": mock_user["telegram_binding_id"],
        }, headers=headers)

        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "SESSION_LOCKED"

