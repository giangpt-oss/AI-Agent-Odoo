import sqlite3
import json
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.providers.tasks.base import TaskProvider
from app.services.file_service import file_service
from pathlib import Path

class LocalTaskProvider(TaskProvider):
    def __init__(self):
        self.db_path = file_service.data_dir / "tasks.db"
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    due_at TEXT,
                    timezone TEXT,
                    project TEXT,
                    tags TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def _row_to_dict(self, row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "title": row[1],
            "description": row[2],
            "status": row[3],
            "priority": row[4],
            "due_at": row[5],
            "timezone": row[6],
            "project": row[7],
            "tags": json.loads(row[8]) if row[8] else [],
            "created_at": row[9],
            "updated_at": row[10]
        }

    async def create_task(self, title: str, description: str, priority: str, due_at: Optional[str], timezone: str, project: Optional[str], tags: List[str]) -> Dict[str, Any]:
        def _exec():
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            now = datetime.now().isoformat()
            tags_json = json.dumps(tags)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO tasks (id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, title, description, "TODO", priority, due_at, timezone, project, tags_json, now, now)
                )
                conn.commit()
            return self._get_task_sync(task_id)
        return await asyncio.to_thread(_exec)

    def _get_task_sync(self, task_id: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row)
            raise Exception(f"Task {task_id} not found")

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_task_sync, task_id)

    async def list_tasks(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        def _exec():
            query = "SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at FROM tasks WHERE 1=1"
            params = []
            if "status" in filters:
                query += " AND status = ?"
                params.append(filters["status"])
            if "priority" in filters:
                query += " AND priority = ?"
                params.append(filters["priority"])
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(query, params)
                return [self._row_to_dict(row) for row in cursor.fetchall()]
        return await asyncio.to_thread(_exec)

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        def _exec():
            task = self._get_task_sync(task_id)
            fields = []
            params = []
            for k, v in updates.items():
                if k in ["title", "description", "status", "priority", "due_at", "timezone", "project"]:
                    fields.append(f"{k} = ?")
                    params.append(v)
                elif k == "tags":
                    fields.append("tags = ?")
                    params.append(json.dumps(v))
            if fields:
                fields.append("updated_at = ?")
                params.append(datetime.now().isoformat())
                
                query = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?"
                params.append(task_id)
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(query, params)
                    conn.commit()
            return self._get_task_sync(task_id)
        return await asyncio.to_thread(_exec)

    async def complete_task(self, task_id: str) -> Dict[str, Any]:
        return await self.update_task(task_id, {"status": "DONE"})

    async def delete_task(self, task_id: str) -> bool:
        def _exec():
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.commit()
                return cursor.rowcount > 0
        return await asyncio.to_thread(_exec)
