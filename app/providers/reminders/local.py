import asyncio
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime
from app.providers.reminders.base import ReminderProvider
from app.services.file_service import file_service

class LocalReminderProvider(ReminderProvider):
    def __init__(self, db_path=None, user_id=None, chat_id=None):
        self.db_path = db_path or (file_service.data_dir / "reminders.db")
        self.user_id = str(user_id) if user_id is not None else None
        self.chat_id = chat_id
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY, task_id TEXT, title TEXT NOT NULL, remind_at TEXT NOT NULL,
                timezone TEXT NOT NULL, recurrence TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL)""")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(reminders)")}
            for name, definition in {"user_id": "TEXT", "chat_id": "INTEGER", "attempts": "INTEGER NOT NULL DEFAULT 0"}.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE reminders ADD COLUMN {name} {definition}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_status_remind ON reminders(status, remind_at)")

    def _owner(self):
        if not self.user_id:
            raise PermissionError("Reminder owner is required")
        return self.user_id

    async def create_reminder(self, task_id, title, remind_at, timezone, recurrence):
        owner = self._owner()
        if not isinstance(self.chat_id, int) or self.chat_id <= 0:
            raise ValueError("A private Telegram chat is required for reminders")
        def run():
            identifier = "rem_" + uuid.uuid4().hex
            with self._connect() as conn:
                conn.execute("INSERT INTO reminders (id, task_id, title, remind_at, timezone, recurrence, status, created_at, user_id, chat_id) VALUES (?, ?, ?, ?, ?, ?, 'SCHEDULED', ?, ?, ?)",
                             (identifier, task_id, title, remind_at, timezone, recurrence, datetime.now().isoformat(), owner, self.chat_id))
            return self._get_reminder_sync(identifier)
        return await asyncio.to_thread(run)

    def _get_reminder_sync(self, identifier):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM reminders WHERE id = ? AND user_id = ?", (identifier, self._owner())).fetchone()
        if row is None:
            raise PermissionError("Reminder not found or not owned by this user")
        return dict(row)

    async def get_reminder(self, reminder_id):
        return await asyncio.to_thread(self._get_reminder_sync, reminder_id)

    async def list_reminders(self, status=None):
        owner = self._owner()
        def run():
            query, params = "SELECT * FROM reminders WHERE user_id = ?", [owner]
            if status:
                query += " AND status = ?"
                params.append(status)
            with self._connect() as conn:
                return [dict(r) for r in conn.execute(query, params)]
        return await asyncio.to_thread(run)

    async def update_reminder(self, reminder_id, updates):
        owner = self._owner()
        allowed = {k: v for k, v in updates.items() if k in {"title", "remind_at", "timezone", "recurrence"}}
        def run():
            self._get_reminder_sync(reminder_id)
            if allowed:
                fields = ", ".join(f"{k} = ?" for k in allowed)
                with self._connect() as conn:
                    conn.execute(f"UPDATE reminders SET {fields} WHERE id = ? AND user_id = ?", [*allowed.values(), reminder_id, owner])
            return self._get_reminder_sync(reminder_id)
        return await asyncio.to_thread(run)

    async def cancel_reminder(self, reminder_id):
        owner = self._owner()
        def run():
            with self._connect() as conn:
                return conn.execute("UPDATE reminders SET status = 'CANCELLED' WHERE id = ? AND user_id = ? AND status IN ('SCHEDULED', 'RETRYING')", (reminder_id, owner)).rowcount > 0
        return await asyncio.to_thread(run)
