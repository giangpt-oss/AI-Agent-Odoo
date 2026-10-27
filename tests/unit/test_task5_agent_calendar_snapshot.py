import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.domain.gateway_context import GatewayPermission, GatewayUserContext
from app.services.gateway_agent_dispatcher import GatewayAgentDispatcher
from app.services.confirmation_service import confirmation_service
from app.providers.tasks.local import LocalTaskProvider


@pytest.fixture
def agent_context():
    return GatewayUserContext(
        user_id=101,
        tenant="company_a",
        database="test_db",
        odoo_uid=202,
        login="testuser@example.com",
        name="Test User",
        timezone="Asia/Ho_Chi_Minh",
        permissions={
            GatewayPermission.TASKS_READ,
            GatewayPermission.TASKS_WRITE,
            GatewayPermission.CALENDAR_READ,
            GatewayPermission.CALENDAR_WRITE,
            GatewayPermission.GMAIL_READ,
            GatewayPermission.GMAIL_SEND,
            GatewayPermission.CRM_READ,
            GatewayPermission.CRM_WRITE,
        },
        telegram_binding_id="bind_999",
    )


@pytest.mark.asyncio
async def test_calendar_view_contract(agent_context):
    """Kiểm tra dispatcher hiển thị đúng contract lịch mới của gateway (events với start/end dạng chuỗi)."""
    dispatcher = GatewayAgentDispatcher()
    
    mock_events_resp = {
        "status": "OK",
        "events": [
            {
                "id": "evt_1",
                "summary": "Họp giao ban quý 4",
                "start": "2026-10-27T09:00:00+07:00",
                "end": "2026-10-27T10:00:00+07:00",
                "is_all_day": False,
            },
            {
                "id": "evt_2",
                "summary": "Nghỉ lễ",
                "start": "2026-10-28",
                "end": "2026-10-28",
                "is_all_day": True,
            }
        ]
    }
    
    with patch.object(dispatcher.calendar_provider, "get_events", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_events_resp
        res = await dispatcher._handle_calendar_view(agent_context)
        
        assert len(res) == 1
        output = res[0]
        assert "Họp giao ban quý 4" in output
        assert "2026-10-27T09:00:00+07:00" in output
        assert "Nghỉ lễ" in output
        assert "Cả ngày" in output or "2026-10-28" in output


@pytest.mark.asyncio
async def test_task_confirm_rejects_altered_snapshot(agent_context, tmp_path):
    """Kiểm tra confirm từ chối thực thi khi dữ liệu đích của task thay đổi sau preview (updated_at khác)."""
    db_file = tmp_path / "test_tasks.db"
    dispatcher = GatewayAgentDispatcher()
    dispatcher.local_tasks = LocalTaskProvider(db_path=db_file)
    
    with patch.object(confirmation_service, "db_path", db_file):
        confirmation_service._init_db()
        
        # 1. Tạo task ban đầu
        task = await dispatcher.local_tasks.create_task(
            title="Soạn hợp đồng",
            description="",
            priority="medium",
            due_at=None,
            timezone="Asia/Ho_Chi_Minh",
            project=None,
            tags=[],
            owner_key=agent_context.owner_key,
        )
        task_id = task["id"]
        original_snapshot = dict(task)
        
        # 2. Tạo preview update task với snapshot ban đầu
        conf_id, _ = confirmation_service.create_confirmation(
            user_id=agent_context.user_id,
            chat_id=12345,
            telegram_binding_id=agent_context.telegram_binding_id,
            action="tasks.update",
            arguments={"task_id": task_id, "updates": {"title": "Soạn hợp đồng v2"}},
            preview_text="Preview update",
            owner_key=agent_context.owner_key,
            target_snapshot=original_snapshot,
        )
        
        # 3. Giả lập một tiến trình khác thay đổi task (updated_at đổi)
        await dispatcher.local_tasks.update_task(
            task_id=task_id,
            updates={"title": "Soạn hợp đồng - người khác sửa"},
            owner_key=agent_context.owner_key,
        )
        
        # 4. Thực hiện confirm preview cũ
        res = await dispatcher._handle_confirm(
            context=agent_context,
            chat_id=12345,
            telegram_user_id=888,
            conf_id=conf_id,
        )
        
        assert len(res) == 1
        assert "Dữ liệu công việc đích đã bị thay đổi" in res[0]
        
        # Xác nhận status trong DB phải là failed
        with confirmation_service.get_connection() as conn:
            row = conn.execute("SELECT status FROM pending_confirmations WHERE confirmation_id = ?", (conf_id,)).fetchone()
            assert row[0] == "failed"


@pytest.mark.asyncio
async def test_prepare_calendar_update_fetches_real_snapshot_and_etag(agent_context):
    """Kiểm tra _prepare_calendar_update lấy dữ liệu thật từ gateway để lưu etag và snapshot trước-sau."""
    dispatcher = GatewayAgentDispatcher()
    
    mock_event = {
        "id": "evt_999",
        "summary": "Họp team cũ",
        "start": "2026-10-27T14:00:00+07:00",
        "end": "2026-10-27T15:00:00+07:00",
        "etag": '"etag_abc_123"',
    }
    
    with patch("app.services.gateway_agent_dispatcher.gateway_client.get_calendar_event", new_callable=AsyncMock) as mock_get_evt, \
         patch("app.services.gateway_agent_dispatcher.gateway_client.get_user_connections", new_callable=AsyncMock) as mock_get_conns:
        
        mock_get_evt.return_value = {"status": "OK", "event": mock_event}
        mock_get_conns.return_value = {"status": "OK", "google": {"email": "user@gmail.com"}}
        
        res = await dispatcher._prepare_calendar_update(
            context=agent_context,
            chat_id=12345,
            match_data={"event_id": "evt_999", "summary": "Họp team mới"},
        )
        
        assert len(res) == 1
        msg = res[0]
        assert "user@gmail.com" in msg
        assert "Họp team cũ" in msg
        assert "Họp team mới" in msg
        assert "/confirm" in msg


@pytest.mark.asyncio
async def test_prepare_crm_update_fetches_real_snapshot_and_write_date(agent_context):
    """Kiểm tra _prepare_crm_update lấy dữ liệu lead thật để lưu expected_write_date và snapshot."""
    dispatcher = GatewayAgentDispatcher()
    
    mock_lead = {
        "id": 42,
        "name": "Khách hàng VinFast",
        "stage_id": 1,
        "expected_revenue": 100000000.0,
        "write_date": "2026-10-25 10:00:00",
    }
    
    with patch("app.services.gateway_agent_dispatcher.gateway_client.get_crm_lead", new_callable=AsyncMock) as mock_get_lead:
        mock_get_lead.return_value = {"status": "OK", "lead": mock_lead}
        
        res = await dispatcher._prepare_crm_update(
            context=agent_context,
            chat_id=12345,
            match_data={"lead_id": 42, "stage_id": 2, "expected_revenue": 150000000.0},
        )
        
        assert len(res) == 1
        msg = res[0]
        assert "Khách hàng VinFast" in msg
        assert "100,000,000" in msg
        assert "150,000,000" in msg
        assert "/confirm" in msg


def test_format_action_result_conflict_and_permission_denied():
    """Kiểm tra formatter không báo thành công giả khi CONFLICT hoặc PERMISSION_DENIED."""
    dispatcher = GatewayAgentDispatcher()
    
    conflict_res = dispatcher._format_action_result("calendar.update", {"status": "CONFLICT", "message": "Etag mismatch"})
    assert any("thay đổi kể từ khi tạo bản xem trước" in s or "xung đột" in s for s in conflict_res)
    assert not any("thành công" in s for s in conflict_res)
    
    perm_res = dispatcher._format_action_result("gmail.send", {"status": "PERMISSION_DENIED", "message": "Missing gmail.send scope"})
    assert any("thiếu quyền" in s or "từ chối" in s for s in perm_res)
    assert not any("thành công" in s for s in perm_res)
