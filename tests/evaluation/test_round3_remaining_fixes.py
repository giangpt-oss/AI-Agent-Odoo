"""
Bộ kiểm thử tái hiện và xác minh 3 thiếu sót còn lại của Agent:
1. Phục hồi confirmation cũ thiếu lease (lease NULL hoặc hết hạn) sang unknown_result và đối soát.
2. Chặn gửi confirmation nếu draft đã bị xóa, hết TTL hoặc lệch version (bằng chính xác version preview).
3. Compare-and-Swap draft thực sự nguyên tử với BEGIN IMMEDIATE (2 writer cạnh tranh v1 -> đúng 1 thành công v2, 1 conflict).
"""

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.services.confirmation_service import ConfirmationService, confirmation_service
from app.services.conversation_history_store import ConversationHistoryStore, conversation_history_store
from app.services.unified_executor import UnifiedExecutor

UTC = timezone.utc


@pytest.fixture
def remaining_fixes_env(tmp_path):
    conf_db = tmp_path / "confirmations.db"
    sess_db = tmp_path / "sessions.db"

    conf_svc = ConfirmationService(db_path=conf_db)
    hist_store = ConversationHistoryStore(db_path=str(sess_db))

    user_ctx = GatewayUserContext(
        user_id=1,
        tenant="https://erp.mycompany.com",
        database="prod_db",
        odoo_uid=105,
        telegram_user_id=12345,
        chat_id=999,
        telegram_binding_id="bind_fix_1",
        permissions={"gmail.send", "tasks.read", "tasks.write", "calendar.write"},
    )

    return {
        "conf_db": conf_db,
        "sess_db": sess_db,
        "conf_svc": conf_svc,
        "hist_store": hist_store,
        "user_ctx": user_ctx,
    }


# ==============================================================================
# 1. Confirmation cũ thiếu lease (lease NULL) phải được recovery chuyển unknown
# ==============================================================================
def test_recovery_handles_null_lease_executing_confirmations(remaining_fixes_env):
    """Mọi confirmation 'executing' có lease NULL phải được recovery chuyển sang unknown_result."""
    env = remaining_fixes_env
    conf_svc: ConfirmationService = env["conf_svc"]

    # Tạo bản ghi executing cũ có lease NULL
    cid, _ = conf_svc.create_confirmation(
        user_id=1,
        chat_id=999,
        telegram_binding_id="bind_fix_1",
        action="calendar.create",
        arguments={"summary": "Meeting with NULL lease"},
        preview_text="Xác nhận lịch họp",
        telegram_user_id=12345,
    )
    op_id = conf_svc.get_confirmation(cid)["operation_id"]
    with sqlite3.connect(conf_svc.db_path) as conn:
        conn.execute(
            "UPDATE pending_confirmations SET status = 'executing', execution_token = 'tok_old_1', lease_expires_at = NULL WHERE confirmation_id = ?",
            (cid,),
        )
        conn.commit()

    # Recovery lần 1
    recovered_count = conf_svc.recover_expired_leases()
    assert recovered_count == 1

    c_after = conf_svc.get_confirmation(cid)
    assert c_after["status"] == "unknown_result"
    assert c_after["execution_token"] is None
    assert c_after["lease_expires_at"] is None
    assert c_after["operation_id"] == op_id  # Giữ nguyên operation ID và payload

    # Recovery lần 2 (idempotent, không đổi trạng thái terminal)
    recovered_count_2 = conf_svc.recover_expired_leases()
    assert recovered_count_2 == 0
    c_after_2 = conf_svc.get_confirmation(cid)
    assert c_after_2["status"] == "unknown_result"


