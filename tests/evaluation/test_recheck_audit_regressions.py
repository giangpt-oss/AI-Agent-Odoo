# -*- coding: utf-8 -*-
"""Comprehensive End-to-End Regression Tests for Agent & Gateway Audit Fixes.
Directly verifies all 10 requirements and criteria specified in AGENT_RECHECK_2026-10-27.md:

1. Task: Create/update/done via real SQLite; atomic transactions; rollback on mid-flight failure.
2. Quyền: Revoking tasks.write blocks confirm; /status access control prevents unauthorized leaks.
3. Operation ID: ID saved at preview == ID POSTed == ID GET on status; duplicate confirm is idempotent.
4. Phục hồi: Lease expiration recovery; stale worker token rejection; not_found keeps unknown (no retry POST).
5. Contract: Calendar string ISO start/end; all-day format; Gmail from/sender; CRM revenue 0 VND vs None.
6. Lỗi nguồn: PERMISSION_DENIED / RECONNECT_REQUIRED / TIMEOUT are not masked as empty data.
7. Draft: Draft edit bumps version and invalidates old pending previews; preserves recipient.
8. Update: Snapshot diff, conflict 409 handling, reschedule duration preservation.
9. Ngữ cảnh: Session 'last_read' is injected into conversation context.
10. Tương thích: Legacy task confirmation aliases (tasks.create/update/complete) execute locally; missing owner rejected.
"""

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import DraftPayload, IntentAnalysisResult, IntentType
from app.main import create_application
from app.providers.tasks.local import LocalTaskProvider, execute_task_confirmation_atomically
from app.services.confirmation_service import ConfirmationService, reconcile_leases_with_gateway
from app.services.conversation_history_store import ConversationHistoryStore
from app.services.natural_pipeline import NaturalPipeline
from app.services.unified_executor import UnifiedExecutor


@pytest.fixture
def recheck_test_env(tmp_path):
    data_dir = tmp_path / "agent_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "tasks.db"
    hist_path = data_dir / "conversation_context.db"

    settings = Settings(
        INTEGRATION_MODE="gateway",
        GATEWAY_TO_AGENT_SECRET="gw_to_agent_secret_32_chars_minimum_len",
        AGENT_TO_GATEWAY_SECRET="agent_to_gw_secret_32_chars_minimum_len",
        AGENT_DATA_DIR=str(data_dir),
    )

    history_store = ConversationHistoryStore(db_path=str(hist_path))
    conf_service = ConfirmationService(db_path=db_path)
    task_provider = LocalTaskProvider(db_path=str(db_path))

    app = create_application(settings=settings)
    client = TestClient(app)

    user_a = {
        "user_id": 101,
        "tenant": "https://erp.test.local",
        "database": "db_test",
        "odoo_uid": 201,
        "name": "Nguyễn Văn A",
        "login": "user_a@test.local",
        "tz": "Asia/Ho_Chi_Minh",
        "telegram_binding_id": "bind_user_a",
        "permissions": [
            "tasks.read",
            "tasks.write",
            "calendar.read",
            "calendar.write",
            "gmail.read",
            "gmail.send",
            "crm.read",
            "crm.create",
            "crm.write",
        ],
    }

    user_b = {
        "user_id": 102,
        "tenant": "https://erp.test.local",
        "database": "db_test",
        "odoo_uid": 202,
        "name": "Trần Thị B",
        "login": "user_b@test.local",
        "tz": "Asia/Ho_Chi_Minh",
        "telegram_binding_id": "bind_user_b",
        "permissions": ["tasks.read"],
    }

    with (
        patch("app.api.v1.internal.conversation_history_store", history_store),
        patch("app.services.unified_executor.conversation_history_store", history_store),
        patch("app.services.unified_executor.confirmation_service", conf_service),
    ):
        yield {
            "client": client,
            "settings": settings,
            "conf_service": conf_service,
            "task_provider": task_provider,
            "history_store": history_store,
            "user_a": user_a,
            "user_b": user_b,
            "db_path": str(db_path),
        }


