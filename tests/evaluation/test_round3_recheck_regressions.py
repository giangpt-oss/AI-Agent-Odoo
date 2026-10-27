# -*- coding: utf-8 -*-
"""Regression tests reproducing and verifying fixes for the 4 issues in docs/AGENT_RECHECK_2026-10-27_ROUND3.md:

1. [P1] Background reconciliation sends Gateway user ID instead of Telegram user ID
2. [P1] Worker with expired lease overwrites completed/terminal status
3. [P1] Task update/done overwrites task changes made after preview (revision conflict)
4. [P1] LLM draft editing changes draft ID, recipient, or subject outside user request
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
def round3_test_env(tmp_path):
    data_dir = tmp_path / "round3_data"
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
    task_provider = LocalTaskProvider(db_path=db_path)

    app = create_application(settings=settings)
    client = TestClient(app)

    user_ctx = GatewayUserContext(
        user_id=1,  # Gateway user ID = 1
        tenant="https://erp.test.local",
        database="db_test",
        odoo_uid=201,
        name="Nguyễn Văn Test",
        login="test@erp.local",
        timezone="Asia/Ho_Chi_Minh",
        telegram_user_id=12345,  # Telegram ID = 12345 (HOÀN TOÀN KHÁC 1)
        telegram_binding_id="bind_test_12345",
        permissions={
            "tasks.read",
            "tasks.write",
            "calendar.read",
            "calendar.write",
            "gmail.read",
            "gmail.send",
            "crm.read",
            "crm.create",
            "crm.write",
        },
    )

    with (
        patch("app.api.v1.internal.conversation_history_store", history_store),
        patch("app.services.unified_executor.conversation_history_store", history_store),
        patch("app.services.unified_executor.confirmation_service", conf_service),
        patch("app.services.confirmation_service.confirmation_service", conf_service),
    ):
        yield {
            "client": client,
            "settings": settings,
            "conf_service": conf_service,
            "task_provider": task_provider,
            "history_store": history_store,
            "user_ctx": user_ctx,
            "db_path": str(db_path),
        }


# ==============================================================================
# 1. Background reconciliation sends Telegram user ID, NOT Gateway user ID
# ==============================================================================
@pytest.mark.asyncio
async def test_background_reconciliation_sends_telegram_user_id(round3_test_env):
    """Tác vụ nền đối soát phải gửi telegram_user_id (12345), tuyệt đối không gửi Gateway user_id (1)."""
    env = round3_test_env
    conf_svc: ConfirmationService = env["conf_service"]
    user_ctx: GatewayUserContext = env["user_ctx"]

    # Tạo confirmation với Gateway action
    cid, _ = conf_svc.create_confirmation(
        user_id=user_ctx.user_id,  # 1
        chat_id=999,
        telegram_binding_id=user_ctx.telegram_binding_id,
        action="calendar.create",
        arguments={"summary": "Họp chiến lược"},
        preview_text="Preview lịch",
        owner_key="user_1",
        telegram_user_id=user_ctx.telegram_user_id,  # 12345
    )
    conf_svc.claim_and_validate(cid, user_ctx.user_id, 999, user_ctx.telegram_binding_id, "user_1")

    # Giả lập lease hết hạn
    with sqlite3.connect(conf_svc.db_path) as conn:
        past_ts = (datetime.now(UTC) - timedelta(seconds=60)).timestamp()
        conn.execute("UPDATE pending_confirmations SET lease_expires_at = ?, status = 'unknown_result' WHERE confirmation_id = ?", (past_ts, cid))
        conn.commit()

    mock_gw = AsyncMock()
    mock_gw.get_action_status.return_value = {"status": "completed", "result": {"id": "ev_123"}}

    updated = await reconcile_leases_with_gateway(gateway_client_instance=mock_gw)
    assert updated == 1
    assert mock_gw.get_action_status.called

    call_kwargs = mock_gw.get_action_status.call_args.kwargs
    # ĐIỀU KIỆN TIÊN QUYẾT: telegram_user_id gửi sang Gateway PHẢI LÀ 12345, KHÔNG ĐƯỢC LÀ 1
    assert call_kwargs["telegram_user_id"] == 12345
    assert call_kwargs["telegram_user_id"] != call_kwargs["user_id"]


# ==============================================================================
# 2. Worker với lease hết hạn hoặc token cũ KHÔNG ĐƯỢC ghi đè kết quả completed
# ==============================================================================
@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_completed_status(round3_test_env):
    """Lease hết hạn -> đối soát completed -> worker cũ mark_unknown/failed đều phải bị từ chối."""
    env = round3_test_env
    conf_svc: ConfirmationService = env["conf_service"]

    cid, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id="bind_1", action="calendar.create", arguments={}, preview_text=""
    )
    ok, _, claim = conf_svc.claim_and_validate(cid, 1, 999, "bind_1")
    assert ok is True
    stale_token = claim["execution_token"]

    # Giả lập lease hết hạn và hệ thống đối soát chuyển thành completed
    with sqlite3.connect(conf_svc.db_path) as conn:
        past_ts = (datetime.now(UTC) - timedelta(seconds=60)).timestamp()
        conn.execute(
            "UPDATE pending_confirmations SET lease_expires_at = ?, status = 'completed', result_json = '{\"ok\": true}' WHERE confirmation_id = ?",
            (past_ts, cid),
        )
        conn.commit()

    # Worker cũ trả muộn cố tình gọi mark_unknown
    res_unknown = conf_svc.mark_unknown(cid, "Worker timeout muộn", execution_token=stale_token)
    assert res_unknown is False, "mark_unknown của worker cũ không được phép thành công khi confirmation đã completed"

    # Worker cũ cố tình gọi mark_failed
    res_failed = conf_svc.mark_failed(cid, "Worker thất bại muộn", execution_token=stale_token)
    assert res_failed is False, "mark_failed của worker cũ không được phép ghi đè trạng thái completed"

    # Trạng thái trong DB phải giữ nguyên completed
    c_after = conf_svc.get_confirmation(cid)
    assert c_after["status"] == "completed"


def test_mark_dispatched_fails_if_claim_expired_or_invalid(round3_test_env):
    """mark_dispatched phải thất bại nếu lease đã hết hạn hoặc token sai/null."""
    env = round3_test_env
    conf_svc: ConfirmationService = env["conf_service"]

    cid, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id="bind_1", action="calendar.create", arguments={}, preview_text=""
    )
    ok, _, claim = conf_svc.claim_and_validate(cid, 1, 999, "bind_1")
    assert ok is True
    valid_token = claim["execution_token"]

    # 1. Token sai phải trả False
    assert conf_svc.mark_dispatched(cid, execution_token="wrong_token") is False

    # 2. Token NULL không được phép khớp bừa
    assert conf_svc.mark_dispatched(cid, execution_token=None) is False

    # 3. Lease hết hạn phải trả False
    with sqlite3.connect(conf_svc.db_path) as conn:
        past_ts = (datetime.now(UTC) - timedelta(seconds=60)).timestamp()
        conn.execute("UPDATE pending_confirmations SET lease_expires_at = ? WHERE confirmation_id = ?", (past_ts, cid))
        conn.commit()

    assert conf_svc.mark_dispatched(cid, execution_token=valid_token) is False


# ==============================================================================
# 3. Task conflict: Đổi title/due_at sau preview nhưng giữ status -> confirm bị chặn
# ==============================================================================
@pytest.mark.asyncio
async def test_task_update_conflict_blocks_confirm_when_changed_after_preview(round3_test_env):
    """Khi task bị thay đổi (title/due_at/revision) sau preview, confirm phải bị chặn kể cả khi status không đổi."""
    env = round3_test_env
    task_prov: LocalTaskProvider = env["task_provider"]
    conf_svc: ConfirmationService = env["conf_service"]
    db_path = env["db_path"]
    user_ctx = env["user_ctx"]
    owner_key = user_ctx.owner_key
    executor = UnifiedExecutor(local_tasks=task_prov)

    # 1. Tạo task ban đầu
    task = await task_prov.create_task(title="Initial Title", owner_key=owner_key)
    task_id = task["id"]
    initial_revision = task.get("revision", 1)

    # 2. Tạo preview update đổi title thành "Preview Title"
    res_prev = await executor._handle_prepare_write(
        intent=IntentAnalysisResult(
            intent_type=IntentType.PREPARE_WRITE,
            tool_name="tasks.personal.update",
            parameters={"task_id": task_id, "title": "Preview Title"},
        ),
        context=user_ctx,
        chat_id=999,
    )
    assert len(res_prev) == 1
    # Lấy confirmation ID vừa tạo
    confs = conf_svc.list_pending_confirmations(user_id=1, chat_id=999, telegram_binding_id=user_ctx.telegram_binding_id)
    cid = confs[0]["confirmation_id"]

    # 3. Bên ngoài cập nhật task: đổi title thành "Changed Outside", GIỮ NGUYÊN STATUS 'TODO'
    await task_prov.update_task(task_id, updates={"title": "Changed Outside"}, owner_key=owner_key)

    # 4. Confirm mã cũ cid
    ok_claim, _, claim = conf_svc.claim_and_validate(cid, 1, 999, user_ctx.telegram_binding_id, owner_key)
    assert ok_claim is True

    # Xác nhận thực thi phải bị chặn do conflict revision/snapshot
    with pytest.raises((ValueError, RuntimeError)) as exc_info:
        execute_task_confirmation_atomically(
            db_path=db_path,
            confirmation_id=cid,
            execution_token=claim["execution_token"],
            action="tasks.personal.update",
            arguments=claim["arguments"],
            owner_key=owner_key,
            target_snapshot=claim["target_snapshot"],
        )
    assert "thay đổi sau bản xem trước" in str(exc_info.value).lower() or "conflict" in str(exc_info.value).lower() or "revision" in str(exc_info.value).lower()

    # Task trong DB phải giữ nguyên "Changed Outside", KHÔNG bị đè thành "Preview Title"
    cur_task = await task_prov.get_task(task_id, owner_key=owner_key)
    assert cur_task["title"] == "Changed Outside"


@pytest.mark.asyncio
async def test_task_done_conflict_blocks_confirm_when_changed_after_preview(round3_test_env):
    """Khi task bị thay đổi sau preview done, confirm done cũng phải bị chặn do khác revision."""
    env = round3_test_env
    task_prov: LocalTaskProvider = env["task_provider"]
    conf_svc: ConfirmationService = env["conf_service"]
    db_path = env["db_path"]
    user_ctx = env["user_ctx"]
    owner_key = user_ctx.owner_key
    executor = UnifiedExecutor(local_tasks=task_prov)

    task = await task_prov.create_task(title="Task to be done", owner_key=owner_key)
    task_id = task["id"]

    # Preview done
    await executor._handle_prepare_write(
        intent=IntentAnalysisResult(
            intent_type=IntentType.PREPARE_WRITE,
            tool_name="tasks.personal.done",
            parameters={"task_id": task_id},
        ),
        context=user_ctx,
        chat_id=999,
    )
    confs = conf_svc.list_pending_confirmations(user_id=1, chat_id=999, telegram_binding_id=user_ctx.telegram_binding_id)
    cid = confs[0]["confirmation_id"]

    # Thay đổi task sau preview (ví dụ đổi title hoặc priority)
    await task_prov.update_task(task_id, updates={"priority": "high"}, owner_key=owner_key)

    # Confirm mã cũ
    ok_claim, _, claim = conf_svc.claim_and_validate(cid, 1, 999, user_ctx.telegram_binding_id, owner_key)
    assert ok_claim is True

    with pytest.raises((ValueError, RuntimeError)) as exc_info:
        execute_task_confirmation_atomically(
            db_path=db_path,
            confirmation_id=cid,
            execution_token=claim["execution_token"],
            action="tasks.personal.done",
            arguments=claim["arguments"],
            owner_key=owner_key,
            target_snapshot=claim["target_snapshot"],
        )
    assert "thay đổi sau bản xem trước" in str(exc_info.value).lower() or "revision" in str(exc_info.value).lower()


# ==============================================================================
# 4. LLM biên tập draft KHÔNG ĐƯỢC phép đổi draft ID, recipient, subject
# ==============================================================================
@pytest.mark.asyncio
async def test_draft_editing_protects_identity_and_recipient(round3_test_env):
    """Khi người dùng yêu cầu 'viết ngắn hơn', application phải cố định draft_id, recipient, subject từ draft gốc."""
    env = round3_test_env
    hist_store: ConversationHistoryStore = env["history_store"]
    user_ctx: GatewayUserContext = env["user_ctx"]

    # Lưu bản nháp gốc
    hist_store.set_active_draft(
        user_ctx.tenant,
        user_ctx.user_id,
        999,
        user_ctx.telegram_binding_id,
        {
            "draft_id": "draft_orig_fixed_123",
            "version": 1,
            "draft_type": "email",
            "recipient": "approved@company.com",
            "to": "approved@company.com",
            "subject": "Báo cáo tiến độ chuẩn",
            "content": "Kính gửi ban giám đốc nội dung báo cáo dài dòng chi tiết...",
        },
    )

    pipeline = NaturalPipeline()
    sess_ctx = hist_store.get_session(user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id)

    # Giả lập Fake LLM cố tình trả về draft_id, recipient, subject khác hoàn toàn
    malicious_model_response = json.dumps({
        "draft": {
            "draft_id": "model_injected_new_id_999",
            "version": 99,
            "recipient": "attacker@evil.com",
            "to": "another@evil.com",
            "subject": "Mô hình tự đổi tiêu đề",
            "content": "Nội dung đã được biên tập ngắn gọn.",
        }
    })

    with patch.object(pipeline, "_call_gemini_raw", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = malicious_model_response

        ana = await pipeline.analyze_input("viết ngắn hơn giúp tôi", user_ctx, session_context=sess_ctx)
        assert ana.intent_type == IntentType.DRAFT
        draft = ana.draft
        assert draft is not None

        # APPLICATION PHẢI BẢO VỆ CÁC TRƯỜNG:
        assert draft.draft_id == "draft_orig_fixed_123", "draft_id không được phép bị LLM thay đổi!"
        assert draft.recipient == "approved@company.com", "recipient không được phép bị LLM thay đổi!"
        assert draft.to == "approved@company.com", "to alias phải khớp chính xác recipient!"
        assert draft.subject == "Báo cáo tiến độ chuẩn", "subject không được phép bị LLM thay đổi!"
        assert draft.version == 2, "version phải do application tăng (+1), không nhận version 99 từ model!"
        assert draft.content == "Nội dung đã được biên tập ngắn gọn.", "chỉ nhận nội dung content từ LLM"


def test_concurrent_draft_modification_cas_rejected(round3_test_env):
    """Nếu draft bị thay đổi trong lúc LLM đang chạy (version lệch), kết quả cũ không được ghi đè."""
    env = round3_test_env
    hist_store: ConversationHistoryStore = env["history_store"]
    user_ctx = env["user_ctx"]

    # Draft ban đầu v1
    hist_store.set_active_draft(
        user_ctx.tenant,
        user_ctx.user_id,
        999,
        user_ctx.telegram_binding_id,
        {"draft_id": "draft_cas_1", "version": 1, "content": "v1"},
    )

    # Một tiến trình khác đã cập nhật draft lên v2
    hist_store.set_active_draft(
        user_ctx.tenant,
        user_ctx.user_id,
        999,
        user_ctx.telegram_binding_id,
        {"draft_id": "draft_cas_1", "version": 2, "content": "v2"},
    )

    # Worker cũ giữ expected_version = 1 cố tình ghi đè
    res = hist_store.set_active_draft(
        user_ctx.tenant,
        user_ctx.user_id,
        999,
        user_ctx.telegram_binding_id,
        {"draft_id": "draft_cas_1", "version": 2, "content": "stale content from old worker"},
        expected_version=1,
    )
    assert res is None, "Compare-and-swap phải từ chối khi expected_version = 1 nhưng DB đã lên v2"

    # Kiểm tra DB vẫn giữ nội dung v2
    curr = hist_store.get_active_draft(user_ctx.tenant, user_ctx.user_id, 999, user_ctx.telegram_binding_id)
    assert curr["content"] == "v2"


# ==============================================================================
# 5. Dữ liệu cũ: Confirmation thiếu Telegram ID không tự đoán; /status đối soát đúng
# ==============================================================================
@pytest.mark.asyncio
async def test_legacy_confirmation_missing_telegram_user_id_handled_safely(round3_test_env):
    """Confirmation cũ thiếu telegram_user_id: background bỏ qua; /status hợp lệ bổ sung mapping và đối soát."""
    env = round3_test_env
    conf_svc: ConfirmationService = env["conf_service"]
    user_ctx: GatewayUserContext = env["user_ctx"]
    executor = UnifiedExecutor()

    # Tạo confirmation giả lập dữ liệu cũ: telegram_user_id = NULL
    cid, _ = conf_svc.create_confirmation(
        user_id=user_ctx.user_id,  # 1
        chat_id=999,
        telegram_binding_id=user_ctx.telegram_binding_id,
        action="calendar.create",
        arguments={"summary": "Họp dữ liệu cũ"},
        preview_text="Preview cũ",
        owner_key=user_ctx.owner_key,
        telegram_user_id=None,
    )
    conf_svc.claim_and_validate(cid, user_ctx.user_id, 999, user_ctx.telegram_binding_id, user_ctx.owner_key)

    # Đưa về unknown_result
    with sqlite3.connect(conf_svc.db_path) as conn:
        past_ts = (datetime.now(UTC) - timedelta(seconds=60)).timestamp()
        conn.execute("UPDATE pending_confirmations SET lease_expires_at = ?, status = 'unknown_result', telegram_user_id = NULL WHERE confirmation_id = ?", (past_ts, cid))
        conn.commit()

    # Background reconciliation: KHÔNG ĐƯỢC TỰ ĐOÁN, không được gọi gateway với Gateway user_id
    mock_gw = AsyncMock()
    updated = await reconcile_leases_with_gateway(gateway_client_instance=mock_gw, confirmation_service_instance=conf_svc)
    assert updated == 0
    assert not mock_gw.get_action_status.called

    # Khi người dùng gửi /status hợp lệ:
    mock_gw.get_action_status.return_value = {"status": "completed", "result": {"id": "ev_legacy_999"}}
    with patch("app.services.unified_executor.gateway_client", mock_gw):
        res_status = await executor._handle_status(
            intent=IntentAnalysisResult(intent_type=IntentType.ACTION_STATUS, confirmation_id=cid),
            context=user_ctx,
            chat_id=999,
            telegram_user_id=user_ctx.telegram_user_id,
            remaining_time=10.0,
        )
    assert any("hoàn tất thành công" in s.lower() for s in res_status)
    # Kiểm tra mapping telegram_user_id đã được cập nhật
    c_after = conf_svc.get_confirmation(cid)
    assert c_after["telegram_user_id"] == 12345
    assert c_after["status"] == "completed"


# ==============================================================================
# 6. Cạnh tranh: Hai reconciler cùng cập nhật, terminal status không bị ghi đè
# ==============================================================================
def test_concurrent_reconciliation_terminal_status_protection(round3_test_env):
    """Hai reconciler cùng cập nhật; chỉ chuyển trạng thái hợp lệ, completed/failed không bị ghi đè."""
    env = round3_test_env
    conf_svc: ConfirmationService = env["conf_service"]

    cid, _ = conf_svc.create_confirmation(
        user_id=1, chat_id=999, telegram_binding_id="bind_1", action="calendar.create", arguments={}, preview_text=""
    )
    # Đưa về unknown_result
    with sqlite3.connect(conf_svc.db_path) as conn:
        conn.execute("UPDATE pending_confirmations SET status = 'unknown_result' WHERE confirmation_id = ?", (cid,))
        conn.commit()

    # Reconciler 1 nhận kết quả completed
    ok1 = conf_svc.reconcile_confirmation_result(cid, status="completed", result={"ok": True})
    assert ok1 is True

    # Reconciler 2 đến sau nhận kết quả failed -> PHẢI BỊ TỪ CHỐI
    ok2 = conf_svc.reconcile_confirmation_result(cid, status="failed", error_message="Lỗi đến muộn")
    assert ok2 is False, "Terminal status completed không được phép bị ghi đè bởi reconciler khác"

    c_final = conf_svc.get_confirmation(cid)
    assert c_final["status"] == "completed"


# ==============================================================================
# 7. Transaction: Lỗi lưu confirmation khiến mutation task rollback hoàn toàn
# ==============================================================================
def test_atomic_rollback_on_confirmation_update_failure(round3_test_env):
    """Nếu ghi confirmation thất bại (token sai/hết hạn), mutation task phải được rollback, không để dữ liệu nửa chừng."""
    env = round3_test_env
    conf_svc: ConfirmationService = env["conf_service"]
    task_prov: LocalTaskProvider = env["task_provider"]
    db_path = env["db_path"]
    user_ctx = env["user_ctx"]
    owner_key = user_ctx.owner_key

    # Tạo confirmation create task
    cid, _ = conf_svc.create_confirmation(
        user_id=1,
        chat_id=999,
        telegram_binding_id=user_ctx.telegram_binding_id,
        action="tasks.personal.create",
        arguments={"title": "Atomic Rollback Task"},
        preview_text="Preview",
        owner_key=owner_key,
    )
    ok_claim, _, claim = conf_svc.claim_and_validate(cid, 1, 999, user_ctx.telegram_binding_id, owner_key)
    assert ok_claim is True

    # Giả lập token sai khi gọi execute_task_confirmation_atomically
    with pytest.raises((PermissionError, RuntimeError)):
        execute_task_confirmation_atomically(
            db_path=db_path,
            confirmation_id=cid,
            execution_token="wrong_token_xyz",
            action="tasks.personal.create",
            arguments={"title": "Atomic Rollback Task"},
            owner_key=owner_key,
        )

    # Task KHÔNG ĐƯỢC phép tồn tại trong DB do transaction rollback
    tasks_in_db = sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM tasks WHERE title = 'Atomic Rollback Task'").fetchone()[0]
    assert tasks_in_db == 0, "Bản ghi task phải bị rollback hoàn toàn khi cập nhật confirmation thất bại"


# ==============================================================================
# 8. Preview cũ: Sửa draft rồi confirm mã cũ bị từ chối; preview mới chứa đúng dữ liệu
# ==============================================================================
@pytest.mark.asyncio
async def test_stale_draft_preview_confirm_rejected(round3_test_env):
    """Khi sửa draft sang version mới, mã confirm của preview cũ phải bị từ chối."""
    env = round3_test_env
    hist_store: ConversationHistoryStore = env["history_store"]
    conf_svc: ConfirmationService = env["conf_service"]
    executor = UnifiedExecutor()
    user_ctx = env["user_ctx"]

    # 1. Tạo draft v1
    hist_store.set_active_draft(
        user_ctx.tenant,
        user_ctx.user_id,
        999,
        user_ctx.telegram_binding_id,
        {"draft_id": "draft_v1_v2", "version": 1, "recipient": "user@test.com", "subject": "Test", "body": "Nội dung v1"},
    )

    # 2. Tạo preview gửi thư v1
    res_prev = await executor._handle_prepare_write(
        intent=IntentAnalysisResult(
            intent_type=IntentType.PREPARE_WRITE,
            tool_name="gmail.send",
            parameters={"to": "user@test.com", "subject": "Test", "body": "Nội dung v1", "draft_id": "draft_v1_v2", "draft_version": 1},
        ),
        context=user_ctx,
        chat_id=999,
    )
    confs = conf_svc.list_pending_confirmations(user_id=1, chat_id=999, telegram_binding_id=user_ctx.telegram_binding_id)
    cid_old = confs[0]["confirmation_id"]

    # 3. Người dùng sửa draft sang v2
    hist_store.set_active_draft(
        user_ctx.tenant,
        user_ctx.user_id,
        999,
        user_ctx.telegram_binding_id,
        {"draft_id": "draft_v1_v2", "version": 2, "recipient": "user@test.com", "subject": "Test", "body": "Nội dung v2 đã sửa"},
    )
    # Hủy preview cũ liên kết với draft_id
    conf_svc.cancel_pending_draft_confirmations(1, 999, user_ctx.telegram_binding_id, "draft_v1_v2")

    # 4. Cố tình confirm mã cũ cid_old -> PHẢI BỊ TỪ CHỐI
    res_confirm = await executor._handle_confirm(
        intent=IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=cid_old),
        context=user_ctx,
        chat_id=999,
        telegram_user_id=user_ctx.telegram_user_id,
        remaining_time=10.0,
    )
    assert any("đã bị hủy" in r.lower() or "phiên bản" in r.lower() or "vô hiệu hóa" in r.lower() for r in res_confirm)


# ==============================================================================
# 9. Migration: Chạy migration an toàn, task cũ có revision, giữ nguyên ID/payload
# ==============================================================================
def test_migration_idempotent_and_preserves_existing_data(round3_test_env):
    """Chạy migration nhiều lần; task cũ có revision=1, confirmations giữ nguyên ID và payload."""
    env = round3_test_env
    db_path = Path(env["db_path"])

    # Khởi tạo dữ liệu mẫu trước migration
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO tasks (id, title, status, priority, created_at, updated_at, owner_key) VALUES ('legacy_task_1', 'Legacy Task', 'TODO', 'medium', '2026-01-01', '2026-01-01', 'user_1')")
        conn.commit()

    # Chạy migration lần 1
    prov1 = LocalTaskProvider(db_path=db_path)
    svc1 = ConfirmationService(db_path=db_path)

    # Chạy migration lần 2 (idempotency check)
    prov2 = LocalTaskProvider(db_path=db_path)
    svc2 = ConfirmationService(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT id, title, revision FROM tasks WHERE id = 'legacy_task_1'")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "legacy_task_1"
        assert row[1] == "Legacy Task"
        assert row[2] == 1, "Task cũ phải có revision = 1 sau migration"
