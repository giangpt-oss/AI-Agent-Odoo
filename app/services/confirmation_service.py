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
            if "draft_id" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN draft_id TEXT")
            if "draft_version" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN draft_version INTEGER")
            if "dispatch_started_at" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN dispatch_started_at TEXT")
            if "execution_token" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN execution_token TEXT")
            if "lease_expires_at" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN lease_expires_at REAL")
            if "telegram_user_id" not in existing_cols:
                conn.execute("ALTER TABLE pending_confirmations ADD COLUMN telegram_user_id INTEGER")

            cursor_tasks = conn.execute("PRAGMA table_info(tasks)")
            task_cols = {r[1] for r in cursor_tasks.fetchall()}
            if "owner_key" not in task_cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN owner_key TEXT")
            if "revision" not in task_cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_tasks_revision_on_update
                AFTER UPDATE OF title, description, status, priority, due_at, timezone, project, tags ON tasks
                FOR EACH ROW
                WHEN (
                    (OLD.title IS NOT NEW.title) OR
                    (OLD.description IS NOT NEW.description) OR
                    (OLD.status IS NOT NEW.status) OR
                    (OLD.priority IS NOT NEW.priority) OR
                    (OLD.due_at IS NOT NEW.due_at) OR
                    (OLD.timezone IS NOT NEW.timezone) OR
                    (OLD.project IS NOT NEW.project) OR
                    (OLD.tags IS NOT NEW.tags)
                )
                BEGIN
                    UPDATE tasks
                    SET revision = OLD.revision + 1
                    WHERE id = OLD.id;
                END;
            """)

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

        # Phục hồi các lease executing đã hết hạn khi khởi động
        try:
            self.recover_expired_leases()
        except Exception:
            pass

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
        draft_id: Optional[str] = None,
        draft_version: Optional[int] = None,
        telegram_user_id: Optional[int] = None,
    ) -> Tuple[str, str]:
        """Tạo bản xem trước và lưu hành động chờ xác nhận với hạn 5 phút.
        Sinh operation_id cố định ngay lúc này để dùng xuyên suốt vòng đời.
        Lưu đúng telegram_user_id đã xác minh từ context."""
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
                    preview_text, status, created_at, updated_at, expires_at,
                    draft_id, draft_version, telegram_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
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
                    draft_id,
                    draft_version,
                    telegram_user_id,
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
        owner_key: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Kiểm tra và nhận claim hành động một cách nguyên tử bằng conditional update.
        Chỉ tiến trình claim thành công (rowcount == 1) mới chuyển sang 'executing' với execution_token và lease."""
        clean_id = confirmation_id.strip()
        now = datetime.now(UTC)
        now_str = now.isoformat()
        now_ts = now.timestamp()

        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                SELECT confirmation_id, operation_id, user_id, chat_id, telegram_binding_id,
                       owner_key, action, arguments_json, target_snapshot_json,
                       preview_text, status, expires_at, draft_id, draft_version,
                       lease_expires_at, telegram_user_id
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
                c_draft_id,
                c_draft_ver,
                c_lease_exp,
                c_tele_uid,
            ) = row

            if c_status == "executing":
                # Kiểm tra xem lease có hết hạn chưa
                if c_lease_exp and now_ts > float(c_lease_exp):
                    # Lease đã hết hạn, chuyển sang unknown_result để đối soát, đồng thời xóa token/lease
                    conn.execute(
                        """
                        UPDATE pending_confirmations
                        SET status = 'unknown_result',
                            error_message = 'Lease hết hạn trước khi có phản hồi, cần đối soát',
                            execution_token = NULL,
                            lease_expires_at = NULL,
                            updated_at = ?
                        WHERE confirmation_id = ? AND status = 'executing'
                        """,
                        (now_str, clean_id),
                    )
                    conn.commit()
                    return False, f"Mã xác nhận `{clean_id}` chưa rõ kết quả và cần được đối soát (lease đã hết hạn).", None
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

            # Check owner_key
            if owner_key and c_owner and c_owner != owner_key:
                conn.commit()
                return False, "Bạn không có quyền thực thi mã xác nhận này (không khớp chủ sở hữu).", None

            # Bổ sung telegram_user_id cho confirmation cũ nếu chưa có
            if telegram_user_id and c_tele_uid is None:
                conn.execute(
                    "UPDATE pending_confirmations SET telegram_user_id = ? WHERE confirmation_id = ?",
                    (telegram_user_id, clean_id),
                )
                c_tele_uid = telegram_user_id

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

            # Sinh execution token và lease 30s
            execution_token = f"tok_{secrets.token_hex(8)}"
            lease_expires_at = now_ts + 30.0

            # Atomic conditional update claim
            update_cursor = conn.execute(
                """
                UPDATE pending_confirmations
                SET status = 'executing',
                    execution_token = ?,
                    lease_expires_at = ?,
                    updated_at = ?
                WHERE confirmation_id = ? AND status = 'pending'
                """,
                (execution_token, lease_expires_at, now_str, clean_id),
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
                "draft_id": c_draft_id,
                "draft_version": c_draft_ver,
                "execution_token": execution_token,
                "lease_expires_at": lease_expires_at,
                "preview_text": c_prev,
                "telegram_user_id": c_tele_uid,
            }

    def mark_dispatched(self, confirmation_id: str, execution_token: Optional[str] = None) -> bool:
        """Ghi nhận mốc bắt đầu dispatch ra mạng ngoài trước network call.
        Chỉ thành công nếu confirmation đang executing, đúng execution_token và lease chưa hết hạn."""
        if not execution_token:
            return False
        clean_id = confirmation_id.strip()
        now = datetime.now(UTC)
        now_str = now.isoformat()
        now_ts = now.timestamp()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            cursor = conn.execute(
                """
                UPDATE pending_confirmations
                SET dispatch_started_at = ?, updated_at = ?
                WHERE confirmation_id = ? AND status = 'executing'
                  AND execution_token = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                """,
                (now_str, now_str, clean_id, execution_token, now_ts),
            )
            conn.commit()
            return cursor.rowcount == 1

    def mark_completed(
        self,
        confirmation_id: str,
        result_data: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        execution_token: Optional[str] = None,
    ) -> bool:
        """Đánh dấu hoàn thành sau khi side effect thành công từ worker.
        Nếu có execution_token: bắt buộc đúng token, status='executing' và lease còn hạn."""
        now = datetime.now(UTC)
        now_str = now.isoformat()
        now_ts = now.timestamp()
        res = result_data if result_data is not None else result
        res_json = json.dumps(res, ensure_ascii=False) if res else None
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            if execution_token is not None:
                cursor = conn.execute(
                    """
                    UPDATE pending_confirmations
                    SET status = 'completed', result_json = ?, updated_at = ?,
                        execution_token = NULL, lease_expires_at = NULL
                    WHERE confirmation_id = ? AND status = 'executing'
                      AND execution_token = ?
                      AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                    """,
                    (res_json, now_str, confirmation_id.strip(), execution_token, now_ts),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE pending_confirmations
                    SET status = 'completed', result_json = ?, updated_at = ?,
                        execution_token = NULL, lease_expires_at = NULL
                    WHERE confirmation_id = ? AND status = 'executing'
                    """,
                    (res_json, now_str, confirmation_id.strip()),
                )
            conn.commit()
            return cursor.rowcount == 1

    def mark_failed(
        self,
        confirmation_id: str,
        error_message: str,
        execution_token: Optional[str] = None,
    ) -> bool:
        """Đánh dấu thất bại từ worker khi side effect gặp lỗi.
        Nếu có execution_token: bắt buộc đúng token, status='executing' và lease còn hạn."""
        now = datetime.now(UTC)
        now_str = now.isoformat()
        now_ts = now.timestamp()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            if execution_token is not None:
                cursor = conn.execute(
                    """
                    UPDATE pending_confirmations
                    SET status = 'failed', error_message = ?, updated_at = ?,
                        execution_token = NULL, lease_expires_at = NULL
                    WHERE confirmation_id = ? AND status = 'executing'
                      AND execution_token = ?
                      AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                    """,
                    (error_message[:1000], now_str, confirmation_id.strip(), execution_token, now_ts),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE pending_confirmations
                    SET status = 'failed', error_message = ?, updated_at = ?,
                        execution_token = NULL, lease_expires_at = NULL
                    WHERE confirmation_id = ? AND status = 'executing'
                    """,
                    (error_message[:1000], now_str, confirmation_id.strip()),
                )
            conn.commit()
            return cursor.rowcount == 1

    def mark_unknown(
        self,
        confirmation_id: str,
        reason: str,
        execution_token: Optional[str] = None,
    ) -> bool:
        """Đánh dấu kết quả không xác định (timeout, network crash) từ worker.
        Nếu có execution_token: bắt buộc đúng token, status='executing' và lease còn hạn."""
        now = datetime.now(UTC)
        now_str = now.isoformat()
        now_ts = now.timestamp()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            if execution_token is not None:
                cursor = conn.execute(
                    """
                    UPDATE pending_confirmations
                    SET status = 'unknown_result', error_message = ?, updated_at = ?,
                        execution_token = NULL, lease_expires_at = NULL
                    WHERE confirmation_id = ? AND status = 'executing'
                      AND execution_token = ?
                      AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                    """,
                    (reason[:1000], now_str, confirmation_id.strip(), execution_token, now_ts),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE pending_confirmations
                    SET status = 'unknown_result', error_message = ?, updated_at = ?,
                        execution_token = NULL, lease_expires_at = NULL
                    WHERE confirmation_id = ? AND status = 'executing'
                    """,
                    (reason[:1000], now_str, confirmation_id.strip()),
                )
            conn.commit()
            return cursor.rowcount == 1

    def reconcile_confirmation_result(
        self,
        confirmation_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        operation_id: Optional[str] = None,
    ) -> bool:
        """Đường đối soát chuyên biệt: chỉ chuyển unknown_result hoặc executing sang completed/failed.
        Không cho phép lùi hoặc ghi đè các trạng thái terminal (completed, failed, cancelled, expired)."""
        if status not in ["completed", "failed"]:
            return False
        clean_id = confirmation_id.strip()
        now_str = datetime.now(UTC).isoformat()
        res_json = json.dumps(result, ensure_ascii=False) if result else None
        err_msg = error_message[:1000] if error_message else None

        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            op_filter = "AND operation_id = ?" if operation_id else ""
            params = [status, res_json, err_msg, now_str, clean_id]
            if operation_id:
                params.append(operation_id)

            cursor = conn.execute(
                f"""
                UPDATE pending_confirmations
                SET status = ?, result_json = ?, error_message = ?, updated_at = ?,
                    execution_token = NULL, lease_expires_at = NULL
                WHERE confirmation_id = ?
                  AND status IN ('unknown_result', 'executing')
                  {op_filter}
                """,
                params,
            )
            conn.commit()
            return cursor.rowcount == 1

    def update_telegram_user_id(self, confirmation_id: str, telegram_user_id: int) -> bool:
        """Bổ sung mapping telegram_user_id cho confirmation cũ khi user đã được xác minh."""
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            cursor = conn.execute(
                "UPDATE pending_confirmations SET telegram_user_id = ? WHERE confirmation_id = ? AND telegram_user_id IS NULL",
                (telegram_user_id, confirmation_id.strip()),
            )
            conn.commit()
            return cursor.rowcount == 1

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

    def get_confirmation(self, identifier: str) -> Optional[Dict[str, Any]]:
        clean_id = identifier.strip()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM pending_confirmations WHERE confirmation_id = ? OR operation_id = ?",
                (clean_id, clean_id),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def validate_access(
        self,
        conf: Dict[str, Any],
        user_id: int,
        chat_id: int,
        telegram_binding_id: str,
        owner_key: Optional[str] = None,
    ) -> bool:
        """Kiểm tra quyền sở hữu confirmation: user_id, chat_id, telegram_binding_id và owner_key."""
        if conf.get("user_id") != user_id:
            return False
        if conf.get("chat_id") != chat_id:
            return False
        c_bind = conf.get("telegram_binding_id")
        if c_bind and telegram_binding_id and c_bind != "default" and telegram_binding_id != "default":
            if c_bind != telegram_binding_id:
                return False
        if conf.get("owner_key") and owner_key and conf.get("owner_key") != owner_key:
            return False
        return True

    def list_pending_confirmations(
        self,
        user_id: int,
        chat_id: int,
        telegram_binding_id: str,
        owner_key: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """Lấy danh sách các confirmation đang pending và chưa hết hạn của người dùng."""
        now_str = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM pending_confirmations
                WHERE user_id = ? AND chat_id = ? AND telegram_binding_id = ?
                  AND status = 'pending' AND expires_at > ?
                ORDER BY created_at DESC
                """,
                (user_id, chat_id, telegram_binding_id, now_str),
            )
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if owner_key and d.get("owner_key") and d["owner_key"] != owner_key:
                    continue
                results.append(d)
            return results

    def cancel_pending_draft_confirmations(
        self,
        user_id: int,
        chat_id: int,
        telegram_binding_id: str,
        draft_id: str,
    ) -> int:
        """Hủy toàn bộ pending confirmations liên kết với draft_id khi draft được cập nhật."""
        now_str = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            cursor = conn.execute(
                """
                UPDATE pending_confirmations
                SET status = 'cancelled',
                    error_message = 'Bản nháp đã được sửa sang phiên bản mới; bản xem trước cũ bị hủy.',
                    updated_at = ?
                WHERE user_id = ? AND chat_id = ? AND telegram_binding_id = ?
                  AND draft_id = ? AND status = 'pending'
                """,
                (now_str, user_id, chat_id, telegram_binding_id, draft_id),
            )
            conn.commit()
            return cursor.rowcount

    def recover_expired_leases(self) -> int:
        """Chuyển đổi trạng thái executing của các confirmation có lease NULL hoặc hết hạn sang unknown_result để đối soát.
        Recovery thực hiện atomically: executing hết lease -> unknown_result, đồng thời xóa execution token và lease cũ.
        """
        now_ts = datetime.now(UTC).timestamp()
        now_str = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            cursor = conn.execute(
                """
                UPDATE pending_confirmations
                SET status = 'unknown_result',
                    error_message = 'Lease thiếu hoặc đã hết hạn trước khi có phản hồi, cần đối soát Gateway',
                    execution_token = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE status = 'executing' AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                """,
                (now_str, now_ts),
            )
            count = cursor.rowcount
            conn.commit()
            return count

    def get_expired_confirmations_to_reconcile(self) -> list[Dict[str, Any]]:
        """Lấy danh sách các confirmation cần đối soát với Gateway (unknown_result hoặc executing đã quá hạn/thiếu lease).
        Chỉ lấy những bản ghi có operation_id và không phải local task."""
        now_ts = datetime.now(UTC).timestamp()
        with sqlite3.connect(self.db_path, timeout=20.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT confirmation_id, operation_id, user_id, chat_id, telegram_binding_id,
                       telegram_user_id, action, status, execution_token, lease_expires_at
                FROM pending_confirmations
                WHERE (status = 'unknown_result' OR (status = 'executing' AND (lease_expires_at IS NULL OR lease_expires_at < ?)))
                  AND operation_id IS NOT NULL
                  AND action NOT LIKE 'tasks.%'
                ORDER BY updated_at ASC
                LIMIT 20
                """,
                (now_ts,),
            )
            return [dict(r) for r in cursor.fetchall()]


confirmation_service = ConfirmationService()


async def reconcile_leases_with_gateway(gateway_client_instance=None, confirmation_service_instance=None) -> int:
    """Tác vụ nền kiểm tra mỗi 30 giây: chỉ GET trạng thái, chỉ xử lý lease đã hết hạn.
    Gateway xác nhận completed/failed thì đồng bộ kết quả; not_found hoặc không xác minh được giữ unknown.
    Dùng telegram_user_id đã lưu; confirmation thiếu telegram_user_id không được tự suy đoán."""
    from app.services.gateway_client import gateway_client
    gw = gateway_client_instance or gateway_client
    cs = confirmation_service_instance or confirmation_service

    # 1. Thu hồi lease executing quá hạn sang unknown_result
    cs.recover_expired_leases()

    # 2. Lấy danh sách cần đối soát
    items = cs.get_expired_confirmations_to_reconcile()
    updated_count = 0
    for item in items:
        tele_uid = item.get("telegram_user_id")
        if not tele_uid:
            # Confirmation thiếu telegram_user_id không tự suy đoán, giữ unknown_result
            continue
        try:
            res = await gw.get_action_status(
                user_id=item["user_id"],
                operation_id=item["operation_id"],
                telegram_user_id=tele_uid,
                chat_id=item["chat_id"],
                telegram_binding_id=item["telegram_binding_id"],
                timeout=5.0,
            )
            st = res.get("status")
            cid = item["confirmation_id"]
            if st in ["completed", "failed"]:
                if cs.reconcile_confirmation_result(
                    cid,
                    status=st,
                    result=res.get("result"),
                    error_message=res.get("message", "Thất bại trên Gateway") if st == "failed" else None,
                    operation_id=item["operation_id"],
                ):
                    updated_count += 1
            # not_found, in_progress, hoặc lỗi khác: GIỮ NGUYÊN unknown_result, không tự POST lại
        except Exception as e:
            logger.warning("Lỗi đối soát background cho confirmation %s: %s", item.get("confirmation_id"), e)

    return updated_count
