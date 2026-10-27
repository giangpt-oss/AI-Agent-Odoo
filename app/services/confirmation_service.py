import json
import sqlite3
import secrets
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.services.file_service import file_service


class ConfirmationService:
    """Quản lý bản xem trước (preview) và xác nhận hành động ghi (/confirm <id>).
    
    Quy tắc nghiệp vụ:
    - Hạn sử dụng: 5 phút kể từ lúc tạo preview.
    - Dùng 1 lần duy nhất (single-use).
    - Vòng đời: pending -> executing -> completed / failed / unknown_result; giữ cancelled, expired.
    - Atomic claim bằng conditional update (rowcount == 1).
    - Sinh và lưu operation_id ngay lúc tạo preview; mọi lần xử lý lại dùng cùng ID.
    - Gắn chặt với user_id, chat_id, telegram_binding_id và payload arguments đóng băng.
    - Lưu trong SQLite cùng database tasks (tasks.db) để đảm bảo transaction nguyên tử.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (file_service.data_dir / "tasks.db")
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Trả về kết nối SQLite tới database chung để hỗ trợ atomic transaction."""
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_confirmations (
                    confirmation_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    telegram_binding_id TEXT NOT NULL,
                    owner_key TEXT,
                    action TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    target_snapshot_json TEXT,
                    preview_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)

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
                    updated_at TEXT NOT NULL,
                    owner_key TEXT
                )
            """)

            # Schema migration for existing tables
            cursor = conn.execute("PRAGMA table_info(pending_confirmations)")
            existing_cols = {r[1] for r in cursor.fetchall()}

            if "operation_id" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN operation_id TEXT")
            if "result_json" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN result_json TEXT")
            if "error_message" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN error_message TEXT")
            if "updated_at" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN updated_at TEXT")

            now_str = datetime.now(UTC).isoformat()
            # Cập nhật operation_id và updated_at cho bản ghi cũ nếu bị null
            conn.execute("""
                UPDATE pending_confirmations
                SET operation_id = 'op_migrated_' || hex(randomblob(8))
                WHERE operation_id IS NULL OR operation_id = ''
            """)
            conn.execute("""
                UPDATE pending_confirmations
                SET updated_at = created_at
                WHERE updated_at IS NULL OR updated_at = ''
            """)

            # Migration: bản ghi 'confirmed' cũ không có bằng chứng kết quả chuyển thành 'unknown_result'
            conn.execute("""
                UPDATE pending_confirmations
                SET status = 'unknown_result',
                    error_message = 'Chuyển đổi từ dữ liệu cũ: không có bằng chứng kết quả thực thi',
                    updated_at = ?
                WHERE status = 'confirmed'
            """, (now_str,))

            conn.commit()

        # Migrate data from legacy confirmations.db if it exists and is distinct
        legacy_file = file_service.data_dir / "confirmations.db"
        if legacy_file.exists() and legacy_file.resolve() != self.db_path.resolve():
            try:
                self._migrate_from_legacy_file(legacy_file)
            except Exception:
                pass

    def _migrate_from_legacy_file(self, legacy_file: Path) -> None:
        with sqlite3.connect(legacy_file) as leg_conn:
            cursor = leg_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pending_confirmations'")
            if not cursor.fetchone():
                return
            rows = leg_conn.execute("SELECT * FROM pending_confirmations").fetchall()
            cursor = leg_conn.execute("PRAGMA table_info(pending_confirmations)")
            cols = [r[1] for r in cursor.fetchall()]

        if not rows:
            return

        with sqlite3.connect(self.db_path, timeout=20.0) as dest_conn:
            for row in rows:
                row_dict = dict(zip(cols, row))
                cid = row_dict["confirmation_id"]
                exists = dest_conn.execute("SELECT 1 FROM pending_confirmations WHERE confirmation_id = ?", (cid,)).fetchone()
                if exists:
                    continue

                raw_status = row_dict.get("status", "pending")
                status = "unknown_result" if raw_status == "confirmed" else raw_status
                op_id = row_dict.get("operation_id") or f"op_leg_{secrets.token_hex(8)}"
                created_at = row_dict.get("created_at") or datetime.now(UTC).isoformat()
                updated_at = row_dict.get("updated_at") or created_at
                expires_at = row_dict.get("expires_at") or created_at

                dest_conn.execute(
                    """
                    INSERT INTO pending_confirmations (
                        confirmation_id, operation_id, user_id, chat_id, telegram_binding_id,
                        owner_key, action, arguments_json, target_snapshot_json, preview_text,
                        status, result_json, error_message, created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid,
                        op_id,
                        row_dict.get("user_id"),
                        row_dict.get("chat_id"),
                        row_dict.get("telegram_binding_id"),
                        row_dict.get("owner_key"),
                        row_dict.get("action"),
                        row_dict.get("arguments_json"),
                        row_dict.get("target_snapshot_json"),
                        row_dict.get("preview_text"),
                        status,
                        row_dict.get("result_json"),
                        row_dict.get("error_message"),
                        created_at,
                        updated_at,
                        expires_at,
                    ),
                )
            dest_conn.commit()

    def create_confirmation(
        self,
        user_id: int,
        chat_id: int,
        telegram_binding_id: str,
        action: str,
        arguments: Dict[str, Any],
        preview_text: str,
        owner_key: Optional[str] = None,
        target_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """Tạo bản xem trước và lưu hành động chờ xác nhận với hạn 5 phút.
        Sinh operation_id cố định ngay lúc này để dùng xuyên suốt vòng đời."""
        conf_id = f"c_{secrets.token_hex(4)}"
        operation_id = f"op_{secrets.token_hex(12)}"
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=5)

        now_str = now.isoformat()
        expires_str = expires.isoformat()
        args_json = json.dumps(arguments, ensure_ascii=False)
        snap_json = json.dumps(target_snapshot, ensure_ascii=False) if target_snapshot else None

        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.execute(
                """
                INSERT INTO pending_confirmations (
                    confirmation_id, operation_id, user_id, chat_id, telegram_binding_id,
                    owner_key, action, arguments_json, target_snapshot_json,
                    preview_text, status, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    conf_id,
                    operation_id,
                    user_id,
                    chat_id,
                    telegram_binding_id,
                    owner_key,
                    action,
                    args_json,
                    snap_json,
                    preview_text,
                    now_str,
                    now_str,
                    expires_str,
                ),
            )
            conn.commit()

        instruction = (
            f"\n\n⚠️ **Hành động cần bạn xác nhận:**\n"
            f"👉 Gửi `/confirm {conf_id}` để xác nhận thực hiện.\n"
            f"👉 Gửi `/cancel {conf_id}` để hủy bỏ.\n"
            f"_(Mã có hiệu lực trong vòng 5 phút)_"
        )
        return conf_id, preview_text + instruction

    def claim_and_validate(
        self,
        confirmation_id: str,
        user_id: int,
        chat_id: int,
        telegram_binding_id: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Kiểm tra và nhận claim hành động một cách nguyên tử bằng conditional update.
        Chỉ tiến trình claim thành công (rowcount == 1) mới chuyển sang 'executing'."""
        clean_id = confirmation_id.strip()
        now = datetime.now(UTC)
        now_str = now.isoformat()

        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                SELECT confirmation_id, operation_id, user_id, chat_id, telegram_binding_id,
                       owner_key, action, arguments_json, target_snapshot_json,
                       preview_text, status, expires_at
                FROM pending_confirmations
                WHERE confirmation_id = ?
                """,
                (clean_id,),
            )
            row = cursor.fetchone()
            if not row:
                conn.commit()
                return False, f"Mã xác nhận `{clean_id}` không tồn tại hoặc đã bị xóa.", None

            (
                cid,
                c_op_id,
                c_uid,
                c_chat,
                c_bind,
                c_owner,
                c_act,
                c_args,
                c_snap,
                c_prev,
                c_status,
                c_exp,
            ) = row

            if c_status == "executing":
                conn.commit()
                return False, f"Mã xác nhận `{clean_id}` đang được thực thi bởi tiến trình khác.", None
            if c_status == "completed":
                conn.commit()
                return False, f"Mã xác nhận `{clean_id}` đã được thực thi hoàn tất trước đó (chỉ dùng 1 lần).", None
            if c_status == "failed":
                conn.commit()
                return False, f"Mã xác nhận `{clean_id}` trước đó đã thất bại. Vui lòng tạo yêu cầu mới.", None
            if c_status == "unknown_result":
                conn.commit()
                return False, f"Mã xác nhận `{clean_id}` chưa rõ kết quả và cần được đối soát, không thể tự động thực thi lại.", None
            if c_status == "cancelled":
                conn.commit()
                return False, f"Mã xác nhận `{clean_id}` đã bị hủy bỏ.", None
            if c_status != "pending":
                conn.commit()
                return False, f"Mã xác nhận `{clean_id}` ở trạng thái không hợp lệ: {c_status}.", None

            # Check user and chat isolation
            if c_uid != user_id or c_chat != chat_id:
                conn.commit()
                return False, "Bạn không có quyền thực thi mã xác nhận này (không khớp tài khoản/cuộc trò chuyện).", None

            # Check binding_id (relinked Telegram must invalidate old confirmations)
            if c_bind != telegram_binding_id:
                conn.commit()
                return False, "Liên kết Telegram của bạn đã được thay đổi kể từ lúc tạo yêu cầu. Vui lòng tạo yêu cầu mới.", None

            # Check expiry (5 minutes)
            try:
                exp_dt = datetime.fromisoformat(c_exp.replace("Z", "+00:00"))
                if not exp_dt.tzinfo:
                    exp_dt = exp_dt.replace(tzinfo=UTC)
                if now > exp_dt:
                    conn.execute(
                        "UPDATE pending_confirmations SET status = 'expired', updated_at = ? WHERE confirmation_id = ?",
                        (now_str, clean_id),
                    )
                    conn.commit()
                    return False, f"Mã xác nhận `{clean_id}` đã hết hạn 5 phút. Vui lòng tạo yêu cầu mới.", None
            except Exception:
                pass

            # Atomic conditional update claim
            update_cursor = conn.execute(
                """
                UPDATE pending_confirmations
                SET status = 'executing', updated_at = ?
                WHERE confirmation_id = ? AND status = 'pending'
                """,
                (now_str, clean_id),
            )
            if update_cursor.rowcount != 1:
                conn.commit()
                return False, f"Mã xác nhận `{clean_id}` đang được thực thi hoặc đã được xử lý bởi tiến trình khác.", None

            conn.commit()

            return True, "", {
                "confirmation_id": cid,
                "operation_id": c_op_id,
                "user_id": c_uid,
                "chat_id": c_chat,
                "telegram_binding_id": c_bind,
                "owner_key": c_owner,
                "action": c_act,
                "arguments": json.loads(c_args),
                "target_snapshot": json.loads(c_snap) if c_snap else None,
            }

    def mark_completed(self, confirmation_id: str, result_data: Optional[Dict[str, Any]] = None) -> None:
        """Đánh dấu hoàn thành sau khi side effect thành công."""
        now_str = datetime.now(UTC).isoformat()
        res_json = json.dumps(result_data, ensure_ascii=False) if result_data else None
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.execute(
                """
                UPDATE pending_confirmations
                SET status = 'completed', result_json = ?, updated_at = ?
                WHERE confirmation_id = ?
                """,
                (res_json, now_str, confirmation_id.strip()),
            )
            conn.commit()

    def mark_failed(self, confirmation_id: str, error_message: str) -> None:
        """Đánh dấu thất bại khi side effect gặp lỗi."""
        now_str = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.execute(
                """
                UPDATE pending_confirmations
                SET status = 'failed', error_message = ?, updated_at = ?
                WHERE confirmation_id = ?
                """,
                (error_message[:1000], now_str, confirmation_id.strip()),
            )
            conn.commit()

    def mark_unknown(self, confirmation_id: str, reason: str) -> None:
        """Đánh dấu kết quả không xác định (timeout, network crash sau khi gửi) để đối soát."""
        now_str = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.execute(
                """
                UPDATE pending_confirmations
                SET status = 'unknown_result', error_message = ?, updated_at = ?
                WHERE confirmation_id = ?
                """,
                (reason[:1000], now_str, confirmation_id.strip()),
            )
            conn.commit()

    def cancel_confirmation(
        self,
        confirmation_id: str,
        user_id: int,
        chat_id: int,
        telegram_binding_id: str,
    ) -> Tuple[bool, str]:
        """Hủy bỏ hành động chờ xác nhận."""
        clean_id = confirmation_id.strip()
        now_str = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            cursor = conn.execute(
                "SELECT user_id, chat_id, telegram_binding_id, status FROM pending_confirmations WHERE confirmation_id = ?",
                (clean_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False, f"Mã xác nhận `{clean_id}` không tồn tại."
            c_uid, c_chat, c_bind, c_status = row
            if c_uid != user_id or c_chat != chat_id or c_bind != telegram_binding_id:
                return False, "Bạn không có quyền hủy mã xác nhận này."
            if c_status != "pending":
                return False, f"Mã xác nhận `{clean_id}` hiện ở trạng thái: {c_status} (không thể hủy)."

            conn.execute(
                "UPDATE pending_confirmations SET status = 'cancelled', updated_at = ? WHERE confirmation_id = ?",
                (now_str, clean_id),
            )
            conn.commit()
            return True, f"Đã hủy bỏ thành công yêu cầu `{clean_id}`."

    def get_confirmation(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        clean_id = confirmation_id.strip()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM pending_confirmations WHERE confirmation_id = ?",
                (clean_id,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None


confirmation_service = ConfirmationService()
