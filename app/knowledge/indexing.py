import os
import hashlib
import logging
from datetime import datetime
from typing import List, Optional
import asyncio

from app.knowledge.models import KnowledgeSource, KnowledgeChunk, SourceType, IndexState, IndexingJob
from app.knowledge.store import metadata_store
from app.providers.vectorstore.chroma import default_vector_store
from app.providers.embeddings.gemini import default_embedding_provider
from app.services.file_service import file_service

import pypdf
import docx
import pandas as pd

logger = logging.getLogger(__name__)

class ChunkingStrategy:
    MAX_TOKENS_ESTIMATE = 1000 # Roughly 4 chars per token, so ~4000 chars

    @staticmethod
    def chunk_text(text: str, source_id: str) -> List[KnowledgeChunk]:
        # Simple paragraph / overlap chunking
        # In a real app, use recursive character text splitter
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""
        pos = 0
        
        for p in paragraphs:
            if len(current_chunk) + len(p) > ChunkingStrategy.MAX_TOKENS_ESTIMATE * 3:
                if current_chunk:
                    chunks.append(KnowledgeChunk(source_id=source_id, text=current_chunk.strip(), position=pos))
                    pos += 1
                current_chunk = p + "\n\n"
            else:
                current_chunk += p + "\n\n"
                
        if current_chunk.strip():
            chunks.append(KnowledgeChunk(source_id=source_id, text=current_chunk.strip(), position=pos))
            
        return chunks

    @staticmethod
    def chunk_pdf(path: str, source_id: str) -> List[KnowledgeChunk]:
        chunks = []
        try:
            with open(path, "rb") as f:
                reader = pypdf.PdfReader(f)
                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text:
                        # Page boundary preferred
                        chunks.append(KnowledgeChunk(
                            source_id=source_id,
                            text=text.strip()[:4000], # basic limit per chunk
                            page=str(i+1),
                            position=i
                        ))
        except Exception as e:
            print(f"Error parsing PDF {path}: {e}")
        return chunks

    @staticmethod
    def chunk_docx(path: str, source_id: str) -> List[KnowledgeChunk]:
        chunks = []
        try:
            doc = docx.Document(path)
            full_text = "\n".join([para.text for para in doc.paragraphs])
            chunks = ChunkingStrategy.chunk_text(full_text, source_id)
        except Exception as e:
            print(f"Error parsing DOCX {path}: {e}")
        return chunks

    @staticmethod
    def chunk_spreadsheet(path: str, source_id: str) -> List[KnowledgeChunk]:
        chunks = []
        try:
            if path.lower().endswith(".csv"):
                df = pd.read_csv(path).head(50)
                return [KnowledgeChunk(source_id=source_id, text=df.to_csv(index=False)[:4000], sheet="CSV", position=0)]
            xl = pd.ExcelFile(path)
            for i, sheet_name in enumerate(xl.sheet_names):
                df = xl.parse(sheet_name).head(50) # Limit to top 50 rows for embedding
                text = f"Sheet: {sheet_name}\nData:\n{df.to_csv(index=False)}"
                chunks.append(KnowledgeChunk(
                    source_id=source_id,
                    text=text[:4000],
                    sheet=sheet_name,
                    position=i
                ))
        except Exception as e:
            print(f"Error parsing Spreadsheet {path}: {e}")
        return chunks

class KnowledgeIndexingService:
    
    def __init__(self):
        self._semaphore = asyncio.Semaphore(3)
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
        """Đọc từ queue và dispatch song song tối đa 3 tác vụ đồng thời thông qua Semaphore(3)."""
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
                # Dispatch job chạy nền song song (được giới hạn bởi Semaphore(3))
                asyncio.create_task(_dispatch_job(job_id, paths, workspace_id, owner_id))
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
        if not os.path.exists(safe_path):
            return False
            
        # Detect type
        ext = os.path.splitext(safe_path)[1].lower()
        if ext == ".pdf": src_type = SourceType.PDF
        elif ext in [".docx"]: src_type = SourceType.DOCX
        elif ext in [".xlsx", ".csv"]: src_type = SourceType.SPREADSHEET
        elif ext in [".txt", ".md"]: src_type = SourceType.DOCUMENT
        else:
            return False # Unsupported
            
        file_hash = await asyncio.to_thread(self._get_file_hash, safe_path)
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
        
        # Parse and Chunk in Worker Thread (Không block Event Loop)
        chunks = await asyncio.to_thread(self._sync_parse_and_chunk, safe_path, src_type, source.source_id)
            
        if not chunks:
            source.state = IndexState.FAILED
            await asyncio.to_thread(metadata_store.upsert_source, source)
            return False
            
        # Embed
        texts_to_embed = [c.text for c in chunks]
        embeddings = await default_embedding_provider.embed_batch(texts_to_embed)
        
        # Remove old chunks in Vector DB if re-indexing
        if existing:
            await asyncio.to_thread(default_vector_store.delete_source, source.source_id)
            
        # Upsert new chunks in thread
        await asyncio.to_thread(default_vector_store.upsert, chunks, embeddings)
        
        # Update metadata
        source.state = IndexState.INDEXED
        source.indexed_at = datetime.now().isoformat()
        source.updated_at = datetime.now().isoformat()
        await asyncio.to_thread(metadata_store.upsert_source, source)
        return True

    async def run_indexing_job(self, job_id: str, paths: List[str], workspace_id: str, owner_id: str):
        job = await asyncio.to_thread(metadata_store.get_job, job_id)
        if not job:
            return
        
        job.status = "RUNNING"
        await asyncio.to_thread(metadata_store.update_job, job)
        
        async with self._semaphore:
            success = 0
            try:
                for i, path in enumerate(paths):
                    try:
                        if await self.index_file(path, workspace_id, owner_id):
                            success += 1
                    except Exception as e:
                        print(f"Index error on {path}: {e}")
                    job.progress = int(((i + 1) / len(paths)) * 100)
                    await asyncio.to_thread(metadata_store.update_job, job)
                    
                job.status = "COMPLETED"
                job.finished_at = datetime.now().isoformat()
                await asyncio.to_thread(metadata_store.update_job, job)
            except Exception as e:
                job.status = "FAILED"
                job.error = str(e)
                job.finished_at = datetime.now().isoformat()
                await asyncio.to_thread(metadata_store.update_job, job)

indexing_service = KnowledgeIndexingService()
