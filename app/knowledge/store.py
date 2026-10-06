import sqlite3
import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from app.services.file_service import file_service
from app.knowledge.models import KnowledgeSource, IndexingJob

class KnowledgeMetadataStore:
    def __init__(self):
        self.db_path = Path(file_service.workspace_root) / "knowledge.db"
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
                    error TEXT
                )
            """)
            conn.commit()

    def upsert_source(self, source: KnowledgeSource) -> None:
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
            conn.commit()

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

    def delete_source(self, source_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
            conn.commit()

    def list_sources(self, workspace_id: str) -> List[KnowledgeSource]:
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO indexing_jobs 
                (job_id, type, status, progress, created_at, finished_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                job.job_id, job.type, job.status, job.progress, job.created_at, job.finished_at, job.error
            ))
            conn.commit()
            
    def get_job(self, job_id: str) -> Optional[IndexingJob]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM indexing_jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if row:
                return IndexingJob(
                    job_id=row[0], type=row[1], status=row[2], progress=row[3], 
                    created_at=row[4], finished_at=row[5], error=row[6]
                )
        return None

metadata_store = KnowledgeMetadataStore()
