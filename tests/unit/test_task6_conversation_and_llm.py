import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.domain.gateway_context import GatewayPermission, GatewayUserContext
from app.services.gateway_agent_dispatcher import GatewayAgentDispatcher
from app.services.conversation_state import conversation_state_manager


@pytest.fixture
def agent_context():
    ctx = GatewayUserContext(
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
    conversation_state_manager.clear_state(ctx.user_id, ctx.telegram_binding_id)
    return ctx


@pytest.mark.asyncio
async def test_multi_turn_calendar_continuation(agent_context):
    """Kiểm tra luồng bổ sung thông tin Calendar đang dang dở được tiếp tục chính xác."""
    dispatcher = GatewayAgentDispatcher()
    
    # 1. Người dùng gõ tạo lịch thiếu thời gian
    res1 = await dispatcher.dispatch(
        context=agent_context,
        chat_id=12345,
        telegram_user_id=888,
        raw_text="/calendar create Họp chiến lược quý 4",
    )
    assert len(res1) == 1
    assert "Vui lòng cung cấp tiêu đề và thời gian" in res1[0]
    
    # Kiểm tra trạng thái đã được lưu
    state = conversation_state_manager.get_state(agent_context.user_id, agent_context.telegram_binding_id)
    assert state.get("pending_intent") == "create_calendar"
    
    # 2. Người dùng gửi bổ sung thời gian
    with patch("app.services.gateway_agent_dispatcher.gateway_client.get_user_connections", new_callable=AsyncMock) as mock_conns:
        mock_conns.return_value = {"status": "OK", "google": {"email": "user@gmail.com"}}
        res2 = await dispatcher.dispatch(
            context=agent_context,
            chat_id=12345,
            telegram_user_id=888,
            raw_text="2026-10-27T09:00:00 | 2026-10-27T10:00:00",
        )
        assert len(res2) == 1
        assert "Bản xem trước tạo sự kiện Lịch Google" in res2[0]
        assert "Họp chiến lược quý 4" in res2[0]
        assert "/confirm" in res2[0]


@pytest.mark.asyncio
async def test_context_reference_send_last_draft_email(agent_context):
    """Kiểm tra tham chiếu ngữ cảnh: 'gửi bản vừa soạn'."""
    dispatcher = GatewayAgentDispatcher()
    
    # Trường hợp 1: Chưa có bản thảo
    conversation_state_manager.clear_state(agent_context.user_id, agent_context.telegram_binding_id)
    res_none = await dispatcher.dispatch(
        context=agent_context,
        chat_id=12345,
        telegram_user_id=888,
        raw_text="gửi bản vừa soạn",
    )
    assert len(res_none) == 1
    assert "chưa có bản thảo" in res_none[0].lower() or "không tìm thấy bản thảo" in res_none[0].lower()
    
    # Trường hợp 2: Đã có bản thảo được lưu trong ngữ cảnh
    conversation_state_manager.update_state(
        agent_context.user_id,
        agent_context.telegram_binding_id,
        {
            "last_draft_email": {
                "to": "giamdoc@company.com",
                "subject": "Báo cáo doanh thu tháng 10",
                "body": "Kính gửi Giám đốc, doanh thu đạt 120% chỉ tiêu.",
            }
        },
    )
    with patch("app.services.gateway_agent_dispatcher.gateway_client.get_user_connections", new_callable=AsyncMock) as mock_conns:
        mock_conns.return_value = {"status": "OK", "google": {"email": "user@gmail.com"}}
        res_send = await dispatcher.dispatch(
            context=agent_context,
            chat_id=12345,
            telegram_user_id=888,
            raw_text="gửi bản vừa soạn",
        )
        assert len(res_send) == 1
        assert "Bản xem trước gửi thư Gmail" in res_send[0]
        assert "giamdoc@company.com" in res_send[0]
        assert "Báo cáo doanh thu tháng 10" in res_send[0]
        assert "/confirm" in res_send[0]


@pytest.mark.asyncio
async def test_context_reference_calendar_multiple_events_ask_choice(agent_context):
    """Khi có nhiều sự kiện lịch phù hợp, hệ thống hỏi người dùng chọn chứ không đoán."""
    dispatcher = GatewayAgentDispatcher()
    
    conversation_state_manager.update_state(
        agent_context.user_id,
        agent_context.telegram_binding_id,
        {
            "recent_calendar_events": [
                {"id": "evt_101", "summary": "Họp Marketing"},
                {"id": "evt_102", "summary": "Họp Ban Giám Đốc"},
            ]
        },
    )
    
    # Yêu cầu chung chung "sửa giờ cuộc họp đó"
    res = await dispatcher.dispatch(
        context=agent_context,
        chat_id=12345,
        telegram_user_id=888,
        raw_text="sửa giờ cuộc họp đó sang 15:00",
    )
    assert len(res) == 1
    assert "nhiều cuộc họp" in res[0].lower() or "chọn" in res[0].lower()
    assert "Họp Marketing" in res[0]
    assert "Họp Ban Giám Đốc" in res[0]


@pytest.mark.asyncio
async def test_llm_parser_natural_language_to_schema_action(agent_context):
    """Kiểm tra LLM phân tích ngôn ngữ tự nhiên thành action có schema, phải qua preview và confirm."""
    dispatcher = GatewayAgentDispatcher()
    
    mock_llm_result = {
        "action": "tasks.create",
        "arguments": {
            "title": "Gặp gỡ đối tác Viettel",
            "priority": "high",
            "due_at": "2026-10-28T09:00:00",
        },
    }
    
    with patch.object(dispatcher, "_call_llm_parse_action", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_llm_result
        
        res = await dispatcher.dispatch(
            context=agent_context,
            chat_id=12345,
            telegram_user_id=888,
            raw_text="Nhớ nhắc tôi đi gặp đối tác Viettel vào 9h sáng ngày 28/10 với mức độ quan trọng cao",
        )
        assert len(res) == 1
        assert "Bản xem trước tạo công việc cá nhân" in res[0]
        assert "Gặp gỡ đối tác Viettel" in res[0]
        assert "high" in res[0]
        assert "/confirm" in res[0]


@pytest.mark.asyncio
async def test_llm_unconfigured_fallback_keeps_slash_commands(agent_context):
    """Khi LLM không được cấu hình hoặc lỗi, slash command vẫn hoạt động, câu tự nhiên báo rõ."""
    dispatcher = GatewayAgentDispatcher()
    
    with patch.object(dispatcher, "_call_llm_parse_action", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = None  # Giả lập LLM không có API key hoặc lỗi
        
        # 1. Slash command vẫn hoạt động tốt
        res_slash = await dispatcher.dispatch(
            context=agent_context,
            chat_id=12345,
            telegram_user_id=888,
            raw_text="/task create Đi nộp báo cáo",
        )
        assert "Bản xem trước tạo công việc cá nhân" in res_slash[0]
        assert "/confirm" in res_slash[0]
        
        # 2. Câu ngôn ngữ tự nhiên không đoán dữ liệu, thông báo rõ ràng
        res_nl = await dispatcher.dispatch(
            context=agent_context,
            chat_id=12345,
            telegram_user_id=888,
            raw_text="Hôm nay trời đẹp quá bạn ơi",
        )
        assert len(res_nl) == 1
        assert "chưa hiểu" in res_nl[0].lower() or "chưa được hỗ trợ" in res_nl[0].lower() or "lệnh trực tiếp" in res_nl[0].lower()
