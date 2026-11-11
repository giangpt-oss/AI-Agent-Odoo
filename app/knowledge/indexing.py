import os
import hashlib
import logging
import re
from pathlib import Path
import csv
import io
from datetime import datetime
from typing import List, Optional
import asyncio

from app.knowledge.models import KnowledgeSource, KnowledgeChunk, SourceType, IndexState
from app.knowledge.store import metadata_store
from app.providers.vectorstore.chroma import default_vector_store
from app.providers.embeddings.gemini import default_embedding_provider
from app.services.file_service import file_service
from app.core.config import get_settings

import pypdf
import docx
import pandas as pd

logger = logging.getLogger(__name__)

class ChunkingStrategy:
    @classmethod
    def chunk_text(cls, text: str, source_id: str) -> List[KnowledgeChunk]:
        chunks = []
        settings = get_settings()
        size, overlap = settings.RAG_CHUNK_CHARS, settings.RAG_CHUNK_OVERLAP
        if size < 2 or not 0 <= overlap < size // 2:
            raise ValueError("Invalid RAG chunk size or overlap")
        remaining = text.strip()
        while remaining:
            end = len(remaining)
            if end > size:
                end = size
                for separator in ("\n\n", "\n", " "):
                    boundary = remaining.rfind(separator, size // 2, end)
                    if boundary >= 0:
                        end = boundary + len(separator)
                        break
            chunks.append(KnowledgeChunk(
                source_id=source_id, text=remaining[:end].strip(), position=len(chunks),
            ))
            if end == len(remaining):
                break
            remaining = remaining[end - overlap:].lstrip()
        return chunks

    @classmethod
    def chunk_pdf(cls, path: str, source_id: str) -> List[KnowledgeChunk]:
        chunks = []
        empty_pages = []
        with open(path, "rb") as file:
            for page_number, page in enumerate(pypdf.PdfReader(file).pages, 1):
                page_text = page.extract_text() or ""
                if not page_text.strip():
                    empty_pages.append(page_number)
                for chunk in cls.chunk_text(page_text, source_id):
                    chunk.page = str(page_number)
                    chunk.position = len(chunks)
                    chunks.append(chunk)
        if empty_pages:
            logger.warning("rag_pdf_empty_pages count=%d", len(empty_pages))
            for chunk in chunks:
                chunk.metadata["pages_without_text"] = empty_pages
        return chunks

    @classmethod
    def chunk_docx(cls, path: str, source_id: str) -> List[KnowledgeChunk]:
        document = docx.Document(path)
        chunks, blocks = [], []
        heading = None

        def flush():
            for chunk in cls.chunk_text("\n\n".join(blocks), source_id):
                chunk.section = heading
                chunk.position = len(chunks)
                chunks.append(chunk)
            blocks.clear()

        for block in document.iter_inner_content():
            if isinstance(block, docx.text.paragraph.Paragraph):
                if block.style and block.style.name.startswith(("Heading", "Title")):
                    flush()
                    heading = block.text
                blocks.append(block.text)
            else:
                blocks.append("\n".join(" | ".join(cell.text for cell in row.cells) for row in block.rows))
        flush()
        return chunks

    @classmethod
    def chunk_spreadsheet(cls, path: str, source_id: str) -> List[KnowledgeChunk]:
        chunks = []

        def add_sheet(sheet_name, frame):
            def csv_line(values):
                buffer = io.StringIO()
                csv.writer(buffer, lineterminator="\n").writerow(values)
                return buffer.getvalue()

            header = csv_line(frame.columns)
            limit = get_settings().RAG_CHUNK_CHARS
            rows = []
            length = len(header)
            start = 2

            def flush(end):
                if rows:
                    chunks.append(KnowledgeChunk(source_id=source_id, text=header + "".join(rows),
                        sheet=sheet_name, section=f"Rows {start}-{end}", position=len(chunks)))

            for number, record in enumerate(frame.itertuples(index=False, name=None), 2):
                row = csv_line(record)
                if len(header) + len(row) > limit:
                    raise ValueError("Spreadsheet record exceeds configured chunk size")
                if length + len(row) > limit:
                    flush(number - 1)
                    rows.clear()
                    start, length = number, len(header)
                rows.append(row)
                length += len(row)
            flush(len(frame) + 1)

        if path.lower().endswith(".csv"):
            add_sheet("CSV", pd.read_csv(path, dtype=str, keep_default_na=False, skip_blank_lines=False))
        else:
            with pd.ExcelFile(path) as workbook:
                for sheet_name in workbook.sheet_names:
                    add_sheet(sheet_name, workbook.parse(sheet_name, dtype=str, keep_default_na=False))
        return chunks

class KnowledgeIndexingService:
    
    def __init__(self):
        self._job_lock = asyncio.Lock()
        self._queue: Optional[asyncio.Queue] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._enqueued_jobs: set[str] = set()
        self._is_running = False

    async def start_worker(self):
        """Khởi động worker ngay lúc server/bot boot để phục hồi các job bị gián đoạn từ SQLite."""
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker_task is None or self._worker_task.done():
            self._is_running = True
            # Phục hồi các jobs bị gián đoạn (PENDING / RUNNING) từ SQLite
            try:
                pending_jobs = await asyncio.to_thread(metadata_store.get_pending_or_interrupted_jobs)
                for j in pending_jobs:
                    if j.job_id not in self._enqueued_jobs and j.payload and "paths" in j.payload:
                        self._enqueued_jobs.add(j.job_id)
                        logger.info(f"Phục hồi indexing job chưa hoàn thành từ SQLite vào queue: {j.job_id}")
                        await self._queue.put((
                            j.job_id,
                            j.payload["paths"],
                            j.payload.get("workspace_id", "default"),
                            j.payload.get("owner_id", "system")
                        ))
            except Exception as e:
                logger.error(f"Lỗi khi khôi phục indexing jobs từ SQLite: {e}")

            self._worker_task = asyncio.create_task(self._queue_worker_loop())

    def stop_worker(self):
        """Dừng worker gracefully khi tắt ứng dụng."""
        self._is_running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()

    async def _queue_worker_loop(self):
        """Chạy lần lượt các job để tránh hai job thay thế cùng một chỉ mục."""
        async def _dispatch_job(job_id: str, paths: List[str], workspace_id: str, owner_id: str):
            try:
                await self.run_indexing_job(job_id, paths, workspace_id, owner_id)
            except Exception as e:
                logger.error(f"Lỗi khi chạy indexing job {job_id}: {e}")
            finally:
                self._enqueued_jobs.discard(job_id)
                if self._queue:
                    self._queue.task_done()

        while self._is_running:
            try:
                job_id, paths, workspace_id, owner_id = await self._queue.get()
                await _dispatch_job(job_id, paths, workspace_id, owner_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Lỗi trong queue worker loop: {e}")

    async def enqueue_indexing_job(self, job_id: str, paths: List[str], workspace_id: str, owner_id: str):
        """Đưa job mới vào hàng đợi, tránh chạy lặp và tự động khởi động worker nếu chưa chạy."""
        await self.start_worker()
        if job_id not in self._enqueued_jobs:
            self._enqueued_jobs.add(job_id)
            await self._queue.put((job_id, paths, workspace_id, owner_id))

    def _get_file_hash(self, path: str) -> str:
        h = hashlib.md5()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    def _sync_parse_and_chunk(self, safe_path: str, src_type: SourceType, source_id: str) -> List[KnowledgeChunk]:
        """Thực thi parsing và chunking trong Worker Thread riêng biệt để không block Event Loop."""
        if src_type == SourceType.PDF:
            return ChunkingStrategy.chunk_pdf(safe_path, source_id)
        elif src_type == SourceType.DOCX:
            return ChunkingStrategy.chunk_docx(safe_path, source_id)
        elif src_type == SourceType.SPREADSHEET:
            return ChunkingStrategy.chunk_spreadsheet(safe_path, source_id)
        else:
            with open(safe_path, "r", encoding="utf-8") as f:
                text = f.read()
            return ChunkingStrategy.chunk_text(text, source_id)

    async def index_file(self, path: str, workspace_id: str, owner_id: str) -> bool:
        safe_path = file_service.get_safe_path(path)
        if not re.fullmatch(r"kb_[0-9a-f]{64}", workspace_id or ""):
            return False
        root = file_service.workspace_root.resolve() / "knowledge_docs" / workspace_id
        if not Path(safe_path).is_relative_to(root):
            return False
        if not os.path.exists(safe_path):
            return False
            
        # Detect type
        ext = os.path.splitext(safe_path)[1].lower()
        src_type = {".pdf": SourceType.PDF, ".docx": SourceType.DOCX,
                    ".xlsx": SourceType.SPREADSHEET, ".csv": SourceType.SPREADSHEET,
                    ".txt": SourceType.DOCUMENT, ".md": SourceType.DOCUMENT}.get(ext)
        if src_type is None:
            return False
            
        file_hash = await asyncio.to_thread(self._get_file_hash, safe_path)
        if not file_hash:
            return False
        settings = get_settings()
        file_hash = f"chunks-v3:{settings.RAG_CHUNK_CHARS}:{settings.RAG_CHUNK_OVERLAP}:{file_hash}"
        title = os.path.basename(safe_path)
        
        # Check if exists and unchanged
        existing = await asyncio.to_thread(metadata_store.get_source_by_path, path, workspace_id)
        if existing:
            if existing.hash == file_hash and existing.state == IndexState.INDEXED:
                # Unchanged
                return True
            else:
                existing.state = IndexState.INDEXING
                existing.version = str(int(existing.version) + 1)
                source = existing
        else:
            source = KnowledgeSource(
                source_type=src_type,
                title=title,
                path=path,
                workspace_id=workspace_id,
                owner_id=owner_id,
                hash=file_hash,
                state=IndexState.INDEXING
            )
            
        await asyncio.to_thread(metadata_store.upsert_source, source)
        
        try:
            chunks = await asyncio.to_thread(
                self._sync_parse_and_chunk, safe_path, src_type, source.source_id,
            )
            if not chunks:
                source.state = IndexState.FAILED
                await asyncio.to_thread(metadata_store.upsert_source, source)
                return False
            for chunk in chunks:
                identity = f"{source.source_id}\0{chunk.position}\0{chunk.text}"
                chunk.chunk_id = "chk_" + hashlib.sha256(identity.encode()).hexdigest()

            embeddings = []
            for start in range(0, len(chunks), 32):
                batch = chunks[start:start + 32]
                embeddings.extend(await default_embedding_provider.embed_batch([c.text for c in batch]))
            if len(embeddings) != len(chunks):
                raise ValueError("Embedding count does not match chunk count")

            # Failed or interrupted replacements remain hidden until a complete retry.
            await asyncio.to_thread(default_vector_store.delete_source, source.source_id)
            await asyncio.to_thread(default_vector_store.upsert, chunks, embeddings)
        except Exception:
            source.state = IndexState.FAILED
            await asyncio.to_thread(metadata_store.upsert_source, source)
            raise

        source.hash = file_hash
        source.state = IndexState.INDEXED
        source.indexed_at = datetime.now().isoformat()
        source.updated_at = source.indexed_at
        await asyncio.to_thread(metadata_store.upsert_source, source, chunks)
        return True

    async def run_indexing_job(self, job_id: str, paths: List[str], workspace_id: str, owner_id: str):
        job = await asyncio.to_thread(metadata_store.get_job, job_id)
        if not job:
            return
        
        job.status = "RUNNING"
        await asyncio.to_thread(metadata_store.update_job, job)
        
        async with self._job_lock:
            indexed = []
            failed = []
            try:
                for i, path in enumerate(paths):
                    try:
                        succeeded = await self.index_file(path, workspace_id, owner_id)
                    except Exception as exc:
                        logger.warning("rag_index_file_failed job=%s category=%s", job_id, type(exc).__name__)
                        succeeded = False
                    (indexed if succeeded else failed).append(path)
                    job.progress = int((i + 1) / len(paths) * 100)
                    await asyncio.to_thread(metadata_store.update_job, job)

                job.payload = {**(job.payload or {}), "indexed": indexed, "failed": failed}
                if not indexed:
                    job.status = "FAILED"
                elif failed:
                    job.status = "PARTIAL"
                else:
                    job.status = "COMPLETED"
            except Exception as exc:
                job.status = "FAILED"
                job.error = type(exc).__name__
            job.finished_at = datetime.now().isoformat()
            await asyncio.to_thread(metadata_store.update_job, job)

indexing_service = KnowledgeIndexingService()
