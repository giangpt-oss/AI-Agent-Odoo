import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, UTC, timedelta

from app.domain.gateway_context import GatewayPermission, GatewayUserContext
from app.services.confirmation_service import ConfirmationService
from app.services.conversation_state import ConversationStateManager
from app.services.gateway_agent_dispatcher import GatewayAgentDispatcher
from app.services.turn_ledger import TurnLedger


@pytest.fixture
def temp_db_dir(tmp_path):
    return tmp_path


@pytest.fixture
def mock_context():
    return GatewayUserContext(
        user_id=1,
        odoo_uid=2,
        login="test@example.com",
        name="Test User",
        tenant="default_tenant",
        database="test_db",
        timezone="Asia/Ho_Chi_Minh",
        job_title="Kỹ sư phần mềm",
        department="Phòng Công nghệ",
        manager_name="Trưởng phòng",
        permissions={
            GatewayPermission.TASKS_READ,
            GatewayPermission.TASKS_WRITE,
            GatewayPermission.CALENDAR_READ,
            GatewayPermission.CALENDAR_WRITE,
            GatewayPermission.GMAIL_READ,
            GatewayPermission.GMAIL_SEND,
            GatewayPermission.CRM_READ,
            GatewayPermission.CRM_CREATE,
            GatewayPermission.CRM_WRITE,
        },
        telegram_binding_id="tg_bind_abc123",
    )


@pytest.mark.asyncio
async def test_task_create_preview_and_confirm(temp_db_dir, mock_context):
    conf_svc = ConfirmationService(db_path=temp_db_dir / "conf.db")
    dispatcher = GatewayAgentDispatcher()
    dispatcher.local_tasks.db_path = temp_db_dir / "tasks.db"
    dispatcher.local_tasks._init_db()

    with patch("app.services.gateway_agent_dispatcher.confirmation_service", conf_svc):
        # 1. Yêu cầu tạo task -> Trả preview + hướng dẫn /confirm
        msgs = await dispatcher.dispatch(
            context=mock_context,
            chat_id=1001,
            telegram_user_id=888,
            raw_text="/task create Báo cáo tuần | 2026-10-30 | high",
        )
        assert len(msgs) == 1
        assert "Bản xem trước tạo công việc cá nhân" in msgs[0]
        assert "/confirm c_" in msgs[0]

        # Trích xuất mã confirmation id
        import re
        m = re.search(r"/confirm\s+(c_[a-f0-9]+)", msgs[0])
        assert m is not None
        conf_id = m.group(1)

        # 2. Gửi lệnh /confirm <id>
        confirm_msgs = await dispatcher.dispatch(
            context=mock_context,
            chat_id=1001,
            telegram_user_id=888,
            raw_text=f"/confirm {conf_id}",
        )
        assert len(confirm_msgs) == 1
        assert "Đã tạo thành công công việc cá nhân" in confirm_msgs[0]

        # 3. Thử confirm lại lần 2 -> Phải bị từ chối (single-use)
        second_confirm = await dispatcher.dispatch(
            context=mock_context,
            chat_id=1001,
            telegram_user_id=888,
            raw_text=f"/confirm {conf_id}",
        )
        assert "Xác nhận không thành công" in second_confirm[0]
        assert "chỉ dùng 1 lần" in second_confirm[0]


@pytest.mark.asyncio
async def test_bare_agreement_rejected(mock_context):
    dispatcher = GatewayAgentDispatcher()
    for phrase in ["đồng ý", "xác nhận", "ok", "yes", "tiến hành đi"]:
        msgs = await dispatcher.dispatch(
            context=mock_context,
            chat_id=1001,
            telegram_user_id=888,
            raw_text=phrase,
        )
        assert "Vui lòng cung cấp mã xác nhận cụ thể" in msgs[0]


@pytest.mark.asyncio
async def test_task_delete_blocked(mock_context):
    dispatcher = GatewayAgentDispatcher()
    msgs = await dispatcher.dispatch(
        context=mock_context,
        chat_id=1001,
        telegram_user_id=888,
        raw_text="xóa việc task_123",
    )
    assert "chưa được hỗ trợ qua cổng Telegram này" in msgs[0]


@pytest.mark.asyncio
async def test_multi_turn_clarification(temp_db_dir, mock_context):
    conf_svc = ConfirmationService(db_path=temp_db_dir / "conf.db")
    state_mgr = ConversationStateManager()
    dispatcher = GatewayAgentDispatcher()

    with patch("app.services.gateway_agent_dispatcher.confirmation_service", conf_svc), \
         patch("app.services.gateway_agent_dispatcher.conversation_state_manager", state_mgr):
        # 1. Gửi lệnh tạo việc thiếu tiêu đề
        msgs1 = await dispatcher.dispatch(
            context=mock_context,
            chat_id=1001,
            telegram_user_id=888,
            raw_text="tạo công việc",
        )
        assert "Bạn muốn đặt tiêu đề cho công việc mới là gì?" in msgs1[0]

        # 2. Turn tiếp theo: người dùng cung cấp tiêu đề
        msgs2 = await dispatcher.dispatch(
            context=mock_context,
            chat_id=1001,
            telegram_user_id=888,
            raw_text="Hoàn thiện tài liệu nghiệm thu",
        )
        assert "Bản xem trước tạo công việc cá nhân" in msgs2[0]
        assert "Hoàn thiện tài liệu nghiệm thu" in msgs2[0]


@pytest.mark.asyncio
async def test_turn_ledger_idempotency(temp_db_dir):
    ledger = TurnLedger(db_path=temp_db_dir / "turn.db")
    req_id = "req_test_001"

    # Turn chưa có -> get_turn trả None
    assert ledger.get_turn(req_id) is None

    # Bắt đầu turn mới
    started = ledger.record_turn_start(req_id, user_id=1, telegram_user_id=888, chat_id=1001)
    assert started is True

    # Retry start cùng request_id -> False
    assert ledger.record_turn_start(req_id, user_id=1, telegram_user_id=888, chat_id=1001) is False

    # Hoàn thành turn
    ledger.record_turn_completed(req_id, ["Thông báo 1", "Thông báo 2"])

    # Khi Gateway retry cùng request_id -> lấy lại đúng messages cũ
    cached = ledger.get_turn(req_id)
    assert cached == ["Thông báo 1", "Thông báo 2"]
