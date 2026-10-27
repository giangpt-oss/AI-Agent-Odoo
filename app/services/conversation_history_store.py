# -*- coding: utf-8 -*-
"""Kho lưu trữ lịch sử và trạng thái hội thoại đa lượt bằng SQLite.

Quản lý ngữ cảnh theo tenant, database, user_id, chat_id, telegram_binding_id.
- Giới hạn 20 lượt gần nhất, TTL 30 phút.
- Tự động reset ngữ cảnh khi hết hạn hoặc khi đổi binding_id.
- Hỗ trợ versioning cho bản nháp (draft).
- Hỗ trợ tuần tự hóa (turn lease) các turn của cùng một session.
- Đặt database trong AGENT_DATA_DIR bền vững (khớp volume Docker).
"""

import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_default_db_path() -> str:
    env_dir = os.environ.get("AGENT_DATA_DIR")
    if env_dir:
        d = Path(env_dir).resolve()
    else:
        d = Path(os.getcwd()).resolve() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "conversation_context.db")


class ConversationHistoryStore:
    def __init__(
        self,
        db_path: Optional[str] = None,
        ttl_seconds: int = 1800,
        max_turns: int = 20,
    ) -> None:
        self.db_path = db_path or _get_default_db_path()
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
                    database TEXT,
                    user_id INTEGER,
                    chat_id INTEGER,
                    binding_id TEXT,
                    history_json TEXT DEFAULT '[]',
                    last_read_json TEXT DEFAULT '{}',
                    active_draft_json TEXT DEFAULT '{}',
                    pending_confirmation_json TEXT DEFAULT '{}',
                    pending_clarification_json TEXT DEFAULT '{}',
                    lease_token TEXT,
                    lease_expires_at REAL,
                    updated_at REAL
                )
            """)

            # Additive migration PRAGMA
            cursor = conn.execute("PRAGMA table_info(conversation_sessions)")
            cols = {r[1] for r in cursor.fetchall()}
            if "database" not in cols:
                conn.execute("ALTER TABLE conversation_sessions ADD COLUMN database TEXT")
            if "pending_clarification_json" not in cols:
                conn.execute("ALTER TABLE conversation_sessions ADD COLUMN pending_clarification_json TEXT DEFAULT '{}'")
            if "lease_token" not in cols:
                conn.execute("ALTER TABLE conversation_sessions ADD COLUMN lease_token TEXT")
            if "lease_expires_at" not in cols:
                conn.execute("ALTER TABLE conversation_sessions ADD COLUMN lease_expires_at REAL")
            conn.commit()

        # Di chuyển dữ liệu cũ từ file root nếu có và khác đích
        legacy_root_file = Path(os.getcwd()) / "conversation_context.db"
        dest_file = Path(self.db_path).resolve()
        if legacy_root_file.exists() and legacy_root_file.resolve() != dest_file:
            try:
                self._migrate_from_legacy(legacy_root_file)
            except Exception:
                pass

    def _migrate_from_legacy(self, legacy_file: Path) -> None:
        with sqlite3.connect(legacy_file) as src_conn:
            src_conn.row_factory = sqlite3.Row
            cursor = src_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_sessions'")
            if not cursor.fetchone():
                return
            rows = src_conn.execute("SELECT * FROM conversation_sessions").fetchall()

        if not rows:
            return

        with self._get_connection() as dest_conn:
            for row in rows:
                k = row["session_key"]
                exists = dest_conn.execute("SELECT 1 FROM conversation_sessions WHERE session_key = ?", (k,)).fetchone()
                if not exists:
                    dest_conn.execute(
                        """
                        INSERT INTO conversation_sessions (
                            session_key, tenant, user_id, chat_id, binding_id, history_json,
                            last_read_json, active_draft_json, pending_confirmation_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            k,
                            row["tenant"],
                            row["user_id"],
                            row["chat_id"],
                            row["binding_id"],
                            row["history_json"],
                            row["last_read_json"],
                            row["active_draft_json"],
                            row["pending_confirmation_json"],
                            row["updated_at"],
                        ),
                    )
            dest_conn.commit()

    def _build_key(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        database: Optional[str] = None,
        binding_id: Optional[str] = None,
    ) -> str:
        t = (tenant or "default").strip()
        db_name = (database or "default").strip()
        bind = (binding_id or "default").strip()
        return f"{t}:{db_name}:{user_id}:{chat_id}:{bind}"

    def get_session(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        database: Optional[str] = None,
    ) -> dict[str, Any]:
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
        now = time.time()

        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_sessions WHERE session_key = ?",
                (key,),
            ).fetchone()

            if not row:
                return {
                    "history": [],
                    "last_read": {},
                    "active_draft": None,
                    "pending": None,
                    "pending_clarification": None,
                }

            # Kiểm tra TTL (30 phút)
            if now - row["updated_at"] > self.ttl_seconds:
                conn.execute("DELETE FROM conversation_sessions WHERE session_key = ?", (key,))
                conn.commit()
                return {
                    "history": [],
                    "last_read": {},
                    "active_draft": None,
                    "pending": None,
                    "pending_clarification": None,
                }

            # Kiểm tra binding_id thay đổi
            curr_binding = binding_id or "default"
            stored_binding = row["binding_id"] or "default"
            if curr_binding != stored_binding:
                conn.execute("DELETE FROM conversation_sessions WHERE session_key = ?", (key,))
                conn.commit()
                return {
                    "history": [],
                    "last_read": {},
                    "active_draft": None,
                    "pending": None,
                    "pending_clarification": None,
                }

            history = json.loads(row["history_json"] or "[]")
            last_read = json.loads(row["last_read_json"] or "{}")
            draft = json.loads(row["active_draft_json"] or "{}")
            pending = json.loads(row["pending_confirmation_json"] or "{}")
            clarification = json.loads(row["pending_clarification_json"] or "{}") if "pending_clarification_json" in row.keys() else {}

            return {
                "history": history,
                "last_read": last_read,
                "active_draft": draft if draft else None,
                "pending": pending if pending else None,
                "pending_clarification": clarification if clarification else None,
            }

    def append_turn(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        role: str,
        content: str,
        database: Optional[str] = None,
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
        curr_session = self.get_session(tenant, user_id, chat_id, binding_id, database)
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
                    session_key, tenant, database, user_id, chat_id, binding_id, history_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    binding_id = excluded.binding_id,
                    history_json = excluded.history_json,
                    updated_at = excluded.updated_at
                """,
                (key, tenant, database or "default", user_id, chat_id, binding_id or "default", json.dumps(history, ensure_ascii=False), now),
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
        database: Optional[str] = None,
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
        curr_session = self.get_session(tenant, user_id, chat_id, binding_id, database)
        last_read = curr_session["last_read"]
        last_read[category] = records

        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO conversation_sessions (
                    session_key, tenant, database, user_id, chat_id, binding_id, last_read_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    binding_id = excluded.binding_id,
                    last_read_json = excluded.last_read_json,
                    updated_at = excluded.updated_at
                """,
                (key, tenant, database or "default", user_id, chat_id, binding_id or "default", json.dumps(last_read, ensure_ascii=False), now),
            )
            conn.commit()

    def get_last_read_data(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        category: str,
        database: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        curr_session = self.get_session(tenant, user_id, chat_id, binding_id, database)
        return curr_session["last_read"].get(category, [])

    def set_active_draft(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        draft_data: dict[str, Any],
        database: Optional[str] = None,
        expected_version: Optional[int] = None,
        expected_draft_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Lưu bản nháp với versioning tự động và Compare-And-Swap (CAS) nguyên tử.
        Sử dụng BEGIN IMMEDIATE trước SELECT; đọc draft, kiểm tra expected ID/version
        và ghi kết quả trong cùng một transaction SQLite."""
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
        now = time.time()
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        self.last_draft_error = None

        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                logger.warning("Lock timeout attempting to acquire SQLite lock for draft session %s: %s", key, e)
                self.last_draft_error = "LOCK_TIMEOUT"
                return None

            cursor = conn.execute(
                "SELECT active_draft_json, updated_at FROM conversation_sessions WHERE session_key = ?", (key,)
            )
            row = cursor.fetchone()
            existing_draft = None
            if row and row["active_draft_json"]:
                try:
                    existing_draft = json.loads(row["active_draft_json"])
                except Exception:
                    existing_draft = None

            # CAS verification
            if expected_draft_id is not None:
                curr_did = existing_draft.get("draft_id") if existing_draft else None
                if curr_did != expected_draft_id:
                    conn.execute("ROLLBACK")
                    logger.warning("Draft CAS failed: expected draft_id=%s, current=%s", expected_draft_id, curr_did)
                    self.last_draft_error = "VERSION_CONFLICT"
                    return None

            if expected_version is not None:
                curr_ver = existing_draft.get("version") if existing_draft else None
                if curr_ver != expected_version:
                    conn.execute("ROLLBACK")
                    logger.warning("Draft CAS failed: expected version=%s, current=%s", expected_version, curr_ver)
                    self.last_draft_error = "VERSION_CONFLICT"
                    return None

            draft_copy = dict(draft_data)
            # Draft ID do application bảo vệ
            if "draft_id" in draft_data or expected_draft_id is not None:
                if not draft_copy.get("draft_id"):
                    draft_copy["draft_id"] = (
                        existing_draft.get("draft_id") if existing_draft else f"draft_{secrets.token_hex(6)}"
                    )

            # Version: application TỰ TÍNH từ version đang lưu (+1), không lấy giá trị tùy ý từ caller hoặc LLM
            if "version" in draft_data or expected_version is not None:
                if existing_draft is not None:
                    draft_copy["version"] = existing_draft.get("version", 0) + 1
                else:
                    draft_copy["version"] = draft_data.get("version", 1)

            if "updated_at" in draft_data:
                draft_copy["updated_at"] = now

            conn.execute(
                """
                INSERT INTO conversation_sessions (
                    session_key, tenant, database, user_id, chat_id, binding_id, active_draft_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    binding_id = excluded.binding_id,
                    active_draft_json = excluded.active_draft_json,
                    updated_at = excluded.updated_at
                """,
                (key, tenant, database or "default", user_id, chat_id, binding_id or "default", json.dumps(draft_copy, ensure_ascii=False), now),
            )
            conn.execute("COMMIT")
            return draft_copy
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            logger.error("Error in atomic set_active_draft for session %s: %s", key, e)
            raise
        finally:
            conn.close()

    def get_active_draft(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        database: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        curr_session = self.get_session(tenant, user_id, chat_id, binding_id, database)
        return curr_session["active_draft"]

    def clear_active_draft(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        database: Optional[str] = None,
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
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

    def set_pending_clarification(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        clarification_data: dict[str, Any],
        database: Optional[str] = None,
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO conversation_sessions (
                    session_key, tenant, database, user_id, chat_id, binding_id, pending_clarification_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    pending_clarification_json = excluded.pending_clarification_json,
                    updated_at = excluded.updated_at
                """,
                (key, tenant, database or "default", user_id, chat_id, binding_id or "default", json.dumps(clarification_data, ensure_ascii=False), now),
            )
            conn.commit()

    def clear_pending_clarification(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        database: Optional[str] = None,
    ) -> None:
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE conversation_sessions SET pending_clarification_json = '{}' WHERE session_key = ?",
                (key,),
            )
            conn.commit()

    def claim_turn_lease(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        database: Optional[str] = None,
        lease_seconds: float = 18.0,
    ) -> Optional[str]:
        """Thử lấy lease cho phiên hội thoại để tuần tự hóa các turn cùng session.
        Trả về lease_token nếu thành công, None nếu session đang bị chiếm giữ."""
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
        token = f"lease_{secrets.token_hex(8)}"
        now = time.time()
        expires_at = now + lease_seconds

        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT lease_token, lease_expires_at FROM conversation_sessions WHERE session_key = ?",
                (key,),
            ).fetchone()

            if not row:
                conn.execute(
                    """
                    INSERT INTO conversation_sessions (
                        session_key, tenant, database, user_id, chat_id, binding_id,
                        lease_token, lease_expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, tenant, database or "default", user_id, chat_id, binding_id or "default", token, expires_at, now),
                )
                conn.commit()
                return token

            old_token = row["lease_token"]
            old_expires = row["lease_expires_at"] or 0.0
            if old_token and now < old_expires:
                return None

            conn.execute(
                """
                UPDATE conversation_sessions
                SET lease_token = ?, lease_expires_at = ?, updated_at = ?
                WHERE session_key = ?
                """,
                (token, expires_at, now, key),
            )
            conn.commit()
            return token

    def release_turn_lease(
        self,
        tenant: Optional[str],
        user_id: int,
        chat_id: int,
        binding_id: Optional[str],
        database: Optional[str] = None,
        lease_token: Optional[str] = None,
    ) -> None:
        if not lease_token:
            return
        key = self._build_key(tenant, user_id, chat_id, database, binding_id)
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE conversation_sessions
                SET lease_token = NULL, lease_expires_at = NULL
                WHERE session_key = ? AND lease_token = ?
                """,
                (key, lease_token),
            )
            conn.commit()


conversation_history_store = ConversationHistoryStore()
