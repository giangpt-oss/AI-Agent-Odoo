import pytest
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta
from app.services.scheduler import scheduler_service
from app.providers.reminders.local import LocalReminderProvider
from app.services.file_service import file_service
from pathlib import Path
import os

@pytest.fixture
def clean_db():
    db_path = Path(file_service.workspace_root) / "test_reminders.db"
    if db_path.exists():
        try:
            os.remove(db_path)
        except Exception:
            pass
    # Ensure provider initializes DB
    LocalReminderProvider(db_path)
    yield db_path
    if db_path.exists():
        try:
            os.remove(db_path)
        except Exception:
            pass

@pytest.mark.asyncio
async def test_scheduler_triggers_due_reminders(clean_db):
    provider = LocalReminderProvider(clean_db, user_id="test", chat_id=123)
    from app.services.scheduler import SchedulerService
    test_scheduler = SchedulerService(clean_db)
    
    # Create reminder in the past
    past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    rem = await provider.create_reminder(None, "Past reminder", past_time, "UTC", None)
    
    from unittest.mock import AsyncMock
    test_scheduler.notification_provider.send_notification = AsyncMock(return_value=True)
    # Run one pass of scheduler check
    await test_scheduler._check_and_trigger_reminders()
    
    # Check status
    with sqlite3.connect(clean_db) as conn:
        cursor = conn.execute("SELECT status FROM reminders WHERE id = ?", (rem["id"],))
        status = cursor.fetchone()[0]
        
    assert status == "TRIGGERED"
    test_scheduler.notification_provider.send_notification.assert_awaited_once_with("123", "🔔 LỜI NHẮC:\nPast reminder") 
    # Usually it will be TRIGGERED or RETRYING depending on Telegram mock
