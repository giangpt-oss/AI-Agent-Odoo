import pytest
import sqlite3
import threading
from pathlib import Path
from app.services.confirmation_service import ConfirmationService


def test_concurrent_claim_only_one_succeeds(tmp_path):
    db_path = tmp_path / "tasks.db"
    service = ConfirmationService(db_path=db_path)

    cid, preview = service.create_confirmation(
        user_id=1,
        chat_id=123,
        telegram_binding_id="tg_bind_1",
        action="tasks.create",
        arguments={"title": "Test Atomic Claim"},
        preview_text="Preview text",
    )

    results = []

    def try_claim():
        ok, msg, data = service.claim_and_validate(
            confirmation_id=cid,
            user_id=1,
            chat_id=123,
            telegram_binding_id="tg_bind_1",
        )
        results.append((ok, msg, data))

    threads = [threading.Thread(target=try_claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    success_count = sum(1 for ok, _, _ in results if ok)
    fail_count = sum(1 for ok, _, _ in results if not ok)

    assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
    assert fail_count == 1, f"Expected exactly 1 failure, got {fail_count}"

    # Verify status in database is now 'executing'
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT status FROM pending_confirmations WHERE confirmation_id = ?", (cid,)).fetchone()
        assert row[0] == "executing"


def test_operation_id_persisted_from_preview(tmp_path):
    db_path = tmp_path / "tasks.db"
    service = ConfirmationService(db_path=db_path)

    cid, _ = service.create_confirmation(
        user_id=2,
        chat_id=456,
        telegram_binding_id="tg_bind_2",
        action="calendar.create",
        arguments={"summary": "Meeting"},
        preview_text="Preview",
    )

    ok, _, data = service.claim_and_validate(
        confirmation_id=cid,
        user_id=2,
        chat_id=456,
        telegram_binding_id="tg_bind_2",
    )
    assert ok is True
    assert "operation_id" in data
    assert data["operation_id"].startswith("op_")


def test_lifecycle_transitions(tmp_path):
    db_path = tmp_path / "tasks.db"
    service = ConfirmationService(db_path=db_path)

    cid, _ = service.create_confirmation(
        user_id=3,
        chat_id=789,
        telegram_binding_id="tg_bind_3",
        action="tasks.create",
        arguments={"title": "Lifecycle test"},
        preview_text="Preview",
    )

    # 1. pending -> executing
    ok, _, data = service.claim_and_validate(cid, 3, 789, "tg_bind_3")
    assert ok is True

    # 2. Cannot claim again while executing
    ok2, msg2, _ = service.claim_and_validate(cid, 3, 789, "tg_bind_3")
    assert ok2 is False
    assert "đang được thực thi" in msg2 or "executing" in msg2.lower()

    # 3. executing -> failed
    service.mark_failed(cid, "Network connection error")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT status, error_message FROM pending_confirmations WHERE confirmation_id = ?", (cid,)).fetchone()
        assert row[0] == "failed"
        assert row[1] == "Network connection error"

    # 4. Cannot claim failed confirmation
    ok3, msg3, _ = service.claim_and_validate(cid, 3, 789, "tg_bind_3")
    assert ok3 is False


def test_local_task_transaction_atomic_rollback(tmp_path):
    """Crash/lỗi giữa chừng trong transaction task không để lại dữ liệu ghi dở."""
    db_path = tmp_path / "tasks.db"
    service = ConfirmationService(db_path=db_path)

    cid, _ = service.create_confirmation(
        user_id=1,
        chat_id=100,
        telegram_binding_id="tg_bind_tx",
        action="tasks.create",
        arguments={"title": "Atomic Rollback Task"},
        preview_text="Preview",
    )

    ok, _, data = service.claim_and_validate(cid, 1, 100, "tg_bind_tx")
    assert ok is True

    # Simulate an atomic transaction that fails halfway
    with pytest.raises(RuntimeError):
        with service.get_connection() as conn:
            # Step 1: create task
            conn.execute(
                "INSERT INTO tasks (id, title, status, priority, created_at, updated_at, owner_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("task_fail_1", "Atomic Rollback Task", "TODO", "medium", "2026-10-26T10:00:00Z", "2026-10-26T10:00:00Z", "user:1"),
            )
            # Step 2: crash occurs before mark_completed
            raise RuntimeError("Database or process crash halfway through task write")

    # Verify task was rolled back
    with sqlite3.connect(db_path) as conn:
        task_row = conn.execute("SELECT * FROM tasks WHERE id = 'task_fail_1'").fetchone()
        assert task_row is None, "Task should have been rolled back"


def test_migration_legacy_records(tmp_path):
    """Migration chuyển các xác nhận cũ: pending giữ nguyên, confirmed cũ chuyển thành unknown_result."""
    db_path = tmp_path / "tasks.db"
    with sqlite3.connect(db_path) as conn:
        # Giả lập bảng legacy
        conn.execute("""
            CREATE TABLE pending_confirmations (
                confirmation_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                telegram_binding_id TEXT NOT NULL,
                owner_key TEXT,
                action TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                target_snapshot_json TEXT,
                preview_text TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO pending_confirmations VALUES ('c_old_1', 1, 1, 'bind', 'k', 'act', '{}', NULL, 'p', 'confirmed', '2026-10-26T00:00:00Z', '2026-10-26T01:00:00Z')")
        conn.execute("INSERT INTO pending_confirmations VALUES ('c_old_2', 1, 1, 'bind', 'k', 'act', '{}', NULL, 'p', 'pending', '2026-10-26T00:00:00Z', '2026-10-26T23:59:59Z')")
        conn.commit()

    # Initialize service and trigger migration
    service = ConfirmationService(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        r1 = conn.execute("SELECT status FROM pending_confirmations WHERE confirmation_id = 'c_old_1'").fetchone()
        # Bản ghi confirmed cũ không có bằng chứng kết quả chuyển thành unknown_result
        assert r1[0] == "unknown_result"

        r2 = conn.execute("SELECT status FROM pending_confirmations WHERE confirmation_id = 'c_old_2'").fetchone()
        assert r2[0] == "pending"