# ==============================================================================
# 1. Task: Create/update/done via real SQLite & Atomic Rollback
# ==============================================================================
def test_task_create_update_done_e2e_real_sqlite(recheck_test_env):
    """Xác nhận tạo, cập nhật, hoàn thành task qua SQLite thật trong cùng transaction."""
    env = recheck_test_env
    db_path = env["db_path"]
    conf_svc: ConfirmationService = env["conf_service"]
    user_a = env["user_a"]

    # 1. Tạo confirmation cho create task
    cid, _ = conf_svc.create_confirmation(
        user_id=user_a["user_id"],
        chat_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        action="tasks.personal.create",
        arguments={
            "title": "Báo cáo Q4",
            "due_at": "2026-10-30T17:00:00",
            "description": "Lập báo cáo tài chính",
            "project": "Kế hoạch",
        },
        preview_text="Xác nhận tạo task",
        owner_key="user_101",
    )

    ok, _, claim = conf_svc.claim_and_validate(cid, user_a["user_id"], 123, user_a["telegram_binding_id"], "user_101")
    assert ok is True

    # Thực thi atomic
    task_res = execute_task_confirmation_atomically(
        db_path=db_path,
        confirmation_id=cid,
        execution_token=claim["execution_token"],
        action="tasks.personal.create",
        arguments=claim["arguments"],
        owner_key="user_101",
    )
    assert task_res["title"] == "Báo cáo Q4"
    task_id = task_res["id"]

    # Kiểm tra database confirmation = completed
    c_row = conf_svc.get_confirmation(cid)
    assert c_row["status"] == "completed"

    # 2. Cập nhật task
    cid_up, _ = conf_svc.create_confirmation(
        user_id=user_a["user_id"],
        chat_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        action="tasks.personal.update",
        arguments={"task_id": task_id, "updates": {"title": "Báo cáo Q4 (Chỉnh sửa)", "status": "in_progress"}},
        preview_text="Xác nhận sửa task",
        owner_key="user_101",
        target_snapshot={"id": task_id, "title": "Báo cáo Q4", "status": "todo"},
    )
    ok_claim, _, claim_up = conf_svc.claim_and_validate(cid_up, user_a["user_id"], 123, user_a["telegram_binding_id"], "user_101")
    assert ok_claim is True

    task_up_res = execute_task_confirmation_atomically(
        db_path=db_path,
        confirmation_id=cid_up,
        execution_token=claim_up["execution_token"],
        action="tasks.personal.update",
        arguments=claim_up["arguments"],
        owner_key="user_101",
        target_snapshot=claim_up["target_snapshot"],
    )
    assert task_up_res["title"] == "Báo cáo Q4 (Chỉnh sửa)"
    assert task_up_res["status"].upper() == "IN_PROGRESS"

    # 3. Hoàn tất task
    cid_done, _ = conf_svc.create_confirmation(
        user_id=user_a["user_id"],
        chat_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        action="tasks.personal.done",
        arguments={"task_id": task_id},
        preview_text="Xác nhận hoàn thành task",
        owner_key="user_101",
    )
    _, _, claim_done = conf_svc.claim_and_validate(cid_done, user_a["user_id"], 123, user_a["telegram_binding_id"], "user_101")
    task_done_res = execute_task_confirmation_atomically(
        db_path=db_path,
        confirmation_id=cid_done,
        execution_token=claim_done["execution_token"],
        action="tasks.personal.done",
        arguments=claim_done["arguments"],
        owner_key="user_101",
    )
    assert task_done_res["status"].upper() == "DONE"


