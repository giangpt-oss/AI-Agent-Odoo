from types import SimpleNamespace
from unittest.mock import AsyncMock

import docx
import pytest
from openpyxl import Workbook
from pypdf import PdfWriter

from app.domain.gateway_context import GatewayUserContext
from app.knowledge.indexing import ChunkingStrategy, indexing_service
from app.knowledge.models import IndexState, KnowledgeChunk, KnowledgeSource
from app.knowledge.rag import rag_service
from app.knowledge.retrieval import search_service
from app.knowledge.store import metadata_store
from app.providers.embeddings.gemini import default_embedding_provider
from app.providers.vectorstore.chroma import default_vector_store
from app.services.file_service import file_service


@pytest.mark.asyncio
async def test_real_dense_sparse_scope_and_generation_gate():
    sources = [KnowledgeSource(source_type="DOCUMENT", title=scope, workspace_id=scope, state=IndexState.INDEXED)
               for scope in ("audit-a", "audit-b", "default", "global")]
    chunks = [KnowledgeChunk(source_id=source.source_id, text=f"Đơn hàng HD-42 {source.title}") for source in sources]
    vectors = await default_embedding_provider.embed_batch([chunk.text for chunk in chunks])
    default_vector_store.upsert(chunks, vectors)
    for source, chunk in zip(sources, chunks, strict=True):
        metadata_store.upsert_source(source, [chunk])

    for mode in ("dense", "sparse", "hybrid"):
        result = await search_service.search("don hang HD-42", "audit-a", mode=mode)
        assert [row.chunk_id for row in result] == [chunks[0].chunk_id]
        for scope in (None, "", "*", "all", "default", "global", "audit-missing"):
            assert await search_service.search("HD-42", scope, mode=mode) == []

    def deny_generation(**kwargs):
        pytest.fail("Unauthorized corpus reached generation")

    client = SimpleNamespace(models=SimpleNamespace(generate_content=deny_generation))
    assert await rag_service.get_answer("HD-42", "audit-missing", client) == ("INSUFFICIENT_EVIDENCE", [])

    def inspect_context(**kwargs):
        context = kwargs["contents"]
        assert chunks[0].text in context
        assert all(chunk.text not in context for chunk in chunks[1:])
        return SimpleNamespace(text="Đơn hàng HD-42 [Nguồn [1]: audit-a]")

    client.models.generate_content = inspect_context
    answer, _ = await rag_service.get_answer("HD-42", "audit-a", client)
    assert "Nguồn [1]" in answer

    metadata_store.delete_source(sources[0].source_id)
    assert metadata_store.search_sparse("audit-a", "HD-42", 5) == []
    assert await search_service.search("HD-42", "audit-a") == []


@pytest.mark.asyncio
async def test_worker_rejects_other_workspace_file_before_embedding(tmp_path, monkeypatch):
    monkeypatch.setattr(file_service, "workspace_root", tmp_path)
    alice = GatewayUserContext(user_id=1, tenant="example", database="a", odoo_uid=1, telegram_binding_id="a")
    bob = GatewayUserContext(user_id=2, tenant="example", database="b", odoo_uid=1, telegram_binding_id="b")
    assert alice.knowledge_workspace != bob.knowledge_workspace
    path = tmp_path / "knowledge_docs" / bob.knowledge_workspace / "private.txt"
    path.parent.mkdir(parents=True)
    path.write_text("private fixture", encoding="utf-8")
    embed = AsyncMock(side_effect=AssertionError("Unauthorized ingestion reached provider"))
    monkeypatch.setattr(default_embedding_provider, "embed_batch", embed)
    assert not await indexing_service.index_file(str(path), alice.knowledge_workspace, "1")
    embed.assert_not_awaited()


def test_docx_keeps_heading_and_table_in_document_order(tmp_path):
    path = tmp_path / "policy.docx"
    document = docx.Document()
    document.add_heading("Quy chế", 1)
    document.add_paragraph("Trước bảng")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Mã HD-42"
    table.rows[0].cells[1].text = "24 tháng"
    document.add_paragraph("Sau bảng")
    document.save(path)
    chunks = ChunkingStrategy.chunk_docx(str(path), "source")
    assert chunks[0].section == "Quy chế"
    assert chunks[0].text.index("Trước bảng") < chunks[0].text.index("Mã HD-42") < chunks[0].text.index("Sau bảng")


def test_spreadsheet_packs_rows_with_sheet_and_header(tmp_path, monkeypatch):
    path = tmp_path / "stock.xlsx"
    workbook = Workbook()
    workbook.active.title = "Kho A"
    workbook.active.append(["Mã", "Số lượng"])
    for i in range(10):
        workbook.active.append([f"PN-{i:03}", i])
    workbook.save(path)
    monkeypatch.setattr("app.knowledge.indexing.get_settings", lambda: SimpleNamespace(RAG_CHUNK_CHARS=35))
    chunks = ChunkingStrategy.chunk_spreadsheet(str(path), "source")
    assert len(chunks) > 1
    assert all(chunk.sheet == "Kho A" and chunk.text.startswith("Mã,Số lượng\n") for chunk in chunks)
    assert chunks[0].section.startswith("Rows 2-")
    assert chunks[-1].section.endswith("-11")
    assert "PN-009,9" in chunks[-1].text


def test_pdf_without_text_does_not_create_evidence(tmp_path):
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(path)
    assert ChunkingStrategy.chunk_pdf(str(path), "source") == []


@pytest.mark.asyncio
async def test_provider_retry_respects_retry_after_and_does_not_retry_auth(monkeypatch):
    from unittest.mock import Mock

    import httpx
    from google.genai.errors import APIError

    from app.providers import gemini_retry

    sleep = AsyncMock()
    monkeypatch.setattr(gemini_retry.asyncio, "sleep", sleep)
    throttled = APIError(429, {}, httpx.Response(429, headers={"Retry-After": "2"}))
    call = Mock(side_effect=[throttled, "ok"])
    assert await gemini_retry.call_gemini("embedding", call) == "ok"
    sleep.assert_awaited_once_with(2)
    unauthorized = Mock(side_effect=APIError(403, {}))
    with pytest.raises(gemini_retry.GeminiRequestError, match="authentication"):
        await gemini_retry.call_gemini("generation", unauthorized)
    unauthorized.assert_called_once()


@pytest.mark.asyncio
async def test_generation_deadline_meets_provider_minimum(monkeypatch):
    from app.knowledge import rag
    from app.knowledge.models import SearchResult

    evidence = SearchResult(chunk_id="fixture", source_id="fixture", title="Fixture", content="Mã HD-42", score=0.01)
    monkeypatch.setattr(rag.search_service, "search", AsyncMock(return_value=[evidence]))
    monkeypatch.setattr(rag, "get_settings", lambda: SimpleNamespace(
        GEMINI_API_KEY="test-only", INTENT_LLM_MODEL="gemini-3-flash-preview"))

    def client(**kwargs):
        assert kwargs["http_options"].timeout >= 10000
        return SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_: SimpleNamespace(
            text="Mã HD-42 [Nguồn [1]: Fixture]")))

    monkeypatch.setattr(rag.genai, "Client", client)
    answer, _ = await rag.rag_service.get_answer("Mã hợp đồng?", "fixture-scope")
    assert "HD-42" in answer
