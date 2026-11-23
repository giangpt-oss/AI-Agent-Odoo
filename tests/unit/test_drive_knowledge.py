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


@pytest.mark.asyncio
async def test_information_question_queues_drive_with_original_question(monkeypatch):
    from app.core.config import get_settings
    from app.knowledge import drive
    from app.services.turn_trace import request_id

    context = GatewayUserContext(user_id=1, tenant="example", database="test", odoo_uid=1,
        telegram_user_id=7, chat_id=7, telegram_binding_id="binding", permissions={"workspace.drive.read"})
    monkeypatch.setattr(get_settings(), "WORKSPACE_BACKGROUND_READS", True)
    queue = AsyncMock(return_value={"status": "queued", "job_id": "drive-job-test"})
    monkeypatch.setattr(drive.gateway_client, "create_workspace_job", queue)
    monkeypatch.setattr(drive.gateway_client, "drive_knowledge", AsyncMock(side_effect=AssertionError("Synchronous Drive call")))
    question = "PN22 còn bao nhiêu bộ?"
    intent = await NaturalPipeline().analyze_input(question, context, {})
    token = request_id.set("drive-turn-test")
    try:
        messages = await UnifiedExecutor().execute(intent, context, 7, 7, {}, 9999999999, {})
    finally:
        request_id.reset(token)
    assert "drive-job-test" in messages[0]
    assert queue.await_args.args == (1, "drive-turn-test", "drive", "knowledge.search")
    assert queue.await_args.kwargs["body"] == {"question": question}
    assert queue.await_args.kwargs["deadline_seconds"] == 300
    assert queue.await_args.kwargs["wait_seconds"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected_text", [
    ("no_evidence", "Chưa đủ bằng chứng"),
    ("not_indexed", "Chưa có chỉ mục"),
    ("connection_error", "lỗi kết nối hoặc quyền truy cập"),
])
async def test_indexed_drive_reports_search_states_without_reading_raw_files(monkeypatch, status, expected_text):
    from app.core.config import get_settings
    from app.knowledge import drive

    context = GatewayUserContext(
        user_id=1, tenant="example", database="test", odoo_uid=1, telegram_user_id=7,
        chat_id=7, telegram_binding_id="binding", permissions={"workspace.drive.read"},
    )
    monkeypatch.setattr(get_settings(), "DRIVE_INDEX_ENABLED", True)
    monkeypatch.setattr(get_settings(), "WORKSPACE_BACKGROUND_READS", False)
    search = AsyncMock(return_value={"format": drive.INDEXED_DRIVE_FORMAT, "status": status, "partial": True})
    legacy_read = AsyncMock()
    synthesis = AsyncMock()
    monkeypatch.setattr(drive.gateway_client, "drive_search", search)
    monkeypatch.setattr(drive.gateway_client, "drive_knowledge", legacy_read)
    monkeypatch.setattr(drive.rag_service, "answer_from_evidence", synthesis)

    question = "PN22 còn bao nhiêu bộ?"
    intent = await NaturalPipeline().analyze_input(question, context, {})
    reply = await UnifiedExecutor().execute(intent, context, 7, 7, {}, 9999999999, {})
    assert expected_text in reply[0]
    if status == "no_evidence":
        assert drive.PARTIAL_COVERAGE_MESSAGE in reply[0]
    assert search.await_args.args[:3] == (context, question, None)
    assert "pn22" in search.await_args.args[3]
    legacy_read.assert_not_awaited()
    synthesis.assert_not_awaited()