@pytest.mark.asyncio
async def test_status_handles_null_lease_and_missing_identity(remaining_fixes_env):
    """Khi /status gặp confirmation executing thiếu lease:
    - Nếu thiếu identity/operation_id -> giữ unknown_result và trả hướng dẫn đối soát thủ công rõ ràng.
    - Nếu đủ identity và operation_id -> đối soát Gateway thành công."""
    env = remaining_fixes_env
    conf_svc: ConfirmationService = env["conf_svc"]
    user_ctx: GatewayUserContext = env["user_ctx"]
    executor = UnifiedExecutor()

    # 1. Bản ghi thiếu operation_id và identity
    cid1, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id="bind_fix_1",
        action="calendar.create", arguments={}, preview_text=""
    )
    with sqlite3.connect(conf_svc.db_path) as conn:
        conn.execute(
            "UPDATE pending_confirmations SET status = 'executing', operation_id = '', telegram_user_id = NULL, lease_expires_at = NULL WHERE confirmation_id = ?",
            (cid1,),
        )
        conn.commit()

    with patch("app.services.unified_executor.confirmation_service", conf_svc):
        res1 = await executor._handle_status(
            intent=IntentAnalysisResult(intent_type=IntentType.ACTION_STATUS, confirmation_id=cid1),
            context=user_ctx,
            chat_id=999,
            telegram_user_id=0,  # Không có telegram_user_id
            remaining_time=10.0,
        )
    assert any("chưa rõ kết quả" in r.lower() or "quản trị viên" in r.lower() or "thủ công" in r.lower() for r in res1)
    c1 = conf_svc.get_confirmation(cid1)
    assert c1["status"] == "unknown_result"

    # 2. Bản ghi có đủ operation_id và telegram_user_id
    cid2, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id="bind_fix_1",
        action="calendar.create", arguments={}, preview_text="", telegram_user_id=12345
    )
    with sqlite3.connect(conf_svc.db_path) as conn:
        conn.execute(
            "UPDATE pending_confirmations SET status = 'executing', lease_expires_at = NULL WHERE confirmation_id = ?",
            (cid2,),
        )
        conn.commit()

    mock_gw = AsyncMock()
    mock_gw.get_action_status.return_value = {"status": "completed", "result": {"id": "ev_123"}}
    with (
        patch("app.services.unified_executor.confirmation_service", conf_svc),
        patch("app.services.unified_executor.gateway_client", mock_gw),
    ):
        res2 = await executor._handle_status(
            intent=IntentAnalysisResult(intent_type=IntentType.ACTION_STATUS, confirmation_id=cid2),
            context=user_ctx,
            chat_id=999,
            telegram_user_id=12345,
            remaining_time=10.0,
        )
    assert any("hoàn tất thành công" in r.lower() for r in res2)
    assert mock_gw.get_action_status.called
    c2 = conf_svc.get_confirmation(cid2)
    assert c2["status"] == "completed"


# ==============================================================================
# 2. Chặn gửi confirmation nếu draft đã bị xóa hoặc lệch phiên bản
# ==============================================================================
@pytest.mark.asyncio
async def test_confirm_blocked_if_draft_deleted_zero_gateway_calls(remaining_fixes_env):
    """Tạo draft -> preview -> xóa draft -> confirm: PHẢI CHẶN, gọi Gateway đúng 0 lần, confirmation = failed."""
    env = remaining_fixes_env
    conf_svc: ConfirmationService = env["conf_svc"]
    hist_store: ConversationHistoryStore = env["hist_store"]
    user_ctx: GatewayUserContext = env["user_ctx"]
    executor = UnifiedExecutor()

    # 1. Tạo draft v1
    hist_store.set_active_draft(
        user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id,
        {"draft_id": "draft_del_test", "version": 1, "recipient": "to@example.com", "subject": "Hi", "body": "Hello"},
        database=user_ctx.database,
    )

    # 2. Tạo preview gửi thư
    cid, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id=user_ctx.telegram_binding_id,
        action="gmail.send", arguments={"to": "to@example.com", "subject": "Hi", "body": "Hello"},
        preview_text="Gửi email", draft_id="draft_del_test", draft_version=1,
    )

    # 3. Người dùng XÓA bản nháp
    hist_store.clear_active_draft(
        user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id, database=user_ctx.database
    )

    # 4. Người dùng cố tình confirm mã cũ
    mock_gw = AsyncMock()
    with (
        patch("app.services.unified_executor.confirmation_service", conf_svc),
        patch("app.services.unified_executor.conversation_history_store", hist_store),
        patch("app.services.unified_executor.gateway_client", mock_gw),
    ):
        res = await executor._handle_confirm(
            intent=IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=cid),
            context=user_ctx,
            chat_id=999,
            telegram_user_id=user_ctx.telegram_user_id,
            remaining_time=10.0,
        )

    # BẮT BUỘC: 0 lần gọi Gateway
    assert mock_gw.execute_action.call_count == 0
    assert any("đã bị xóa" in r.lower() or "không còn tồn tại" in r.lower() for r in res)
    c_after = conf_svc.get_confirmation(cid)
    assert c_after["status"] == "failed"


