import base64
from unittest.mock import AsyncMock
from time import perf_counter

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.services.natural_pipeline import NaturalPipeline
from app.services.unified_executor import UnifiedExecutor


@pytest.mark.asyncio
async def test_normal_questions_use_drive_evidence_and_greetings_do_not(monkeypatch, caplog):
    from app.knowledge import drive, rag
    from app.services.turn_trace import request_id

    context = GatewayUserContext(user_id=1, tenant="example", database="test", odoo_uid=1,
        telegram_user_id=7, chat_id=7, telegram_binding_id="binding", permissions={"workspace.drive.read"})
    local_search = AsyncMock(side_effect=AssertionError("Local RAG was queried"))
    monkeypatch.setattr(rag.search_service, "search", local_search)
    download = AsyncMock(return_value={"status": "OK", "files": [{"id": "drive-file", "name": "Kho.txt",
        "suffix": ".txt", "content_base64": base64.b64encode("Tồn kho PN22 là 15 bộ.".encode()).decode()}]})
    monkeypatch.setattr(drive.gateway_client, "drive_knowledge", download)

    async def generate(question, evidence):
        assert [row.content for row in evidence] == ["Tồn kho PN22 là 15 bộ."]
        return "PN22 còn 15 bộ [Nguồn [1]: Kho.txt].", evidence

    monkeypatch.setattr(drive.rag_service, "answer_from_evidence", generate)
    pipeline = NaturalPipeline()
    executor = UnifiedExecutor()
    greeting = await pipeline.analyze_input("Hello!", context, {})
    assert "Xin chào" in (await executor.execute(greeting, context, 7, 7, {}, 9999999999, {}))[0]
    download.assert_not_awaited()

    started = perf_counter()
    intent = await pipeline.analyze_input("PN22 còn bao nhiêu bộ?", context, {})
    print(f"baseline-drive-evidence classification_ms={(perf_counter() - started) * 1000:.3f}")
    assert intent.tool_name == "knowledge.query"
    token = request_id.set("baseline-drive-evidence")
    try:
        with caplog.at_level("INFO", logger="app.services.turn_trace"):
            result = await executor.execute(intent, context, 7, 7, {}, 9999999999, {})
    finally:
        request_id.reset(token)
    assert "request_id=baseline-drive-evidence stage=internal_retrieval_ms" in caplog.text
    assert "request_id=baseline-drive-evidence stage=llm_synthesis_ms" in caplog.text
    for entry in caplog.records:
        if entry.name == "app.services.turn_trace":
            print(entry.getMessage())
    assert "https://drive.google.com/open?id=drive-file" in result[0]
    assert "pn22" in download.await_args.args[1]

    download.return_value = {"status": "OK", "files": []}
    result = await executor.execute(intent, context, 7, 7, {}, 9999999999, {})
    assert "Chưa tìm được tài liệu Drive" in result[0]
    local_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_direct_answer_is_routed_to_drive(monkeypatch):
    pipeline = NaturalPipeline()
    monkeypatch.setattr(pipeline, "_call_gemini_raw", AsyncMock(return_value=(
        '{"intent_type":"reply","reply_text":"Một câu trả lời chưa có nguồn."}'
    )))
    context = GatewayUserContext(user_id=1, tenant="example", database="test", odoo_uid=1,
        telegram_user_id=7, chat_id=7, telegram_binding_id="binding", permissions={"workspace.drive.read"})
    question = "Sản phẩm PN22 có xuất xứ ở đâu"
    intent = await pipeline.analyze_input(question, context, {})

    assert intent.tool_name == "knowledge.query"
    assert intent.parameters == {"query": question}
    assert intent.reply_text is None


def test_only_google_file_urls_supply_ids():
    from app.knowledge.drive import drive_reference

    assert drive_reference("https://docs.google.com/spreadsheets/d/file_123/edit") == ("file_123", True)
    assert drive_reference("https://drive.google.com/drive/folders/folder123") == (None, True)
    assert drive_reference("https://drive.google.com.invalid/file/d/file123") == (None, False)