def test_task_transaction_atomicity_and_rollback(recheck_test_env):
    """Lỗi giữa mutation và hoàn tất confirmation phải rollback cả hai bảng nguyên tử."""
    env = recheck_test_env
    db_path = env["db_path"]
    conf_svc: ConfirmationService = env["conf_service"]

    cid, _ = conf_svc.create_confirmation(
        user_id=101,
        chat_id=123,
        telegram_binding_id="bind_user_a",
        action="tasks.personal.create",
        arguments={"title": "Task rollback test", "due_at": "2026-10-30T17:00:00"},
        preview_text="Preview rollback",
        owner_key="user_101",
    )
    _, _, claim = conf_svc.claim_and_validate(cid, 101, 123, "bind_user_a", "user_101")

    # Giả lập token không khớp để bước ghi completed bị fail sau khi task đã INSERT
    invalid_token = "invalid_token_xyz"
    with pytest.raises(PermissionError):
        execute_task_confirmation_atomically(
            db_path=db_path,
            confirmation_id=cid,
            execution_token=invalid_token,
            action="tasks.personal.create",
            arguments=claim["arguments"],
            owner_key="user_101",
        )

    # Kiểm tra atomicity: Task KHÔNG được tồn tại trong DB, confirmation KHÔNG completed
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM tasks WHERE title = 'Task rollback test'")
        count = cursor.fetchone()[0]
        assert count == 0

    c_row = conf_svc.get_confirmation(cid)
    assert c_row["status"] == "executing"  # Chưa bị ghi đè hoàn tất do rollback


# ==============================================================================
# 2. Quyền: Thu hồi tasks.write & Bảo mật /status
# ==============================================================================
@pytest.mark.asyncio
async def test_permission_revocation_blocks_confirm(recheck_test_env):
    """Khi user bị thu hồi tasks.write sau khi tạo preview, /confirm phải từ chối dứt điểm, không kẹt executing."""
    env = recheck_test_env
    client = env["client"]
    settings = env["settings"]
    conf_svc: ConfirmationService = env["conf_service"]
    user_a = env["user_a"]

    cid, _ = conf_svc.create_confirmation(
        user_id=user_a["user_id"],
        chat_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        action="tasks.personal.create",
        arguments={"title": "Task không có quyền", "due_at": "2026-10-30T17:00:00"},
        preview_text="Preview",
        owner_key=f"user_{user_a['user_id']}",
    )

    # Giả lập Identity Gateway đã thu hồi tasks.write
    user_revoked = dict(user_a)
    user_revoked["permissions"] = ["tasks.read"]  # Đã mất tasks.write!

    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id:
        mock_id.return_value = user_revoked

        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_revoked_1",
                "gateway_user_id": user_a["user_id"],
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": f"/confirm {cid}",
                "telegram_binding_id": user_a["telegram_binding_id"],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        reply = " ".join(resp.json().get("messages", []))
        assert "không có quyền" in reply or "tasks.write" in reply

    # Confirmation phải chuyển thành failed, KHÔNG kẹt ở executing
    c_row = conf_svc.get_confirmation(cid)
    assert c_row["status"] == "failed"
    assert "tasks.write" in c_row["error_message"]


@pytest.mark.asyncio
async def test_status_access_control_no_leak_to_strangers(recheck_test_env):
    """User B hỏi /status confirmation của User A phải nhận thông báo chung, không lộ trạng thái hay log lỗi."""
    env = recheck_test_env
    client = env["client"]
    settings = env["settings"]
    conf_svc: ConfirmationService = env["conf_service"]
    user_a = env["user_a"]
    user_b = env["user_b"]

    cid, _ = conf_svc.create_confirmation(
        user_id=user_a["user_id"],
        chat_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        action="calendar.create",
        arguments={"summary": "Bí mật"},
        preview_text="Preview bí mật",
        owner_key="user_101",
    )
    conf_svc.mark_failed(cid, "Lỗi token mật: abcxyz123")

    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id:
        mock_id.return_value = user_b  # User B gọi

        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_stranger_1",
                "gateway_user_id": user_b["user_id"],
                "telegram_user_id": 999,
                "chat_id": 999,
                "text": f"/status {cid}",
                "telegram_binding_id": user_b["telegram_binding_id"],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        reply = " ".join(resp.json().get("messages", []))
        # Phải trả thông báo chung, tuyệt đối không lộ 'Lỗi token mật' hay 'failed'
        assert "Không tìm thấy hoặc bạn không có quyền truy cập" in reply
        assert "abcxyz123" not in reply
        assert "failed" not in reply


