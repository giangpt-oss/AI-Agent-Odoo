import sqlite3
import json
from typing import List, Optional
from pathlib import Path
from datetime import datetime

from app.memory.models import MemoryRecord, MemoryStatus
from app.providers.memory.base import MemoryStore
from app.services.file_service import file_service

class SQLiteMemoryStore(MemoryStore):
    def __init__(self):
        self.db_path = file_service.data_dir / "memory.db"
        self._init_db()
        
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    workspace_id TEXT,
                    project_id TEXT,
                    scope TEXT NOT NULL,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    summary TEXT,
                    source_type TEXT NOT NULL,
                    source_id TEXT,
                    confidence REAL,
                    privacy_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    previous_memory_id TEXT
                )
            """)
            conn.commit()

    def _row_to_model(self, row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row[0], user_id=row[1], workspace_id=row[2], project_id=row[3], scope=row[4],
            category=row[5], key=row[6], value=json.loads(row[7]), summary=row[8], source_type=row[9],
            source_id=row[10], confidence=row[11], privacy_level=row[12], status=row[13],
            created_at=row[14], updated_at=row[15], expires_at=row[16], previous_memory_id=row[17]
        )

    def create(self, memory: MemoryRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO memory_records 
                (memory_id, user_id, workspace_id, project_id, scope, category, key, value, summary, 
                source_type, source_id, confidence, privacy_level, status, created_at, updated_at, expires_at, previous_memory_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory.memory_id, memory.user_id, memory.workspace_id, memory.project_id, memory.scope,
                memory.category, memory.key, json.dumps(memory.value), memory.summary, memory.source_type,
                memory.source_id, memory.confidence, memory.privacy_level, memory.status,
                memory.created_at, memory.updated_at, memory.expires_at, memory.previous_memory_id
            ))
            conn.commit()

    def get(self, memory_id: str, user_id: str) -> Optional[MemoryRecord]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM memory_records WHERE memory_id = ? AND user_id = ?", (memory_id, user_id))
            row = cursor.fetchone()
            if row: return self._row_to_model(row)
        return None

    def get_by_key(self, key: str, user_id: str, workspace_id: Optional[str] = None) -> Optional[MemoryRecord]:
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT * FROM memory_records WHERE key = ? AND user_id = ? AND status = ?"
            params = [key, user_id, MemoryStatus.ACTIVE]
            if workspace_id:
                query += " AND (workspace_id = ? OR scope = 'GLOBAL')"
                params.append(workspace_id)
                
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            if row: return self._row_to_model(row)
        return None

    def search(self, query: str, user_id: str, workspace_id: Optional[str] = None) -> List[MemoryRecord]:
        with sqlite3.connect(self.db_path) as conn:
            sql = "SELECT * FROM memory_records WHERE user_id = ? AND status = ? AND (key LIKE ? OR summary LIKE ? OR category LIKE ?)"
            params = [user_id, MemoryStatus.ACTIVE, f"%{query}%", f"%{query}%", f"%{query}%"]
            if workspace_id:
                sql += " AND (workspace_id = ? OR scope = 'GLOBAL')"
                params.append(workspace_id)
            cursor = conn.execute(sql, params)
            return [self._row_to_model(row) for row in cursor.fetchall()]

    def list(self, user_id: str, workspace_id: Optional[str] = None) -> List[MemoryRecord]:
        with sqlite3.connect(self.db_path) as conn:
            sql = "SELECT * FROM memory_records WHERE user_id = ? AND status = ?"
            params = [user_id, MemoryStatus.ACTIVE]
            if workspace_id:
                sql += " AND (workspace_id = ? OR scope = 'GLOBAL')"
                params.append(workspace_id)
            cursor = conn.execute(sql, params)
            return [self._row_to_model(row) for row in cursor.fetchall()]

    def supersede(self, old_memory_id: str, new_memory: MemoryRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE memory_records SET status = ?, updated_at = ? WHERE memory_id = ?", 
                         (MemoryStatus.SUPERSEDED, datetime.now().isoformat(), old_memory_id))
            conn.execute("""
                INSERT INTO memory_records 
                (memory_id, user_id, workspace_id, project_id, scope, category, key, value, summary, 
                source_type, source_id, confidence, privacy_level, status, created_at, updated_at, expires_at, previous_memory_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_memory.memory_id, new_memory.user_id, new_memory.workspace_id, new_memory.project_id, new_memory.scope,
                new_memory.category, new_memory.key, json.dumps(new_memory.value), new_memory.summary, new_memory.source_type,
                new_memory.source_id, new_memory.confidence, new_memory.privacy_level, new_memory.status,
                new_memory.created_at, new_memory.updated_at, new_memory.expires_at, new_memory.previous_memory_id
            ))
            conn.commit()

    def delete(self, memory_id: str, user_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("UPDATE memory_records SET status = ?, updated_at = ? WHERE memory_id = ? AND user_id = ?", 
                         (MemoryStatus.DELETED, datetime.now().isoformat(), memory_id, user_id))
            conn.commit()
            return cursor.rowcount > 0

    def expire_old_memories(self) -> int:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("UPDATE memory_records SET status = ?, updated_at = ? WHERE status = ? AND expires_at IS NOT NULL AND expires_at < ?", 
                         (MemoryStatus.EXPIRED, now, MemoryStatus.ACTIVE, now))
            conn.commit()
            return cursor.rowcount

sqlite_memory_store = SQLiteMemoryStore()
