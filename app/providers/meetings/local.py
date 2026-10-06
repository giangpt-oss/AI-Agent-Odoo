import sqlite3
import json
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.providers.meetings.base import MeetingRepository
from app.services.file_service import file_service
from pathlib import Path

class LocalMeetingRepository(MeetingRepository):
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (Path(file_service.workspace_root) / "meetings.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    start_at TEXT,
                    end_at TEXT,
                    timezone TEXT,
                    participants TEXT,
                    agenda TEXT,
                    source_type TEXT,
                    source_ids TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def _row_to_dict(self, row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "title": row[1],
            "start_at": row[2],
            "end_at": row[3],
            "timezone": row[4],
            "participants": json.loads(row[5]) if row[5] else [],
            "agenda": json.loads(row[6]) if row[6] else [],
            "source_type": row[7],
            "source_ids": json.loads(row[8]) if row[8] else [],
            "status": row[9],
            "created_at": row[10],
            "updated_at": row[11]
        }

    async def create_meeting(self, title: str, start_at: Optional[str], end_at: Optional[str], timezone: str, participants: List[str], agenda: List[str], source_type: str, source_ids: List[str]) -> Dict[str, Any]:
        def _exec():
            meeting_id = f"mtg_{uuid.uuid4().hex[:8]}"
            now = datetime.now().isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO meetings (id, title, start_at, end_at, timezone, participants, agenda, source_type, source_ids, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (meeting_id, title, start_at, end_at, timezone, json.dumps(participants), json.dumps(agenda), source_type, json.dumps(source_ids), "PLANNED", now, now)
                )
                conn.commit()
            return self._get_meeting_sync(meeting_id)
        return await asyncio.to_thread(_exec)

    def _get_meeting_sync(self, meeting_id: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id, title, start_at, end_at, timezone, participants, agenda, source_type, source_ids, status, created_at, updated_at FROM meetings WHERE id = ?", (meeting_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row)
            raise Exception(f"Meeting {meeting_id} not found")

    async def get_meeting(self, meeting_id: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_meeting_sync, meeting_id)

    async def list_meetings(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        def _exec():
            query = "SELECT id, title, start_at, end_at, timezone, participants, agenda, source_type, source_ids, status, created_at, updated_at FROM meetings WHERE 1=1"
            params = []
            if "status" in filters:
                query += " AND status = ?"
                params.append(filters["status"])
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                results = [self._row_to_dict(row) for row in rows]
                
                # In-memory simple filter for participants and titles for MVP
                if "participant" in filters:
                    p = filters["participant"].lower()
                    results = [r for r in results if any(p in part.lower() for part in r["participants"])]
                if "title" in filters:
                    t = filters["title"].lower()
                    results = [r for r in results if t in r["title"].lower()]
                    
                return results
        return await asyncio.to_thread(_exec)

    async def update_meeting(self, meeting_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        def _exec():
            fields = []
            params = []
            for k, v in updates.items():
                if k in ["title", "start_at", "end_at", "timezone", "source_type", "status"]:
                    fields.append(f"{k} = ?")
                    params.append(v)
                elif k in ["participants", "agenda", "source_ids"]:
                    fields.append(f"{k} = ?")
                    params.append(json.dumps(v))
            if fields:
                fields.append("updated_at = ?")
                params.append(datetime.now().isoformat())
                
                query = f"UPDATE meetings SET {', '.join(fields)} WHERE id = ?"
                params.append(meeting_id)
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(query, params)
                    conn.commit()
            return self._get_meeting_sync(meeting_id)
        return await asyncio.to_thread(_exec)
