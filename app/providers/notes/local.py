import sqlite3
import json
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.providers.notes.base import NoteRepository
from app.services.file_service import file_service
from pathlib import Path

class LocalNoteRepository(NoteRepository):
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (file_service.data_dir / "notes.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    note_type TEXT NOT NULL,
                    tags TEXT,
                    meeting_id TEXT,
                    project TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def _row_to_dict(self, row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "title": row[1],
            "content": row[2],
            "note_type": row[3],
            "tags": json.loads(row[4]) if row[4] else [],
            "meeting_id": row[5],
            "project": row[6],
            "created_at": row[7],
            "updated_at": row[8]
        }

    async def create_note(self, title: str, content: str, note_type: str, tags: List[str], meeting_id: Optional[str], project: Optional[str]) -> Dict[str, Any]:
        def _exec():
            note_id = f"note_{uuid.uuid4().hex[:8]}"
            now = datetime.now().isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO notes (id, title, content, note_type, tags, meeting_id, project, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (note_id, title, content, note_type, json.dumps(tags), meeting_id, project, now, now)
                )
                conn.commit()
            return self._get_note_sync(note_id)
        return await asyncio.to_thread(_exec)

    def _get_note_sync(self, note_id: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id, title, content, note_type, tags, meeting_id, project, created_at, updated_at FROM notes WHERE id = ?", (note_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row)
            raise Exception(f"Note {note_id} not found")

    async def get_note(self, note_id: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_note_sync, note_id)

    async def list_notes(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        def _exec():
            query = "SELECT id, title, content, note_type, tags, meeting_id, project, created_at, updated_at FROM notes WHERE 1=1"
            params = []
            
            if "note_type" in filters:
                query += " AND note_type = ?"
                params.append(filters["note_type"])
            if "meeting_id" in filters:
                query += " AND meeting_id = ?"
                params.append(filters["meeting_id"])
            if "project" in filters:
                query += " AND project = ?"
                params.append(filters["project"])
                
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                results = [self._row_to_dict(row) for row in rows]
                
                # In-memory keyword filtering
                if "keyword" in filters:
                    kw = filters["keyword"].lower()
                    results = [r for r in results if kw in r["title"].lower() or kw in r["content"].lower()]
                
                return results
        return await asyncio.to_thread(_exec)

    async def update_note(self, note_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        def _exec():
            fields = []
            params = []
            for k, v in updates.items():
                if k in ["title", "content", "note_type", "meeting_id", "project"]:
                    fields.append(f"{k} = ?")
                    params.append(v)
                elif k == "tags":
                    fields.append("tags = ?")
                    params.append(json.dumps(v))
            if fields:
                fields.append("updated_at = ?")
                params.append(datetime.now().isoformat())
                
                query = f"UPDATE notes SET {', '.join(fields)} WHERE id = ?"
                params.append(note_id)
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(query, params)
                    conn.commit()
            return self._get_note_sync(note_id)
        return await asyncio.to_thread(_exec)