@pytest.mark.asyncio
async def test_confirm_blocked_on_any_version_or_id_mismatch(remaining_fixes_env):
    """Draft khác ID hoặc khác version (dù version cao hơn hoặc thấp hơn) đều phải bị chặn."""
    env = remaining_fixes_env
    conf_svc: ConfirmationService = env["conf_svc"]
    hist_store: ConversationHistoryStore = env["hist_store"]
    user_ctx: GatewayUserContext = env["user_ctx"]
    executor = UnifiedExecutor()

    # Draft hiện tại đang ở version 2
    hist_store.set_active_draft(
        user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id,
        {"draft_id": "draft_ver_test", "version": 2, "recipient": "to@example.com", "subject": "Hi", "body": "Hello v2"},
        database=user_ctx.database,
    )

    # Confirmation kỳ vọng version 1 (version cũ)
    cid_old, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id=user_ctx.telegram_binding_id,
        action="gmail.send", arguments={"to": "to@example.com", "subject": "Hi", "body": "Hello v1"},
        preview_text="Gửi email v1", draft_id="draft_ver_test", draft_version=1,
    )

    # Confirmation kỳ vọng version 3 (version cao hơn)
    cid_future, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id=user_ctx.telegram_binding_id,
        action="gmail.send", arguments={"to": "to@example.com", "subject": "Hi", "body": "Hello v3"},
        preview_text="Gửi email v3", draft_id="draft_ver_test", draft_version=3,
    )

    mock_gw = AsyncMock()
    with (
        patch("app.services.unified_executor.confirmation_service", conf_svc),
        patch("app.services.unified_executor.conversation_history_store", hist_store),
        patch("app.services.unified_executor.gateway_client", mock_gw),
    ):
        # Xác nhận mã v1 -> BỊ CHẶN
        res1 = await executor._handle_confirm(
            intent=IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=cid_old),
            context=user_ctx, chat_id=999, telegram_user_id=12345, remaining_time=10.0,
        )
        assert mock_gw.execute_action.call_count == 0
        assert any("phiên bản" in r.lower() or "chỉnh sửa" in r.lower() for r in res1)
        assert conf_svc.get_confirmation(cid_old)["status"] == "failed"

        # Xác nhận mã v3 -> BỊ CHẶN
        res2 = await executor._handle_confirm(
            intent=IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=cid_future),
            context=user_ctx, chat_id=999, telegram_user_id=12345, remaining_time=10.0,
        )
        assert mock_gw.execute_action.call_count == 0
        assert conf_svc.get_confirmation(cid_future)["status"] == "failed"


@pytest.mark.asyncio
async def test_direct_email_without_draft_succeeds(remaining_fixes_env):
    """Email trực tiếp không liên kết draft (draft_id is None) vẫn gửi thành công."""
    env = remaining_fixes_env
    conf_svc: ConfirmationService = env["conf_svc"]
    user_ctx: GatewayUserContext = env["user_ctx"]
    executor = UnifiedExecutor()

    cid, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id=user_ctx.telegram_binding_id,
        action="gmail.send", arguments={"to": "direct@example.com", "subject": "Direct", "body": "Direct body"},
        preview_text="Gửi email trực tiếp", draft_id=None, draft_version=None,
    )

    mock_gw = AsyncMock()
    mock_gw.execute_action.return_value = {"status": "completed", "result": {"message_id": "msg_direct_999"}}
    with (
        patch("app.services.unified_executor.confirmation_service", conf_svc),
        patch("app.services.unified_executor.gateway_client", mock_gw),
    ):
        res = await executor._handle_confirm(
            intent=IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=cid),
            context=user_ctx, chat_id=999, telegram_user_id=12345, remaining_time=10.0,
        )

    assert mock_gw.execute_action.call_count == 1
    assert any("thành công" in r.lower() for r in res)
    assert conf_svc.get_confirmation(cid)["status"] == "completed"


