# -*- coding: utf-8 -*-
"""Unit tests for Knowledge RAG pipeline integration."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.domain.tool_registry import build_default_tool_registry
from app.services.natural_pipeline import natural_pipeline
from app.services.unified_executor import UnifiedExecutor
from app.knowledge.models import KnowledgeSource, IndexState


@pytest.fixture
def mock_context():
    return GatewayUserContext(
        user_id=1,
        chat_id=1001,
        telegram_binding_id="bind_1",
        tenant="default",
        database="test_db",
        odoo_uid=2,
        permissions={"knowledge.read", "tasks.read", "workspace.drive.read"},
    )


def test_tool_registry_has_knowledge_tools():
    registry = build_default_tool_registry()
    assert registry.get_tool("knowledge.query") is not None
    assert registry.get_tool("knowledge.documents.list") is not None
    assert registry.get_tool("knowledge.documents.index") is not None

    overview = registry.generate_capability_overview()
    assert "Kho Tri Thức" in overview


@pytest.mark.asyncio
async def test_natural_pipeline_fast_path_slash_knowledge(mock_context):
    res_list = await natural_pipeline.analyze_input("/knowledge list", mock_context)
    assert res_list.intent_type == IntentType.READ
    assert res_list.tool_name == "knowledge.documents.list"

    res_idx = await natural_pipeline.analyze_input("/knowledge index", mock_context)
    assert res_idx.intent_type == IntentType.PREPARE_WRITE
    assert res_idx.tool_name == "knowledge.documents.index"

    res_q = await natural_pipeline.analyze_input("/knowledge giờ làm việc thế nào", mock_context)
    assert res_q.intent_type == IntentType.READ
    assert res_q.tool_name == "knowledge.query"
    assert "giờ làm việc thế nào" in res_q.parameters.get("query", "")


@pytest.mark.asyncio
async def test_natural_pipeline_offline_semantic_dispatch(mock_context):
    with patch("app.services.natural_pipeline.get_settings") as mock_settings:
        mock_settings.return_value.ENABLE_QUOTATION_APPROVAL_FLOW = False
        mock_settings.return_value.ENABLE_REPORTING = False
        mock_settings.return_value.ENABLE_QUOTATIONS = False
        mock_settings.return_value.GEMINI_API_KEY = ""

        # Test semantic list
        res_list = await natural_pipeline.analyze_input("Cho tôi xem danh sách tài liệu trong kho tri thức", mock_context)
        assert res_list.tool_name == "knowledge.documents.list"

        # Test semantic index
        res_idx = await natural_pipeline.analyze_input("Lập chỉ mục tài liệu mới", mock_context)
        assert res_idx.tool_name == "knowledge.documents.index"

        # Test semantic policy query
        res_q = await natural_pipeline.analyze_input("Quy chế công ty về làm việc từ xa WFH quy định thế nào?", mock_context)
        assert res_q.tool_name == "knowledge.query"
        assert "WFH" in res_q.parameters.get("query", "")


@pytest.mark.asyncio
async def test_unified_executor_knowledge_list_empty(mock_context):
    executor = UnifiedExecutor()
    intent = IntentAnalysisResult(
        intent_type=IntentType.READ,
        tool_name="knowledge.documents.list",
        parameters={},
    )
    with patch("app.knowledge.store.metadata_store.list_sources", return_value=[]):
        res = await executor.execute(
            intent=intent,
            context=mock_context,
            chat_id=1001,
            telegram_user_id=1,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )
        assert len(res) == 1
        assert "Chưa có tài liệu nào" in res[0]


@pytest.mark.asyncio
async def test_unified_executor_knowledge_list_with_docs(mock_context):
    executor = UnifiedExecutor()
    intent = IntentAnalysisResult(
        intent_type=IntentType.READ,
        tool_name="knowledge.documents.list",
        parameters={},
    )
    fake_doc = KnowledgeSource(
        source_id="src_1",
        source_type="DOCUMENT",
        title="Quy_che_cong_ty_Hopita.txt",
        workspace_id="default",
        state=IndexState.INDEXED,
        indexed_at="2026-01-01T10:00:00",
    )
    with patch("app.knowledge.store.metadata_store.list_sources", return_value=[fake_doc]):
        res = await executor.execute(
            intent=intent,
            context=mock_context,
            chat_id=1001,
            telegram_user_id=1,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )
        assert len(res) == 1
        assert "Quy_che_cong_ty_Hopita.txt" in res[0]
        assert "INDEXED" in res[0] or "✅" in res[0]


@pytest.mark.asyncio
async def test_unified_executor_knowledge_query_success(mock_context):
    executor = UnifiedExecutor()
    intent = IntentAnalysisResult(
        intent_type=IntentType.READ,
        tool_name="knowledge.query",
        parameters={"query": "Giờ làm việc của công ty như thế nào?"},
    )
    with patch("app.knowledge.drive.answer_from_drive", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = ["Công ty làm việc từ 08:30 đến 17:30. Nguồn: Quy_che_cong_ty_Hopita.txt"]
        res = await executor.execute(
            intent=intent,
            context=mock_context,
            chat_id=1001,
            telegram_user_id=1,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )
        assert len(res) == 1
        assert "08:30 đến 17:30" in res[0]
        assert "Quy_che_cong_ty_Hopita.txt" in res[0]


@pytest.mark.asyncio
async def test_unified_executor_knowledge_query_insufficient_evidence(mock_context):
    executor = UnifiedExecutor()
    intent = IntentAnalysisResult(
        intent_type=IntentType.READ,
        tool_name="knowledge.query",
        parameters={"query": "Công ty có xe đưa đón không?"},
    )
    with patch("app.knowledge.drive.answer_from_drive", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = ["Không tìm thấy thông tin phù hợp trên Drive."]
        res = await executor.execute(
            intent=intent,
            context=mock_context,
            chat_id=1001,
            telegram_user_id=1,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )
        assert len(res) == 1
        assert "Không tìm thấy thông tin phù hợp" in res[0]


def test_index_requires_write_permission():
    tool = build_default_tool_registry().get_tool("knowledge.documents.index")
    assert tool.action_type == "write"
    assert tool.required_permission == "knowledge.write"


@pytest.mark.asyncio
async def test_indexing_job_reports_failed_files(monkeypatch):
    from app.knowledge.indexing import indexing_service
    from app.knowledge.models import IndexingJob
    from app.knowledge.store import metadata_store

    job = IndexingJob()
    metadata_store.update_job(job)
    monkeypatch.setattr(indexing_service, "index_file", AsyncMock(side_effect=[True, False]))
    await indexing_service.run_indexing_job(job.job_id, ["good.txt", "bad.txt"], "default", "1")
    stored = metadata_store.get_job(job.job_id)
    assert stored.status == "PARTIAL"
    assert stored.payload["indexed"] == ["good.txt"]
    assert stored.payload["failed"] == ["bad.txt"]


def test_chunking_preserves_long_paragraph():
    from app.knowledge.indexing import ChunkingStrategy
    from app.core.config import get_settings

    content = "x" * 9000 + "tail"
    chunks = ChunkingStrategy.chunk_text(content, "source")
    assert all(len(chunk.text) <= 4000 for chunk in chunks)
    overlap = get_settings().RAG_CHUNK_OVERLAP
    assert chunks[0].text + "".join(chunk.text[overlap:] for chunk in chunks[1:]) == content



@pytest.mark.asyncio
async def test_retrieval_filters_workspace_before_limit():
    from app.knowledge.models import KnowledgeChunk
    from app.knowledge.retrieval import search_service
    from app.knowledge.store import metadata_store
    from app.providers.embeddings.gemini import default_embedding_provider
    from app.providers.vectorstore.chroma import default_vector_store

    query = "unique policy token"
    own = KnowledgeSource(
        source_type="DOCUMENT", title="Own", workspace_id="scope_a", state=IndexState.INDEXED,
    )
    other = KnowledgeSource(
        source_type="DOCUMENT", title="Other", workspace_id="scope_b", state=IndexState.INDEXED,
    )
    metadata_store.upsert_source(own)
    metadata_store.upsert_source(other)
    chunks = [KnowledgeChunk(source_id=other.source_id, text=query) for _ in range(5)]
    chunks.append(KnowledgeChunk(source_id=own.source_id, text="unique policy text"))
    vectors = await default_embedding_provider.embed_batch([chunk.text for chunk in chunks])
    default_vector_store.upsert(chunks, vectors)

    results = await search_service.search(query, "scope_a", top_k=1)
    assert [item.source_id for item in results] == [own.source_id]


@pytest.mark.asyncio
async def test_chat_index_denied_without_write_permission(mock_context):
    intent = await natural_pipeline.analyze_input("/knowledge index", mock_context)
    result = await UnifiedExecutor().execute(
        intent, mock_context, 1001, 1, {}, 9999999999.0, {},
    )
    assert "knowledge.write" in result[0]



@pytest.mark.asyncio
async def test_chat_index_reports_actual_outcome(mock_context, monkeypatch, tmp_path):
    from app.knowledge.indexing import KnowledgeIndexingService
    from app.knowledge.retrieval import search_service
    from app.services.file_service import file_service

    worker = KnowledgeIndexingService()
    monkeypatch.setattr("app.knowledge.indexing.indexing_service", worker)
    monkeypatch.setattr(file_service, "workspace_root", tmp_path)
    mock_context.permissions.add("knowledge.write")
    mock_context.tenant = "scope_index"
    scope = mock_context.knowledge_workspace
    documents = tmp_path / "knowledge_docs" / scope
    documents.mkdir(parents=True)
    (documents / "policy.txt").write_text("Policy identifier HOPITA-42", encoding="utf-8")
    intent = await natural_pipeline.analyze_input(
        f"/knowledge index knowledge_docs/{scope}/policy.txt,knowledge_docs/{scope}/missing.txt", mock_context,
    )
    try:
        result = await UnifiedExecutor().execute(
            intent, mock_context, 1001, 1, {}, 9999999999.0, {},
        )
        assert "1/2" in result[0]
        assert "missing.txt" in result[0]
        matches = await search_service.search("HOPITA-42", scope)
        assert matches[0].title == "policy.txt"
    finally:
        worker.stop_worker()



def test_csv_chunking_indexes_rows_after_fifty(tmp_path):
    from app.knowledge.indexing import ChunkingStrategy

    path = tmp_path / "records.csv"
    path.write_text("record\n" + "\n".join(f"record-{i:03}" for i in range(60)), encoding="utf-8")
    chunks = ChunkingStrategy.chunk_spreadsheet(str(path), "source")
    assert any("record-059" in chunk.text and chunk.sheet == "CSV" for chunk in chunks)


@pytest.mark.asyncio
async def test_knowledge_status_only_visible_to_job_owner(mock_context):
    from app.knowledge.models import IndexingJob
    from app.knowledge.store import metadata_store

    mock_context.permissions.add("knowledge.write")
    job = IndexingJob(payload={"workspace_id": mock_context.knowledge_workspace, "owner_id": "99"})
    metadata_store.update_job(job)
    intent = await natural_pipeline.analyze_input(f"/knowledge status {job.job_id}", mock_context)
    executor = UnifiedExecutor()
    result = await executor.execute(intent, mock_context, 1001, 1, {}, 9999999999.0, {})
    assert job.job_id not in result[0]
    mock_context.user_id = 99
    result = await executor.execute(intent, mock_context, 1001, 99, {}, 9999999999.0, {})
    assert job.job_id in result[0]
