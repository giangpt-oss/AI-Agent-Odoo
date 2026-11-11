import sqlite3
import json
import re
import unicodedata
from typing import List, Dict, Optional
from app.services.file_service import file_service
from app.knowledge.models import KnowledgeSource, KnowledgeChunk, IndexingJob


def sparse_terms(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    identifiers = re.findall(r"\b\w+(?:[-./_]\w+)+\b", folded)
    return re.findall(r"[^\W_]+", folded) + ["id" + value.encode().hex() for value in identifiers]

class KnowledgeMetadataStore:
    def __init__(self):
        self.db_path = file_service.data_dir / "knowledge.db"
        self._init_db()
        
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    path TEXT,
                    external_id TEXT,
                    workspace_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    mime_type TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    indexed_at TEXT,
                    version TEXT,
                    state TEXT NOT NULL,
                    hash TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indexing_jobs (
                    job_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT,
                    payload TEXT
                )
            """)
            job_cols = {r[1] for r in conn.execute("PRAGMA table_info(indexing_jobs)")}
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                chunk_id UNINDEXED, source_id UNINDEXED, document UNINDEXED,
                metadata UNINDEXED, terms, tokenize='unicode61')""")
            if "payload" not in job_cols:
                conn.execute("ALTER TABLE indexing_jobs ADD COLUMN payload TEXT")
            conn.commit()

    def upsert_source(self, source: KnowledgeSource, chunks: list[KnowledgeChunk] | None = None) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sources 
                (source_id, source_type, title, path, external_id, workspace_id, owner_id, mime_type, created_at, updated_at, indexed_at, version, state, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source.source_id, source.source_type, source.title, source.path, source.external_id, 
                source.workspace_id, source.owner_id, source.mime_type, source.created_at, 
                source.updated_at, source.indexed_at, source.version, source.state, source.hash
            ))
            if chunks is not None:
                conn.execute("DELETE FROM knowledge_fts WHERE source_id = ?", (source.source_id,))
                conn.executemany("INSERT INTO knowledge_fts VALUES (?, ?, ?, ?, ?)", [
                    (chunk.chunk_id, source.source_id, chunk.text,
                     json.dumps(chunk.model_dump(include={"source_id", "page", "section", "sheet", "slide"})),
                     " ".join(sparse_terms(chunk.text))) for chunk in chunks
                ])

    def search_sparse(self, workspace_id: str, query: str, limit: int) -> list[dict]:
        terms = list(dict.fromkeys(sparse_terms(query)))[:64]
        if not terms or not workspace_id or workspace_id.strip().lower() in {"", "*", "all", "default", "global"}:
            return []
        expression = " OR ".join(f'"{term}"' for term in terms)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""SELECT chunk_id, document, metadata, bm25(knowledge_fts)
                FROM knowledge_fts JOIN sources USING (source_id)
                WHERE knowledge_fts MATCH ? AND sources.workspace_id = ? AND sources.state = 'INDEXED'
                ORDER BY bm25(knowledge_fts), chunk_id LIMIT ?""", (expression, workspace_id, limit)).fetchall()
        return [{"chunk_id": row[0], "document": row[1], "metadata": json.loads(row[2]), "bm25": row[3]} for row in rows]

    def get_source_by_path(self, path: str, workspace_id: str) -> Optional[KnowledgeSource]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM sources WHERE path = ? AND workspace_id = ?", (path, workspace_id))
            row = cursor.fetchone()
            if row:
                return KnowledgeSource(
                    source_id=row[0], source_type=row[1], title=row[2], path=row[3], external_id=row[4],
                    workspace_id=row[5], owner_id=row[6], mime_type=row[7], created_at=row[8],
                    updated_at=row[9], indexed_at=row[10], version=row[11], state=row[12], hash=row[13]
                )
        return None
        
    def get_source(self, source_id: str) -> Optional[KnowledgeSource]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,))
            row = cursor.fetchone()
            if row:
                return KnowledgeSource(
                    source_id=row[0], source_type=row[1], title=row[2], path=row[3], external_id=row[4],
                    workspace_id=row[5], owner_id=row[6], mime_type=row[7], created_at=row[8],
                    updated_at=row[9], indexed_at=row[10], version=row[11], state=row[12], hash=row[13]
                )
        return None

    def get_sources_by_ids(self, source_ids: List[str]) -> Dict[str, KnowledgeSource]:
        """Khử N+1 query: Lấy danh sách nhiều sources cùng lúc trong 1 câu SQL duy nhất."""
        if not source_ids:
            return {}
        unique_ids = list(set(filter(None, source_ids)))
        if not unique_ids:
            return {}
        result = {}
        placeholders = ",".join("?" for _ in unique_ids)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"SELECT * FROM sources WHERE source_id IN ({placeholders})", tuple(unique_ids))
            for row in cursor.fetchall():
                src = KnowledgeSource(
                    source_id=row[0], source_type=row[1], title=row[2], path=row[3], external_id=row[4],
                    workspace_id=row[5], owner_id=row[6], mime_type=row[7], created_at=row[8],
                    updated_at=row[9], indexed_at=row[10], version=row[11], state=row[12], hash=row[13]
                )
                result[src.source_id] = src
        return result

    def delete_source(self, source_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM knowledge_fts WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
            conn.commit()

    def list_sources(self, workspace_id: Optional[str] = None) -> List[KnowledgeSource]:
        if not workspace_id or workspace_id.strip().lower() in {"", "*", "all", "default", "global"}:
            return []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM sources WHERE workspace_id = ?", (workspace_id,))
            rows = cursor.fetchall()
            return [
                KnowledgeSource(
                    source_id=r[0], source_type=r[1], title=r[2], path=r[3], external_id=r[4],
                    workspace_id=r[5], owner_id=r[6], mime_type=r[7], created_at=r[8],
                    updated_at=r[9], indexed_at=r[10], version=r[11], state=r[12], hash=r[13]
                ) for r in rows
            ]
            
    def update_job(self, job: IndexingJob) -> None:
        payload_str = json.dumps(job.payload) if job.payload else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO indexing_jobs 
                (job_id, type, status, progress, created_at, finished_at, error, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.job_id, job.type, job.status, job.progress, job.created_at, job.finished_at, job.error, payload_str
            ))
            conn.commit()
            
    def get_job(self, job_id: str) -> Optional[IndexingJob]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT job_id, type, status, progress, created_at, finished_at, error, payload FROM indexing_jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if row:
                payload = json.loads(row[7]) if row[7] else None
                return IndexingJob(
                    job_id=row[0], type=row[1], status=row[2], progress=row[3], 
                    created_at=row[4], finished_at=row[5], error=row[6], payload=payload
                )
        return None

    def get_pending_or_interrupted_jobs(self) -> List[IndexingJob]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT job_id, type, status, progress, created_at, finished_at, error, payload "
                "FROM indexing_jobs WHERE status IN ('PENDING', 'RUNNING') ORDER BY created_at ASC"
            )
            rows = cursor.fetchall()
            jobs = []
            for row in rows:
                payload = json.loads(row[7]) if row[7] else None
                jobs.append(IndexingJob(
                    job_id=row[0], type=row[1], status=row[2], progress=row[3], 
                    created_at=row[4], finished_at=row[5], error=row[6], payload=payload
                ))
            return jobs

metadata_store = KnowledgeMetadataStore()
