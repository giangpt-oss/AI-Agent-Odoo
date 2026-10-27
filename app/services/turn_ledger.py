import json
import sqlite3
import logging
from datetime import datetime, UTC
from pathlib import Path
from typing import List, Optional

from app.services.file_service import file_service

logger = logging.getLogger(__name__)


class TurnLedger:
    """Quản lý nhật ký xử lý turns để bảo đảm idempotency theo request_id."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (file_service.data_dir / "turn_ledger.db")
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS turn_records (
                    request_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    messages_json TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
            """)
            cursor = conn.execute("PRAGMA table_info(turn_records)")
            columns = [row[1] for row in cursor.fetchall()]
            if "telegram_binding_id" not in columns:
                conn.execute("ALTER TABLE turn_records ADD COLUMN telegram_binding_id TEXT")
            if "payload_hash" not in columns:
                conn.execute("ALTER TABLE turn_records ADD COLUMN payload_hash TEXT")
            conn.commit()

    def get_turn_record(self, request_id: str) -> Optional[dict]:
        """Lấy toàn bộ thông tin bản ghi turn theo request_id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM turn_records WHERE request_id = ?",
                (request_id,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_turn(self, request_id: str) -> Optional[List[str]]:
        """Trả về messages đã lưu nếu request_id đã hoàn thành trước đó."""
        rec = self.get_turn_record(request_id)
        if rec and rec.get("status") == "completed" and rec.get("messages_json"):
            try:
                return json.loads(rec["messages_json"])
            except Exception as e:
                logger.warning("Error parsing cached messages for request_id=%s: %s", request_id, e)
                return None
        return None

    def record_turn_start(
        self,
        request_id: str,
        user_id: int,
        telegram_user_id: int,
        chat_id: int,
        telegram_binding_id: Optional[str] = None,
        payload_hash: Optional[str] = None,
    ) -> bool:
        """Ghi nhận bắt đầu turn. Trả về True nếu là request mới, False nếu request_id đã tồn tại."""
        now = datetime.now(UTC).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO turn_records (
                        request_id, user_id, telegram_user_id, chat_id,
                        telegram_binding_id, payload_hash, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'in_progress', ?)
                    """,
                    (request_id, user_id, telegram_user_id, chat_id, telegram_binding_id, payload_hash, now),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def record_turn_completed(self, request_id: str, messages: List[str]) -> None:
        """Ghi nhận turn đã hoàn thành cùng danh sách messages trả về."""
        now = datetime.now(UTC).isoformat()
        messages_json = json.dumps(messages, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE turn_records
                SET status = 'completed', messages_json = ?, completed_at = ?
                WHERE request_id = ?
                """,
                (messages_json, now, request_id),
            )
            conn.commit()

    def clear_turn(self, request_id: str) -> None:
        """Xóa turn khỏi sổ nhật ký (dùng khi gặp transient network error để cho phép retry sạch)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM turn_records WHERE request_id = ?", (request_id,))
            conn.commit()


turn_ledger = TurnLedger()
