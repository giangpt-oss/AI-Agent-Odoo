import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_settings
from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult
from app.services.natural_pipeline import NaturalPipeline


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Tìm tệp Quy_che_cong_ty_hopita trên drive",
    "Cho tôi biết quy chế công ty Hopita trên drive",
    "Chuẩn bị bản nháp Gmail cho đối tác Trần Minh Trúc trong Odoo",
])
async def test_explicit_workspace_request_leaves_open_quotation(monkeypatch, text):
    monkeypatch.setattr(get_settings(), "ENABLE_QUOTATIONS", True)
    pipeline = NaturalPipeline()
    expected = IntentAnalysisResult(intent_type="read", tool_name="knowledge.query",
                                    parameters={"query": text})
    general = AsyncMock(return_value=expected)
    quote = AsyncMock(side_effect=AssertionError("New topic reached quotation parser"))
    monkeypatch.setattr(pipeline, "_dispatch_with_llm", general)
    monkeypatch.setattr(pipeline, "_dispatch_quote_turn", quote)
    session = {"active_draft": {"draft_type": "quotation", "draft_id": "saved-quote"}}
    context = GatewayUserContext(user_id=1)

    assert await pipeline.analyze_input(text, context, session) == expected
    general.assert_awaited_once()
    assert not general.await_args.args[2].get("active_draft")
    assert session["active_draft"]["draft_id"] == "saved-quote"


@pytest.mark.asyncio
async def test_workspace_command_uses_fast_path_with_open_quotation(monkeypatch):
    monkeypatch.setattr(get_settings(), "ENABLE_QUOTATIONS", True)
    pipeline = NaturalPipeline()
    dispatch = AsyncMock(side_effect=AssertionError("Slash command reached the model"))
    monkeypatch.setattr(pipeline, "_dispatch_with_llm", dispatch)
    session = {"active_draft": {"draft_type": "quotation", "draft_id": "saved-quote"}}

    result = await pipeline.analyze_input(
        "/workspace drive files.list", GatewayUserContext(user_id=1), session,
    )

    assert result.tool_name == "workspace.drive.read"
    assert result.parameters["method"] == "files.list"
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_groq_uses_requested_response_schema(monkeypatch):
    import openai
    from app.business.quote_schema import QuoteTurn
    from app.services import natural_pipeline

    settings = SimpleNamespace(
        DEFAULT_LLM_PROVIDER="groq", GROQ_API_KEY="synthetic",
        OPENAI_API_KEY="", GEMINI_API_KEY="", GROQ_BASE_URL="https://test.invalid",
        INTENT_LLM_MODEL="openai/gpt-oss-120b", DEFAULT_LLM_MODEL="",
    )
    monkeypatch.setattr(natural_pipeline, "get_settings", lambda: settings)
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [
        SimpleNamespace(message=SimpleNamespace(content='{"action":"other"}')),
    ]
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)
    schema = QuoteTurn.model_json_schema()
    result = await NaturalPipeline()._call_gemini_raw("Extract quotation", schema)

    assert json.loads(result) == {"action": "other"}
    prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert json.dumps(schema, ensure_ascii=False) in prompt
    assert "intent_type:" not in prompt
