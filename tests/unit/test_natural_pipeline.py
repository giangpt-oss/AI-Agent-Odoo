import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.services.natural_pipeline import NaturalPipeline


@pytest.fixture
def mock_context():
    return GatewayUserContext(
        user_id=1,
        tenant="https://odoo.local",
        database="odoo_db",
        odoo_uid=10,
        telegram_user_id=777,
        chat_id=777,
        name="Lê Văn C",
        login="c.le@mycompany.com",
        timezone="Asia/Bangkok",
        permissions={"tasks.read", "calendar.events", "gmail.readonly", "crm.read"},
    )


@pytest.mark.asyncio
async def test_fast_path_greetings(mock_context):
    pipeline = NaturalPipeline()
    for text in ["hello", "hi", "xin chào", "chào bạn", "Chào bot"]:
        res = await pipeline.analyze_input(text, mock_context)
        assert res.intent_type == IntentType.REPLY
        assert "Xin chào" in (res.reply_text or "")


@pytest.mark.asyncio
async def test_fast_path_capabilities(mock_context):
    pipeline = NaturalPipeline()
    queries = [
        "bạn có thể làm gì",
        "bạn giúp tôi được việc gì",
        "hướng dẫn tôi dùng bot",
        "hướng dẫn sử dụng",
        "chức năng của bot",
        "menu tính năng",
    ]
    for text in queries:
        res = await pipeline.analyze_input(text, mock_context)
        assert res.intent_type == IntentType.REPLY
        assert "Điểm tin hàng ngày" in (res.reply_text or "")
        assert "Google Calendar" in (res.reply_text or "")


@pytest.mark.asyncio
async def test_fast_path_slash_commands(mock_context):
    pipeline = NaturalPipeline()
    res_today = await pipeline.analyze_input("/today", mock_context)
    assert res_today.intent_type == IntentType.READ
    assert res_today.tool_name == "briefing.today"

    res_tasks = await pipeline.analyze_input("/task list", mock_context)
    assert res_tasks.intent_type == IntentType.READ
    assert res_tasks.tool_name == "tasks.personal.list"


@pytest.mark.asyncio
async def test_llm_dispatch_valid_read(mock_context):
    pipeline = NaturalPipeline()
    mock_llm_response = (
        '{"intent_type": "read", "tool_name": "calendar.events.list", "parameters": {"time_min": "tomorrow"}}'
    )

    with patch.object(pipeline, "_call_gemini_raw", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_llm_response
        res = await pipeline.analyze_input("Mai tôi có lịch gì không nhỉ?", mock_context)
        assert res.intent_type == IntentType.READ
        assert res.tool_name == "calendar.events.list"


@pytest.mark.asyncio
async def test_llm_dispatch_invalid_json_fallback(mock_context):
    pipeline = NaturalPipeline()
    # LLM trả về văn bản không phải JSON hoặc sai schema
    with patch.object(pipeline, "_call_gemini_raw", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = "Xin lỗi tôi không hiểu cấu trúc này."
        res = await pipeline.analyze_input("Một câu hỏi bất kỳ", mock_context)
        assert res.intent_type == IntentType.UNSUPPORTED
        assert "không thể xử lý" in (res.reply_text or "").lower() or "thử lại" in (res.reply_text or "").lower()
