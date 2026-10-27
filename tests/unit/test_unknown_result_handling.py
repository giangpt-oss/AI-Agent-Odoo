import pytest
import httpx
from unittest.mock import AsyncMock, patch
from app.services.confirmation_service import confirmation_service
from app.services.gateway_agent_dispatcher import gateway_agent_dispatcher
from app.domain.gateway_context import GatewayUserContext, GatewayPermission


@pytest.fixture
def clean_conf_env(tmp_path):
    db_file = tmp_path / "test_unknown_conf.db"
    old_path = confirmation_service.db_path
    confirmation_service.db_path = db_file
    confirmation_service._init_db()

    context = GatewayUserContext(
        user_id=1,
        odoo_uid=10,
        tenant="default",
        database="odoo_db",
        name="User A",
        login="user@example.com",
        telegram_binding_id="bind_1",
        permissions=[
            GatewayPermission.CALENDAR_READ,
            GatewayPermission.CALENDAR_WRITE,
            GatewayPermission.GMAIL_SEND,
            GatewayPermission.CRM_READ,
            GatewayPermission.CRM_WRITE,
        ],
    )

    yield context
    confirmation_service.db_path = old_path


@pytest.mark.asyncio
async def test_timeout_marks_unknown_result_and_blocks_reexecution(clean_conf_env):
    """Khi gọi Gateway bị timeout, confirmation phải được lưu trạng thái unknown_result.
    Lần confirm tiếp theo phải bị từ chối, không được tự ý thực thi lại."""
    context = clean_conf_env
    conf_id, _ = confirmation_service.create_confirmation(
        user_id=1,
        chat_id=123,
        telegram_binding_id="bind_1",
        action="calendar.create",
        arguments={"summary": "Cuộc họp quan trọng", "start": "2026-10-27T10:00:00Z", "end": "2026-10-27T11:00:00Z"},
        preview_text="Tạo lịch...",
    )

    with patch("app.services.gateway_client.gateway_client.execute_action", AsyncMock(return_value={"status": "unknown_result", "message": "Timeout"})):
        msgs = await gateway_agent_dispatcher._handle_confirm(context, 123, 123, conf_id)
        assert any("chưa rõ kết quả" in m or "đang được xử lý" in m for m in msgs)
        assert not any("gửi lại ngay" in m for m in msgs)

    # Kiểm tra trạng thái trong database
    conf = confirmation_service.get_confirmation(conf_id)
    assert conf["status"] == "unknown_result"

    # Người dùng thử gửi lại /confirm conf_id
    msgs2 = await gateway_agent_dispatcher._handle_confirm(context, 123, 123, conf_id)
    assert any("chưa rõ kết quả và cần được đối soát" in m for m in msgs2)


@pytest.mark.asyncio
async def test_network_exception_does_not_get_overwritten_to_failed(clean_conf_env):
    """Khi mất kết nối mạng sau khi gửi request, trạng thái unknown_result
    tuyệt đối không bị khối try/except ngoài ghi đè thành failed."""
    context = clean_conf_env
    conf_id, _ = confirmation_service.create_confirmation(
        user_id=1,
        chat_id=123,
        telegram_binding_id="bind_1",
        action="gmail.send",
        arguments={"to": "client@example.com", "subject": "Báo giá", "body_text": "Nội dung"},
        preview_text="Gửi thư...",
    )

    # Giả lập ném lỗi mạng httpx.NetworkError
    with patch("app.services.gateway_client.gateway_client.execute_action", AsyncMock(side_effect=httpx.NetworkError("Connection reset by peer"))):
        msgs = await gateway_agent_dispatcher._handle_confirm(context, 123, 123, conf_id)

    # Trạng thái trong database phải là unknown_result, KHÔNG ĐƯỢC là failed
    conf = confirmation_service.get_confirmation(conf_id)
    assert conf["status"] == "unknown_result"


@pytest.mark.asyncio
async def test_in_progress_marks_unknown_result(clean_conf_env):
    """Khi Gateway trả về IN_PROGRESS, confirmation phải được lưu trạng thái unknown_result."""
    context = clean_conf_env
    conf_id, _ = confirmation_service.create_confirmation(
        user_id=1,
        chat_id=123,
        telegram_binding_id="bind_1",
        action="crm.update",
        arguments={"lead_id": 99, "name": "Lead VIP"},
        preview_text="Cập nhật CRM...",
    )

    with patch("app.services.gateway_client.gateway_client.execute_action", AsyncMock(return_value={"status": "IN_PROGRESS", "message": "Đang xử lý"})):
        msgs = await gateway_agent_dispatcher._handle_confirm(context, 123, 123, conf_id)

    conf = confirmation_service.get_confirmation(conf_id)
    assert conf["status"] == "unknown_result"


@pytest.mark.asyncio
async def test_missing_operation_id_blocks_execution(clean_conf_env):
    """Khi bản ghi xem trước cũ bị thiếu operation_id, hệ thống phải chặn thực thi
    để không tự ý sinh operation_id mới gây side effect lặp."""
    context = clean_conf_env
    conf_id, _ = confirmation_service.create_confirmation(
        user_id=1,
        chat_id=123,
        telegram_binding_id="bind_1",
        action="calendar.create",
        arguments={"summary": "Cuộc họp", "start": "2026-10-27T10:00:00Z", "end": "2026-10-27T11:00:00Z"},
        preview_text="Tạo lịch...",
    )

    # Cố tình xóa operation_id khỏi database để giả lập dữ liệu cũ
    with confirmation_service.get_connection() as conn:
        conn.execute("UPDATE pending_confirmations SET operation_id = '' WHERE confirmation_id = ?", (conf_id,))
        conn.commit()

    with patch("app.services.gateway_client.gateway_client.execute_action") as mock_exec:
        msgs = await gateway_agent_dispatcher._handle_confirm(context, 123, 123, conf_id)
        # Không được gọi execute_action
        mock_exec.assert_not_called()
        assert any("thiếu mã định danh thao tác" in m for m in msgs)

    conf = confirmation_service.get_confirmation(conf_id)
    assert conf["status"] == "unknown_result"
