from unittest.mock import AsyncMock, patch

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.services.natural_pipeline import NaturalPipeline


@pytest.fixture
def mock_user_context():
    return GatewayUserContext(
        user_id=10,
        tenant="default",
        database="odoo_db",
        odoo_uid=20,
        telegram_user_id=12345,
        chat_id=12345,
        name="Nguyễn Văn A",
        login="a@example.com",
        timezone="Asia/Ho_Chi_Minh",
        permissions={
            "tasks.read", "tasks.write",
            "calendar.read", "calendar.write",
            "gmail.read", "gmail.send",
            "crm.read",
        },
        telegram_binding_id="bind_12345",
    )


@pytest.mark.asyncio
async def test_explanation_intent_does_not_trigger_write(mock_user_context):
    """Câu hỏi hướng dẫn được tra cứu trong Drive với quyền đọc."""
    pipeline = NaturalPipeline()
    explanation_queries = [
        "Cách tạo công việc?",
        "Làm thế nào để tạo task?",
        "Làm sao để tạo công việc cá nhân?",
        "Hướng dẫn tạo task",
        "Có nên tạo công việc không?",
    ]

    for q in explanation_queries:
        res = await pipeline.analyze_input(q, mock_user_context)
        assert res.intent_type == IntentType.READ, f"Query '{q}' bị gán nhầm thành intent {res.intent_type}"
        assert res.tool_name == "knowledge.query"
        assert res.parameters == {"query": q}


@pytest.mark.asyncio
async def test_negation_intent_does_not_trigger_write(mock_user_context):
    """Câu phủ định ('Đừng tạo công việc') phải trả lời REPLY từ chối, không được tạo task."""
    pipeline = NaturalPipeline()
    negation_queries = [
        "Đừng tạo công việc",
        "Tôi không muốn tạo task nữa",
        "Thôi không tạo công việc",
        "Đừng làm task đó",
    ]

    for q in negation_queries:
        res = await pipeline.analyze_input(q, mock_user_context)
        assert res.intent_type == IntentType.REPLY, f"Query '{q}' bị gán nhầm thành intent {res.intent_type}"
        assert res.intent_type != IntentType.PREPARE_WRITE
        assert res.tool_name != "tasks.personal.create"
        assert "không" in (res.reply_text or "").lower()


@pytest.mark.asyncio
async def test_action_intent_triggers_prepare_write(mock_user_context):
    """Yêu cầu hành động rõ ràng ('Tạo công việc báo cáo tuần') phải trả về PREPARE_WRITE có tiêu đề đúng."""
    pipeline = NaturalPipeline()
    res = await pipeline.analyze_input("Tạo công việc báo cáo tuần", mock_user_context)
    assert res.intent_type == IntentType.PREPARE_WRITE
    assert res.tool_name == "tasks.personal.create"
    assert res.parameters.get("title") == "báo cáo tuần"


@pytest.mark.asyncio
async def test_vague_input_triggers_clarify(mock_user_context):
    """Đầu vào mơ hồ ('Tạo công việc') thiếu tiêu đề phải hỏi lại (CLARIFY), không đoán lung tung."""
    pipeline = NaturalPipeline()
    res = await pipeline.analyze_input("Tạo công việc", mock_user_context)
    assert res.intent_type == IntentType.CLARIFY
    assert res.intent_type != IntentType.PREPARE_WRITE
    assert res.clarification_prompt is not None
    assert "tên" in res.clarification_prompt.lower()


@pytest.mark.asyncio
async def test_unsourced_follow_up_uses_knowledge_read_with_history(mock_user_context):
    """Câu hỏi không chỉ định nguồn dùng đường đọc thông tin mặc định."""
    pipeline = NaturalPipeline()
    sess_ctx = {
        "last_read": {
            "task_id": "task_1001",
            "title": "Báo cáo tài chính quý 3",
            "due_at": "2026-10-31T17:00:00",
        }
    }
    question = "Công việc vừa tạo có hạn khi nào?"
    res = await pipeline.analyze_input(question, mock_user_context, session_context=sess_ctx)
    assert res.intent_type == IntentType.READ
    assert res.tool_name == "knowledge.query"
    assert res.parameters == {"query": question}


@pytest.mark.asyncio
async def test_llm_json_dispatch_with_mock(mock_user_context):
    """Khi LLM mock trả về JSON phân tích ý định đọc Odoo đối tác."""
    pipeline = NaturalPipeline()
    mock_llm_json = (
        '{"intent_type": "read", "tool_name": "odoo.partners.search", '
        '"parameters": {"query": "An Phát", "limit": 5}}'
    )

    with patch.object(pipeline, "_call_gemini_raw", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_llm_json
        res = await pipeline.analyze_input("Tìm cho tôi khách hàng An Phát trên Odoo", mock_user_context)
        assert res.intent_type == IntentType.READ
        assert res.tool_name == "odoo.partners.search"
        assert res.parameters.get("query") == "An Phát"
        assert res.parameters.get("limit") == 5