# ==============================================================================
# 3. Compare-and-Swap nguyên tử với BEGIN IMMEDIATE (2 writer cạnh tranh v1)
# ==============================================================================
def test_atomic_cas_two_writers_race_on_version_1(remaining_fixes_env):
    """Hai thread cùng đọc v1 và cùng gọi set_active_draft kỳ vọng v1:
    Đúng 1 thread thành công ghi v2, 1 thread nhận conflict, kết quả cuối cùng là v2."""
    env = remaining_fixes_env
    hist_store: ConversationHistoryStore = env["hist_store"]
    user_ctx: GatewayUserContext = env["user_ctx"]

    # 1. Khởi tạo draft v1
    hist_store.set_active_draft(
        user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id,
        {"draft_id": "race_draft_1", "version": 1, "body": "Gốc v1"},
        database=user_ctx.database,
    )

    results = []
    barrier = threading.Barrier(2)

    def writer_task(writer_id: int, new_body: str):
        # Đợi cả 2 thread sẵn sàng để tăng tối đa tính cạnh tranh
        barrier.wait()
        res = hist_store.set_active_draft(
            user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id,
            {"draft_id": "race_draft_1", "body": new_body},
            database=user_ctx.database,
            expected_version=1,
            expected_draft_id="race_draft_1",
        )
        results.append((writer_id, res))

    t1 = threading.Thread(target=writer_task, args=(1, "Nội dung từ Writer 1"))
    t2 = threading.Thread(target=writer_task, args=(2, "Nội dung từ Writer 2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Kiểm tra: đúng 1 writer thành công, 1 writer thất bại (None)
    successes = [r for r in results if r[1] is not None]
    failures = [r for r in results if r[1] is None]

    assert len(successes) == 1, f"Kỳ vọng đúng 1 writer thành công, nhận: {len(successes)}"
    assert len(failures) == 1, f"Kỳ vọng đúng 1 writer nhận conflict, nhận: {len(failures)}"

    winning_writer_id, winning_draft = successes[0]
    assert winning_draft["version"] == 2

    # Kiểm tra trong DB: bản ghi cuối cùng phải là v2 của writer thắng cuộc
    sess = hist_store.get_session(user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id, database=user_ctx.database)
    active = sess["active_draft"]
    assert active["version"] == 2
    assert active["body"] == f"Nội dung từ Writer {winning_writer_id}"


def test_recovery_does_not_modify_terminal_statuses(remaining_fixes_env):
    """Recovery chạy nhiều lần không được thay đổi các trạng thái terminal (completed, failed, cancelled, expired)."""
    env = remaining_fixes_env
    conf_svc: ConfirmationService = env["conf_svc"]

    statuses = ["completed", "failed", "cancelled", "expired"]
    cids = []
    for st in statuses:
        cid, _ = conf_svc.create_confirmation(
            user_id=1, chat_id=999, telegram_binding_id="bind_fix_1",
            action="calendar.create", arguments={"st": st}, preview_text="",
        )
        with sqlite3.connect(conf_svc.db_path) as conn:
            conn.execute("UPDATE pending_confirmations SET status = ?, lease_expires_at = NULL WHERE confirmation_id = ?", (st, cid))
            conn.commit()
        cids.append((cid, st))

    # Chạy recovery 2 lần
    assert conf_svc.recover_expired_leases() == 0
    assert conf_svc.recover_expired_leases() == 0

    for cid, st in cids:
        c = conf_svc.get_confirmation(cid)
        assert c["status"] == st, f"Trạng thái {st} không được phép bị recovery thay đổi"


@pytest.mark.asyncio
async def test_draft_error_before_commit_preserves_old_draft_and_previews(remaining_fixes_env):
    """Khi lưu draft gặp lỗi trước commit: draft cũ và version cũ được giữ nguyên; preview không bị hủy."""
    env = remaining_fixes_env
    conf_svc: ConfirmationService = env["conf_svc"]
    hist_store: ConversationHistoryStore = env["hist_store"]
    user_ctx: GatewayUserContext = env["user_ctx"]
    executor = UnifiedExecutor()

    # 1. Khởi tạo draft v1 và preview v1
    hist_store.set_active_draft(
        user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id,
        {"draft_id": "draft_err_1", "version": 1, "body": "Bản nháp gốc v1"},
        database=user_ctx.database,
    )
    cid_prev, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id=user_ctx.telegram_binding_id,
        action="gmail.send", arguments={"to": "user@test.com", "body": "Bản nháp gốc v1"},
        preview_text="Preview v1", draft_id="draft_err_1", draft_version=1,
    )

    # 2. Giả lập lỗi xảy ra bên trong set_active_draft trước commit (ví dụ OperationalError giả lập lỗi disk/schema)
    with (
        patch("app.services.unified_executor.confirmation_service", conf_svc),
        patch("app.services.unified_executor.conversation_history_store", hist_store),
        patch.object(hist_store, "set_active_draft", side_effect=sqlite3.OperationalError("Simulated disk error")),
    ):
        try:
            await executor._handle_draft(
                intent=IntentAnalysisResult(
                    intent_type=IntentType.DRAFT,
                    draft={
                        "draft_id": "draft_err_1",
                        "draft_action": "update",
                        "content": "Nội dung lỗi",
                        "expected_version": 1,
                    },
                ),
                context=user_ctx,
                chat_id=999,
            )
        except Exception:
            pass

    # 3. Bản nháp trong session vẫn phải là v1
    sess = hist_store.get_session(user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id, database=user_ctx.database)
    assert sess["active_draft"]["version"] == 1
    assert sess["active_draft"]["body"] == "Bản nháp gốc v1"

    # 4. Preview liên kết với v1 KHÔNG ĐƯỢC bị hủy (vẫn giữ pending)
    c_prev = conf_svc.get_confirmation(cid_prev)
    assert c_prev["status"] == "pending"