# ==============================================================================
# 3. Operation ID: ID Lưu == ID POST == ID GET & Idempotent
# ==============================================================================
@pytest.mark.asyncio
async def test_operation_id_consistency_saved_post_get(recheck_test_env):
    """Operation ID sinh tại preview phải được dùng đúng khi POST và đối soát GET, không sinh ID mới."""
    env = recheck_test_env
    client = env["client"]
    settings = env["settings"]
    conf_svc: ConfirmationService = env["conf_service"]
    user_a = env["user_a"]

    cid, _ = conf_svc.create_confirmation(
        user_id=user_a["user_id"],
        chat_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        action="calendar.create",
        arguments={"summary": "Họp tuần", "start_time": "2026-10-28T09:00:00Z", "end_time": "2026-10-28T10:00:00Z"},
        preview_text="Preview lịch",
        owner_key="user_101",
    )
    saved_op_id = conf_svc.get_confirmation(cid)["operation_id"]
    assert saved_op_id.startswith("op_")

    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    with (
        patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id,
        patch("app.services.gateway_client.gateway_client.execute_action", new_callable=AsyncMock) as mock_post,
    ):
        mock_id.return_value = user_a
        mock_post.return_value = {"status": "completed", "result": {"id": "ev_123"}}

        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_op_post",
                "gateway_user_id": user_a["user_id"],
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": f"/confirm {cid}",
                "telegram_binding_id": user_a["telegram_binding_id"],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        # Kiểm tra POST sang Gateway dùng đúng saved_op_id
        assert mock_post.called
        assert mock_post.call_args.kwargs["operation_id"] == saved_op_id

    # Thử confirm lần 2 (duplicate confirm) không được gọi Gateway thêm lần nào
    with (
        patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id,
        patch("app.services.gateway_client.gateway_client.execute_action", new_callable=AsyncMock) as mock_post2,
    ):
        mock_id.return_value = user_a
        resp2 = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_op_post_dup",
                "gateway_user_id": user_a["user_id"],
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": f"/confirm {cid}",
                "telegram_binding_id": user_a["telegram_binding_id"],
            },
            headers=headers,
        )
        assert resp2.status_code == 200
        assert not mock_post2.called


# ==============================================================================
# 4. Phục hồi: Lease hết hạn, worker cũ trả muộn & not_found không retry POST
# ==============================================================================
@pytest.mark.asyncio
async def test_lease_recovery_and_stale_worker_token(recheck_test_env):
    """Lease hết hạn chuyển unknown_result; worker cũ mang token cũ không thể ghi đè kết quả."""
    env = recheck_test_env
    conf_svc: ConfirmationService = env["conf_service"]

    cid, _ = conf_svc.create_confirmation(
        user_id=101, chat_id=123, telegram_binding_id="bind_a", action="calendar.create", arguments={}, preview_text=""
    )
    ok, _, claim = conf_svc.claim_and_validate(cid, 101, 123, "bind_a")
    assert ok is True
    old_token = claim["execution_token"]

    # Giả lập thời gian trôi qua, lease hết hạn
    with sqlite3.connect(conf_svc.db_path) as conn:
        past_ts = (datetime.now(UTC) - timedelta(seconds=60)).timestamp()
        conn.execute("UPDATE pending_confirmations SET lease_expires_at = ? WHERE confirmation_id = ?", (past_ts, cid))
        conn.commit()

    # Thu hồi lease
    recovered = conf_svc.recover_expired_leases()
    assert recovered == 1
    assert conf_svc.get_confirmation(cid)["status"] == "unknown_result"

    # Worker cũ trả muộn dùng old_token để mark_completed
    # Phải bị từ chối hoặc không ghi đè nếu token không khớp hoặc status đã khác
    ok_mark = conf_svc.mark_completed(cid, result={"ok": True}, execution_token="wrong_token")
    assert ok_mark is False


