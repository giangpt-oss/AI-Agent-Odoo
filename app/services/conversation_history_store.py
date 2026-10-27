import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional


class ConversationHistoryStore:
    """Kho lưu trữ lịch sử và trạng thái hội thoại đa lượt bằng SQLite.
    
    Quản lý ngữ cảnh theo tenant, user_id, chat_id, telegram_binding_id.
    - Giới hạn 20 lượt gần nhất, TTL 30 phút.
    - Tự động reset ngữ cảnh khi hết hạn hoặc khi đổi binding_id.
    """

    def __init__(
        self,
        db_path: str = "conversation_context.db",
        ttl_seconds: int = 1800,
        max_turns: int = 20,
    ) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_key TEXT PRIMARY KEY,
                    tenant TEXT,
                    user_id INTEGER,
                    chat_id INTEGER,
                    binding_id TEXT,
                    history_json TEXT DEFAULT '[]',
                    last_read_json TEXT DEFAULT '{}',
                    active_draft_json TEXT DEFAULT '{}',
                    pending_confirmation_json TEXT DEFAULT '{}',
                    updated_at REAL
                )
            """)
            conn.commit()

    def _build_key(self, tenant: Optional[str], user_id: int, chat_id: int) -> str:
        t = (tenant or "default").strip()
        return f"{t}:{user_id}:{chat_id}"

    def get_session(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
    ) -> dict[str, Any]:
        key = self._build_key(tenant, user_id, chat_id)
        now = time.time()

        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_sessions WHERE session_key = ?",
                (key,),
            ).fetchone()

            if not row:
                return {"history": [], "last_read": {}, "active_draft": None, "pending": None}

            # Kiểm tra TTL
            if now - row["updated_at"] > self.ttl_seconds:
                conn.execute("DELETE FROM conversation_sessions WHERE session_key = ?", (key,))
                conn.commit()
                return {"history": [], "last_read": {}, "active_draft": None, "pending": None}

            # Kiểm tra binding_id thay đổi
            curr_binding = binding_id or "default"
            stored_binding = row["binding_id"] or "default"
            if curr_binding != stored_binding:
                conn.execute("DELETE FROM conversation_sessions WHERE session_key = ?", (key,))
                conn.commit()
                return {"history": [], "last_read": {}, "active_draft": None, "pending": None}

            history = json.loads(row["history_json"] or "[]")
            last_read = json.loads(row["last_read_json"] or "{}")
            draft = json.loads(row["active_draft_json"] or "{}")
            pending = json.loads(row["pending_confirmation_json"] or "{}")

            return {
                "history": history,
                "last_read": last_read,
                "active_draft": draft if draft else None,
                "pending": pending if pending else None,
            }

    def append_turn(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        role: str,
        content: str,
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id)
        curr_session = self.get_session(tenant, user_id, chat_id, binding_id)
        history = curr_session["history"]
        history.append({"role": role, "content": content, "timestamp": time.time()})

        # Cắt gọt nếu vượt quá max_turns
        if len(history) > self.max_turns:
            history = history[-self.max_turns :]

        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO conversation_sessions (
                    session_key, tenant, user_id, chat_id, binding_id, history_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    binding_id = excluded.binding_id,
                    history_json = excluded.history_json,
                    updated_at = excluded.updated_at
                """,
                (key, tenant, user_id, chat_id, binding_id or "default", json.dumps(history, ensure_ascii=False), now),
            )
            conn.commit()

    def set_last_read_data(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        category: str,
        records: list[dict[str, Any]],
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id)
        curr_session = self.get_session(tenant, user_id, chat_id, binding_id)
        last_read = curr_session["last_read"]
        last_read[category] = records

        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO conversation_sessions (
                    session_key, tenant, user_id, chat_id, binding_id, last_read_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    binding_id = excluded.binding_id,
                    last_read_json = excluded.last_read_json,
                    updated_at = excluded.updated_at
                """,
                (key, tenant, user_id, chat_id, binding_id or "default", json.dumps(last_read, ensure_ascii=False), now),
            )
            conn.commit()

    def get_last_read_data(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        category: str,
    ) -> list[dict[str, Any]]:
        curr_session = self.get_session(tenant, user_id, chat_id, binding_id)
        return curr_session["last_read"].get(category, [])

    def set_active_draft(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        draft_data: dict[str, Any],
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id)
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO conversation_sessions (
                    session_key, tenant, user_id, chat_id, binding_id, active_draft_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    binding_id = excluded.binding_id,
                    active_draft_json = excluded.active_draft_json,
                    updated_at = excluded.updated_at
                """,
                (key, tenant, user_id, chat_id, binding_id or "default", json.dumps(draft_data, ensure_ascii=False), now),
            )
            conn.commit()

    def get_active_draft(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
    ) -> Optional[dict[str, Any]]:
        curr_session = self.get_session(tenant, user_id, chat_id, binding_id)
        return curr_session["active_draft"]

    def clear_active_draft(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id)
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE conversation_sessions
                SET active_draft_json = '{}', updated_at = ?
                WHERE session_key = ?
                """,
                (now, key),
            )
            conn.commit()


conversation_history_store = ConversationHistoryStore()
