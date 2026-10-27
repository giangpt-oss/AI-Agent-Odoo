import sqlite3
import json
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, UTC
from app.providers.tasks.base import TaskProvider
from app.services.file_service import file_service
from pathlib import Path

class LocalTaskProvider(TaskProvider):
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (file_service.data_dir / "tasks.db")
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
                    updated_at TEXT NOT NULL,
                    owner_key TEXT,
                    revision INTEGER NOT NULL DEFAULT 1
                )
            """)
            cursor = conn.execute("PRAGMA table_info(tasks)")
            cols = [r[1] for r in cursor.fetchall()]
            if "owner_key" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN owner_key TEXT")
            if "revision" not in cols:
                conn.execute("ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")

            # SQLite trigger tự tăng revision khi các trường nghiệp vụ thay đổi:
            # title, description, status, priority, due_at, timezone, project, tags
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_tasks_revision_on_update
                AFTER UPDATE ON tasks
                FOR EACH ROW
                WHEN (
                    OLD.title IS NOT NEW.title OR
                    OLD.description IS NOT NEW.description OR
                    OLD.status IS NOT NEW.status OR
                    OLD.priority IS NOT NEW.priority OR
                    OLD.due_at IS NOT NEW.due_at OR
                    OLD.timezone IS NOT NEW.timezone OR
                    OLD.project IS NOT NEW.project OR
                    OLD.tags IS NOT NEW.tags
                )
                BEGIN
                    UPDATE tasks
                    SET revision = OLD.revision + 1
                    WHERE id = OLD.id AND revision = OLD.revision;
                END;
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
            "updated_at": row[10],
            "owner_key": row[11] if len(row) > 11 else None,
            "revision": row[12] if len(row) > 12 else 1,
        }

    async def create_task(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        due_at: Optional[str] = None,
        timezone: str = "Asia/Ho_Chi_Minh",
        project: Optional[str] = None,
        tags: Optional[List[str]] = None,
        owner_key: Optional[str] = None,
        due_date: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        actual_due = due_at or due_date
        actual_tags = tags if tags is not None else []
        def _exec():
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            now = datetime.now().isoformat()
            tags_json = json.dumps(actual_tags)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO tasks (id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (task_id, title, description, "TODO", priority, actual_due, timezone, project, tags_json, now, now, owner_key)
                )
                conn.commit()
            return self._get_task_sync(task_id, owner_key=owner_key)
        return await asyncio.to_thread(_exec)

    def _get_task_sync(self, task_id: str, owner_key: Optional[str] = None) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            if owner_key:
                cursor = conn.execute(
                    "SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key, revision FROM tasks WHERE id = ? AND owner_key = ?",
                    (task_id, owner_key),
                )
            else:
                cursor = conn.execute(
                    "SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key, revision FROM tasks WHERE id = ? AND (owner_key IS NULL OR owner_key = '')",
                    (task_id,),
                )
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row)
            raise KeyError(f"Task {task_id} not found or access denied")

    async def get_task(self, task_id: str, owner_key: Optional[str] = None) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_task_sync, task_id, owner_key)

    async def list_tasks(self, filters: Dict[str, Any], owner_key: Optional[str] = None) -> List[Dict[str, Any]]:
        def _exec():
            query = "SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key, revision FROM tasks WHERE 1=1"
            params = []
            if "status" in filters:
                query += " AND status = ?"
                params.append(filters["status"])
            if "priority" in filters:
                query += " AND priority = ?"
                params.append(filters["priority"])

            # Enforce owner scope at provider level
            if owner_key:
                query += " AND owner_key = ?"
                params.append(owner_key)
            else:
                query += " AND (owner_key IS NULL OR owner_key = '')"
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(query, params)
                return [self._row_to_dict(row) for row in cursor.fetchall()]
        return await asyncio.to_thread(_exec)

    async def update_task(
        self,
        task_id: str,
        updates: Optional[Dict[str, Any]] = None,
        owner_key: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        actual_updates = dict(updates or {})
        for k, v in kwargs.items():
            if k not in actual_updates:
                actual_updates[k] = v
        if "due_date" in actual_updates and "due_at" not in actual_updates:
            actual_updates["due_at"] = actual_updates.pop("due_date")

        def _exec():
            # Verify existence and ownership
            self._get_task_sync(task_id, owner_key=owner_key)

            fields = []
            params = []
            for k, v in actual_updates.items():
                if k in ["title", "description", "status", "priority", "due_at", "timezone", "project"]:
                    fields.append(f"{k} = ?")
                    params.append(v)
                elif k == "tags":
                    fields.append("tags = ?")
                    params.append(json.dumps(v))
            if fields:
                fields.append("updated_at = ?")
                params.append(datetime.now().isoformat())
                
                if owner_key:
                    query = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND owner_key = ?"
                    params.extend([task_id, owner_key])
                else:
                    query = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND (owner_key IS NULL OR owner_key = '')"
                    params.append(task_id)

                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(query, params)
                    conn.commit()
            return self._get_task_sync(task_id, owner_key=owner_key)
        return await asyncio.to_thread(_exec)

    async def complete_task(self, task_id: str, owner_key: Optional[str] = None) -> Dict[str, Any]:
        return await self.update_task(task_id, {"status": "DONE"}, owner_key=owner_key)

    async def delete_task(self, task_id: str, owner_key: Optional[str] = None) -> bool:
        def _exec():
            with sqlite3.connect(self.db_path) as conn:
                if owner_key:
                    cursor = conn.execute("DELETE FROM tasks WHERE id = ? AND owner_key = ?", (task_id, owner_key))
                else:
                    cursor = conn.execute("DELETE FROM tasks WHERE id = ? AND (owner_key IS NULL OR owner_key = '')", (task_id,))
                conn.commit()
                return cursor.rowcount > 0
        return await asyncio.to_thread(_exec)


def execute_task_confirmation_atomically(
    db_path: Path,
    confirmation_id: str,
    execution_token: Optional[str],
    action: str,
    arguments: Dict[str, Any],
    owner_key: Optional[str],
    target_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Thực thi thao tác task và cập nhật confirmation completed trong CÙNG TRANSACTION SQLite.
    Nếu bất kỳ bước nào lỗi hoặc rowcount != 1, rollback toàn bộ."""
    clean_id = confirmation_id.strip()
    norm_action = action.lower()

    # Ánh xạ tên action cũ sang canonical name
    if norm_action in ["tasks.create", "tasks.personal.create"]:
        op_type = "create"
    elif norm_action in ["tasks.update", "tasks.personal.update"]:
        op_type = "update"
    elif norm_action in ["tasks.done", "tasks.complete", "tasks.personal.done", "tasks.personal.complete"]:
        op_type = "done"
    else:
        raise ValueError(f"Hành động task không hợp lệ: {action}")

    # Chặn bản ghi cũ thiếu owner_key cần thiết
    if not owner_key:
        raise PermissionError("Bản ghi thao tác thiếu thông tin người sở hữu (owner_key). Vui lòng tạo yêu cầu xác nhận mới.")

    now_iso = datetime.now().isoformat()
    now_utc_str = datetime.now(UTC).isoformat()
    now_ts = datetime.now(UTC).timestamp()

    with sqlite3.connect(db_path, timeout=20.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 1. Xác minh confirmation đang ở executing và token khớp, lease chưa hết hạn
            cur = conn.execute(
                "SELECT confirmation_id, execution_token, status, owner_key, lease_expires_at FROM pending_confirmations WHERE confirmation_id = ?",
                (clean_id,),
            )
            row = cur.fetchone()
            if not row:
                raise KeyError(f"Mã xác nhận {clean_id} không tồn tại")
            c_status = row[2]
            c_tok = row[1]
            c_owner = row[3]
            c_lease = row[4]
            if c_status != "executing":
                raise RuntimeError(f"Mã xác nhận {clean_id} không ở trạng thái executing (hiện tại: {c_status})")
            if not execution_token or not c_tok or c_tok != execution_token:
                raise PermissionError("Execution token không hợp lệ hoặc không khớp, thao tác có thể đã bị worker khác xử lý")
            if c_lease is not None and c_lease < now_ts:
                raise PermissionError("Lease của thao tác đã hết hạn, không thể thực thi tiếp")
            if c_owner and c_owner != owner_key:
                raise PermissionError("Owner key không khớp với bản xác nhận ban đầu")

            # 2. Thực hiện mutation trên bảng tasks
            task_result = {}
            if op_type == "create":
                task_id = f"task_{uuid.uuid4().hex[:8]}"
                title = arguments.get("title") or "Công việc mới"
                description = arguments.get("description") or ""
                priority = arguments.get("priority") or "medium"
                due_at = arguments.get("due_at") or arguments.get("due_date")
                timezone = arguments.get("timezone") or "Asia/Ho_Chi_Minh"
                project = arguments.get("project")
                tags = arguments.get("tags") or []
                tags_json = json.dumps(tags)

                cur_ins = conn.execute(
                    """
                    INSERT INTO tasks (
                        id, title, description, status, priority, due_at, timezone,
                        project, tags, created_at, updated_at, owner_key, revision
                    ) VALUES (?, ?, ?, 'TODO', ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (task_id, title, description, priority, due_at, timezone, project, tags_json, now_iso, now_iso, owner_key),
                )
                if cur_ins.rowcount != 1:
                    raise RuntimeError("Không thể thêm bản ghi task mới vào cơ sở dữ liệu")
                task_result = {
                    "id": task_id,
                    "title": title,
                    "description": description,
                    "status": "TODO",
                    "priority": priority,
                    "due_at": due_at,
                    "timezone": timezone,
                    "project": project,
                    "tags": tags,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "owner_key": owner_key,
                    "revision": 1,
                }

            elif op_type == "update":
                task_id = arguments.get("task_id") or arguments.get("id")
                if not task_id:
                    raise ValueError("Thiếu mã công việc (task_id) để cập nhật")

                expected_revision = target_snapshot.get("revision") if isinstance(target_snapshot, dict) else None

                # Kiểm tra task tồn tại, đúng owner và đúng revision trong cùng transaction
                cur_chk = conn.execute(
                    "SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key, revision FROM tasks WHERE id = ? AND owner_key = ?",
                    (task_id, owner_key),
                )
                task_row = cur_chk.fetchone()
                if not task_row:
                    raise KeyError(f"Công việc {task_id} không tồn tại hoặc bạn không có quyền sở hữu")

                curr_revision = task_row[12]
                if expected_revision is not None and curr_revision != expected_revision:
                    raise ValueError("Công việc đã thay đổi sau bản xem trước, cần tạo yêu cầu mới")

                # Áp dụng các thay đổi
                updates = arguments.get("updates") or {}
                for k in ["title", "description", "status", "priority", "due_at", "timezone", "project"]:
                    if k in arguments and k not in updates:
                        updates[k] = arguments[k]
                if "due_date" in arguments and "due_at" not in updates:
                    updates["due_at"] = arguments["due_date"]
                if "tags" in arguments and "tags" not in updates:
                    updates["tags"] = arguments["tags"]
                if "status" in updates and updates["status"]:
                    updates["status"] = str(updates["status"]).upper()

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
                    params.append(now_iso)
                    if expected_revision is not None:
                        params.extend([task_id, owner_key, expected_revision])
                        cur_upd = conn.execute(
                            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND owner_key = ? AND revision = ?",
                            params,
                        )
                    else:
                        params.extend([task_id, owner_key])
                        cur_upd = conn.execute(
                            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND owner_key = ?",
                            params,
                        )
                    if cur_upd.rowcount != 1:
                        raise ValueError("Công việc đã thay đổi sau bản xem trước, cần tạo yêu cầu mới")

                cur_ref = conn.execute(
                    "SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key, revision FROM tasks WHERE id = ? AND owner_key = ?",
                    (task_id, owner_key),
                )
                ref_row = cur_ref.fetchone()
                task_result = {
                    "id": ref_row[0],
                    "title": ref_row[1],
                    "description": ref_row[2],
                    "status": ref_row[3],
                    "priority": ref_row[4],
                    "due_at": ref_row[5],
                    "timezone": ref_row[6],
                    "project": ref_row[7],
                    "tags": json.loads(ref_row[8]) if ref_row[8] else [],
                    "created_at": ref_row[9],
                    "updated_at": ref_row[10],
                    "owner_key": ref_row[11],
                    "revision": ref_row[12],
                }

            elif op_type == "done":
                task_id = arguments.get("task_id") or arguments.get("id")
                if not task_id:
                    raise ValueError("Thiếu mã công việc (task_id) để hoàn thành")

                expected_revision = target_snapshot.get("revision") if isinstance(target_snapshot, dict) else None

                cur_chk = conn.execute(
                    "SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key, revision FROM tasks WHERE id = ? AND owner_key = ?",
                    (task_id, owner_key),
                )
                task_row = cur_chk.fetchone()
                if not task_row:
                    raise KeyError(f"Công việc {task_id} không tồn tại hoặc bạn không có quyền sở hữu")

                curr_revision = task_row[12]
                if expected_revision is not None and curr_revision != expected_revision:
                    raise ValueError("Công việc đã thay đổi sau bản xem trước, cần tạo yêu cầu mới")

                if expected_revision is not None:
                    cur_upd = conn.execute(
                        "UPDATE tasks SET status = 'DONE', updated_at = ? WHERE id = ? AND owner_key = ? AND revision = ?",
                        (now_iso, task_id, owner_key, expected_revision),
                    )
                else:
                    cur_upd = conn.execute(
                        "UPDATE tasks SET status = 'DONE', updated_at = ? WHERE id = ? AND owner_key = ?",
                        (now_iso, task_id, owner_key),
                    )
                if cur_upd.rowcount != 1:
                    raise ValueError("Công việc đã thay đổi sau bản xem trước, cần tạo yêu cầu mới")

                cur_ref = conn.execute(
                    "SELECT id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key, revision FROM tasks WHERE id = ? AND owner_key = ?",
                    (task_id, owner_key),
                )
                ref_row = cur_ref.fetchone()
                task_result = {
                    "id": ref_row[0],
                    "title": ref_row[1],
                    "description": ref_row[2],
                    "status": "DONE",
                    "priority": ref_row[4],
                    "due_at": ref_row[5],
                    "timezone": ref_row[6],
                    "project": ref_row[7],
                    "tags": json.loads(ref_row[8]) if ref_row[8] else [],
                    "created_at": ref_row[9],
                    "updated_at": now_iso,
                    "owner_key": owner_key,
                    "revision": ref_row[12],
                }

            # 3. Đánh dấu completed trên confirmation TRONG CÙNG TRANSACTION
            # Yêu cầu đúng token, status 'executing' và xóa token/lease
            res_json = json.dumps(task_result, ensure_ascii=False)
            cur_conf = conn.execute(
                """
                UPDATE pending_confirmations
                SET status = 'completed', result_json = ?, updated_at = ?,
                    execution_token = NULL, lease_expires_at = NULL
                WHERE confirmation_id = ? AND status = 'executing'
                  AND execution_token = ?
                  AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                """,
                (res_json, now_utc_str, clean_id, execution_token, now_ts),
            )
            if cur_conf.rowcount != 1:
                raise RuntimeError(f"Không thể ghi nhận trạng thái hoàn thành cho mã {clean_id} (rowcount != 1, token hoặc lease không hợp lệ)")

            conn.commit()
            return task_result

        except Exception as e:
            conn.rollback()
            raise e