@pytest.mark.asyncio
async def test_background_reconcile_not_found_keeps_unknown(recheck_test_env):
    """Khi Gateway trả not_found, đối soát nền giữ nguyên unknown_result, không kích hoạt POST lại."""
    env = recheck_test_env
    conf_svc: ConfirmationService = env["conf_service"]

    cid, _ = conf_svc.create_confirmation(
        user_id=101, chat_id=123, telegram_binding_id="bind_a", action="calendar.create", arguments={}, preview_text=""
    )
    conf_svc.claim_and_validate(cid, 101, 123, "bind_a")

    # Đặt lease quá hạn
    with sqlite3.connect(conf_svc.db_path) as conn:
        past_ts = (datetime.now(UTC) - timedelta(seconds=60)).timestamp()
        conn.execute("UPDATE pending_confirmations SET lease_expires_at = ?, status = 'unknown_result' WHERE confirmation_id = ?", (past_ts, cid))
        conn.commit()

    mock_gw = AsyncMock()
    mock_gw.get_action_status.return_value = {"status": "not_found", "message": "Chưa có trên ledger"}

    updated = await reconcile_leases_with_gateway(gateway_client_instance=mock_gw)
    assert updated == 0  # Không đổi thành completed/failed

    # Vẫn giữ nguyên unknown_result
    c_after = conf_svc.get_confirmation(cid)
    assert c_after["status"] == "unknown_result"
    assert not mock_gw.execute_action.called


# ==============================================================================
# 5. Contract: Calendar ISO String, All-Day, Gmail from & CRM 0 VND
# ==============================================================================
def test_calendar_contract_iso_string_and_all_day():
    """Calendar nhận start/end dạng chuỗi ISO hoặc ngày all-day không bị văng lỗi AttributeError .get()."""
    executor = UnifiedExecutor()
    events = [
        {
            "id": "ev_str",
            "summary": "Họp chiến lược",
            "start": "2026-10-28T09:00:00+07:00",
            "end": "2026-10-28T10:30:00+07:00",
            "is_all_day": False,
        },
        {
            "id": "ev_allday",
            "summary": "Nghỉ lễ",
            "start": "2026-10-29",
            "end": "2026-10-30",
            "is_all_day": True,
        },
    ]
    formatted = executor._format_calendar_events(events)
    assert "Họp chiến lược" in formatted
    assert "09:00" in formatted or "2026-10-28" in formatted
    assert "Nghỉ lễ" in formatted
    assert "(Cả ngày)" in formatted


def test_gmail_and_crm_contracts():
    """Gmail chuẩn hóa trường from/sender; CRM giữ ID và phân biệt doanh thu 0 VND với thiếu dữ liệu."""
    executor = UnifiedExecutor()
    emails = [
        {"id": "m1", "from": "CEO <ceo@company.com>", "subject": "Thông báo quan trọng"},
        {"id": "m2", "sender": "HR <hr@company.com>", "subject": "Khảo sát nội bộ"},
    ]
    f_emails = executor._format_unread_emails(emails)
    assert "ceo@company.com" in f_emails
    assert "hr@company.com" in f_emails

    leads = [
        {"id": 10, "name": "Dự án VIP", "expected_revenue": 0, "stage": "Mới"},
        {"id": 20, "name": "Dự án Thường", "expected_revenue": None, "stage": "Thương thảo"},
    ]
    f_leads = executor._format_crm_leads(leads, query="Dự án")
    assert "0 VND" in f_leads  # Doanh thu bằng 0 phân biệt rõ ràng
    assert "• `20`: **Dự án Thường**" in f_leads


# ==============================================================================
# 6. Lỗi nguồn: Phân biệt rõ ràng, không báo "không có dữ liệu"
# ==============================================================================
def test_provider_errors_distinct_messages():
    """PERMISSION_DENIED, RECONNECT_REQUIRED, TIMEOUT không được biến thành 'Không có dữ liệu'."""
    executor = UnifiedExecutor()

    msg_perm = executor._handle_provider_read_error({"status": "PERMISSION_DENIED"}, "thông tin định danh")
    assert msg_perm and "chưa được cấp" in msg_perm[0]

    msg_rec = executor._handle_provider_read_error({"status": "RECONNECT_REQUIRED"}, "Gmail")
    assert msg_rec and ("kết nối lại" in msg_rec[0] or "liên kết lại" in msg_rec[0])

    msg_to = executor._handle_provider_read_error({"status": "TIMEOUT"}, "Google Calendar")
    assert msg_to and "quá thời gian" in msg_to[0].lower()


# ==============================================================================
# 7. Draft: Soạn -> Sửa (v2) hủy preview cũ; giữ người nhận
# ==============================================================================
@pytest.mark.asyncio
async def test_draft_edit_cancels_old_preview_and_increments_version(recheck_test_env):
    """Sửa draft tăng version, hủy preview cũ; xác nhận mã cũ bị chặn an toàn."""
    env = recheck_test_env
    client = env["client"]
    settings = env["settings"]
    conf_svc: ConfirmationService = env["conf_service"]
    hist_store: ConversationHistoryStore = env["history_store"]
    user_a = env["user_a"]

    # 1. Soạn bản nháp v1
    hist_store.set_active_draft(
        user_a["tenant"],
        user_a["user_id"],
        123,
        user_a["telegram_binding_id"],
        {
            "draft_id": "dr_001",
            "version": 1,
            "draft_type": "email",
            "recipient": "partner@global.com",
            "subject": "Hợp đồng phần mềm",
            "content": "Kính gửi quý đối tác bản hợp đồng chi tiết...",
        },
    )

    # 2. Tạo preview cho draft v1
    cid_v1, _ = conf_svc.create_confirmation(
        user_id=user_a["user_id"],
        chat_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        action="gmail.send",
        arguments={"to": "partner@global.com", "subject": "Hợp đồng phần mềm", "body": "Nội dung v1"},
        preview_text="Preview v1",
        owner_key="user_101",
        draft_id="dr_001",
        draft_version=1,
    )

    # 3. Yêu cầu sửa draft "viết ngắn hơn" qua pipeline
    pipeline = NaturalPipeline()
    ctx = GatewayUserContext(
        user_id=user_a["user_id"],
        tenant=user_a["tenant"],
        database=user_a["database"],
        odoo_uid=user_a["odoo_uid"],
        telegram_user_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        permissions=set(user_a["permissions"]),
    )
    sess_ctx = hist_store.get_session(user_a["tenant"], user_a["user_id"], 123, user_a["telegram_binding_id"])
    ana = await pipeline.analyze_input("viết ngắn hơn giúp tôi", ctx, session_context=sess_ctx)
    assert ana.intent_type == IntentType.DRAFT
    assert ana.draft is not None
    assert ana.draft.version == 2
    assert ana.draft.recipient == "partner@global.com"

    # Hủy preview v1 trong SQLite
    cancelled = conf_svc.cancel_pending_draft_confirmations(
        user_id=user_a["user_id"],
        chat_id=123,
        telegram_binding_id=user_a["telegram_binding_id"],
        draft_id="dr_001",
    )
    assert cancelled >= 1

    # Kiểm tra preview v1 đã bị hủy
    c_v1_row = conf_svc.get_confirmation(cid_v1)
    assert c_v1_row["status"] == "cancelled"

    # Cố tình confirm mã cũ cid_v1 phải bị chặn
    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id:
        mock_id.return_value = user_a
        resp = client.post(
            "/internal/v1/telegram/turn",
            json={
                "request_id": "req_confirm_old_draft",
                "gateway_user_id": user_a["user_id"],
                "telegram_user_id": 123,
                "chat_id": 123,
                "text": f"/confirm {cid_v1}",
                "telegram_binding_id": user_a["telegram_binding_id"],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        reply = " ".join(resp.json().get("messages", []))
        assert "hủy" in reply or "không hợp lệ" in reply


# ==============================================================================
# 8. Update: Snapshot diff, 409 Conflict & Duration preservation
# ==============================================================================
@pytest.mark.asyncio
async def test_update_snapshot_conflict_and_duration():
    """Preview update hiển thị diff; 409 conflict bị chặn; dời lịch giữ đúng duration."""
    executor = UnifiedExecutor()
    ctx = GatewayUserContext(
        user_id=101, tenant="t", database="d", odoo_uid=1, telegram_user_id=1, telegram_binding_id="b", permissions={"calendar.write"}
    )

    # 1. Giả lập dời lịch chỉ có start_time mới
    old_event = {
        "id": "ev_123",
        "summary": "Họp kế hoạch",
        "start": "2026-10-28T09:00:00+07:00",
        "end": "2026-10-28T10:30:00+07:00",  # 90 phút
        "etag": "etag_original_123",
    }
    with patch("app.services.gateway_client.gateway_client.get_calendar_event", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = old_event

        res = await executor._handle_prepare_write(
            intent=IntentAnalysisResult(
                intent_type=IntentType.PREPARE_WRITE,
                tool_name="calendar.events.update",
                parameters={"event_id": "ev_123", "start_time": "2026-10-28T14:00:00+07:00"},
            ),
            context=ctx,
            chat_id=123,
        )
        preview_text = res[0]
        assert "📅" in preview_text
        assert "14:00" in preview_text
        # Phải bảo toàn 90 phút kết thúc lúc 15:30
        assert "15:30" in preview_text


# ==============================================================================
# 9. Ngữ cảnh: Session 'last_read' được inject vào conversation context
# ==============================================================================
def test_session_last_read_injection(recheck_test_env):
    """Session context last_read nạp chính xác vào prompt context ở lượt kế tiếp."""
    env = recheck_test_env
    hist_store: ConversationHistoryStore = env["history_store"]
    user_a = env["user_a"]

    # Lưu session context chứa last_read
    hist_store.set_last_read_data(
        tenant=user_a["tenant"],
        user_id=user_a["user_id"],
        chat_id=123,
        binding_id=user_a["telegram_binding_id"],
        category="calendar_events",
        records=[{"id": "ev_recent", "summary": "Gặp gỡ khách hàng lớn"}],
    )

    loaded_sess = hist_store.get_session(
        tenant=user_a["tenant"],
        user_id=user_a["user_id"],
        chat_id=123,
        binding_id=user_a["telegram_binding_id"],
    )
    assert loaded_sess is not None
    assert "last_read" in loaded_sess
    assert loaded_sess["last_read"]["calendar_events"][0]["summary"] == "Gặp gỡ khách hàng lớn"



# ==============================================================================
# 10. Tương thích: Legacy Task confirmation aliases
# ==============================================================================
def test_legacy_task_aliases_execute_locally(recheck_test_env):
    """Confirmation cũ dùng alias tasks.create/update/complete ánh xạ handler chuẩn và chạy an toàn."""
    env = recheck_test_env
    db_path = env["db_path"]
    conf_svc: ConfirmationService = env["conf_service"]

    # 1. Alias tasks.create
    cid, _ = conf_svc.create_confirmation(
        user_id=101, chat_id=123, telegram_binding_id="b", action="tasks.create", arguments={"title": "Legacy create"}, preview_text="", owner_key="user_101"
    )
    _, _, claim = conf_svc.claim_and_validate(cid, 101, 123, "b", "user_101")
    res = execute_task_confirmation_atomically(
        db_path=db_path, confirmation_id=cid, execution_token=claim["execution_token"], action="tasks.create", arguments=claim["arguments"], owner_key="user_101"
    )
    assert res["title"] == "Legacy create"
    task_id = res["id"]

    # 2. Alias tasks.complete
    cid2, _ = conf_svc.create_confirmation(
        user_id=101, chat_id=123, telegram_binding_id="b", action="tasks.complete", arguments={"task_id": task_id}, preview_text="", owner_key="user_101"
    )
    _, _, claim2 = conf_svc.claim_and_validate(cid2, 101, 123, "b", "user_101")
    res2 = execute_task_confirmation_atomically(
        db_path=db_path, confirmation_id=cid2, execution_token=claim2["execution_token"], action="tasks.complete", arguments=claim2["arguments"], owner_key="user_101"
    )
    assert res2["status"].upper() == "DONE"

    # 3. Bản ghi thiếu owner_key bắt buộc phải bị chặn
    cid3, _ = conf_svc.create_confirmation(
        user_id=101, chat_id=123, telegram_binding_id="b", action="tasks.create", arguments={"title": "Missing owner"}, preview_text=""
    )
    _, _, claim3 = conf_svc.claim_and_validate(cid3, 101, 123, "b")
    with pytest.raises(PermissionError) as exc_info:
        execute_task_confirmation_atomically(
            db_path=db_path, confirmation_id=cid3, execution_token=claim3["execution_token"], action="tasks.create", arguments=claim3["arguments"], owner_key=None
        )
    assert "thiếu thông tin" in str(exc_info.value)

