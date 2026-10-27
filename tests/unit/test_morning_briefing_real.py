import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from app.domain.employee import EmployeeProfile
from app.services.morning_briefing_service import MorningBriefingService


@pytest.fixture
def mock_gateway():
    client = MagicMock()
    client.get_calendar_events = AsyncMock()
    client.get_unread_emails = AsyncMock()
    return client


@pytest.fixture
def mock_tasks():
    provider = MagicMock()
    provider.list_tasks = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_morning_briefing_deterministic_time_and_tz_classification(mock_gateway, mock_tasks):
    # Fixed test time: 2026-10-26 10:00:00 in Asia/Bangkok (+07:00)
    tz_bkk = ZoneInfo("Asia/Bangkok")
    fixed_now = datetime(2026, 10, 26, 10, 0, 0, tzinfo=tz_bkk)

    user_info = {
        "user_id": 1,
        "name": "Nguyễn Văn A",
        "tz": "Asia/Bangkok",
        "tenant": "corp.odoo.com",
        "database": "db1",
        "odoo_uid": 1,
    }

    mock_gateway.get_calendar_events.return_value = {"status": "OK", "events": []}
    mock_gateway.get_unread_emails.return_value = {"status": "OK", "total_unread": 2, "messages": []}

    # Task 1: Due at 2026-10-26 17:30 UTC -> In BKK (+7) this is 2026-10-27 00:30 (Tomorrow!)
    # Slicing due_at[:10] would mistakenly think 2026-10-26 (today)
    task_tomorrow = {
        "id": "t1",
        "title": "Việc ngày mai UTC+7",
        "status": "TODO",
        "due_at": "2026-10-26T17:30:00Z",
    }
    # Task 2: Due at 2026-10-26 05:00 UTC -> In BKK (+7) this is 2026-10-26 12:00 (Today!)
    task_today = {
        "id": "t2",
        "title": "Việc trưa nay",
        "status": "TODO",
        "due_at": "2026-10-26T05:00:00Z",
    }
    # Task 3: Overdue
    task_overdue = {
        "id": "t3",
        "title": "Việc hôm qua quá hạn",
        "status": "TODO",
        "due_at": "2026-10-25T05:00:00Z",
    }
    # Task 4: Completed task should be excluded
    task_done = {
        "id": "t4",
        "title": "Việc đã xong",
        "status": "DONE",
        "due_at": "2026-10-26T05:00:00Z",
    }

    mock_tasks.list_tasks.return_value = [task_tomorrow, task_today, task_overdue, task_done]

    service = MorningBriefingService(task_provider=mock_tasks)
    result = await service.generate_real_briefing(user_info, client=mock_gateway, now_dt=fixed_now)

    # Assertions
    assert "26/10/2026" in result
    assert "Đến hạn hôm nay" in result
    assert "Việc trưa nay" in result
    assert "Quá hạn (1 việc)" in result
    assert "Việc hôm qua quá hạn" in result
    # Tomorrow's task should not be in today's section
    assert "Việc ngày mai UTC+7" not in result
    # Done task excluded
    assert "Việc đã xong" not in result


@pytest.mark.asyncio
async def test_morning_briefing_task_error_distinct_from_empty(mock_gateway, mock_tasks):
    user_info = {
        "user_id": 1,
        "name": "Nguyễn Văn A",
        "tz": "Asia/Bangkok",
    }
    mock_gateway.get_calendar_events.return_value = {"status": "OK", "events": []}
    mock_gateway.get_unread_emails.return_value = {"status": "OK", "total_unread": 0, "messages": []}

    # Provider list_tasks fails with exception
    mock_tasks.list_tasks.side_effect = RuntimeError("Database locked")

    service = MorningBriefingService(task_provider=mock_tasks)
    result = await service.generate_real_briefing(user_info, client=mock_gateway)

    assert "Không thể tải danh sách công việc" in result
    assert "không có công việc nào chưa hoàn thành" not in result


@pytest.mark.asyncio
async def test_morning_briefing_gmail_undetermined_total(mock_gateway, mock_tasks):
    user_info = {
        "user_id": 1,
        "name": "Nguyễn Văn A",
        "tz": "Asia/Bangkok",
    }
    mock_gateway.get_calendar_events.return_value = {"status": "OK", "events": []}
    mock_tasks.list_tasks.return_value = []

    # Gmail unread total is undetermined (None)
    mock_gateway.get_unread_emails.return_value = {
        "status": "OK",
        "total_unread": None,
        "messages": [{"id": "1", "from": "boss@co.com", "subject": "Test", "link": ""}],
    }

    service = MorningBriefingService(task_provider=mock_tasks)
    result = await service.generate_real_briefing(user_info, client=mock_gateway)

    assert "chưa xác định" in result
    assert "boss@co.com" in result


@pytest.mark.asyncio
async def test_morning_briefing_partial_failure_resilience(mock_gateway, mock_tasks):
    user_info = {
        "user_id": 1,
        "name": "Nguyễn Văn A",
        "tz": "Asia/Bangkok",
    }
    # Calendar timed out
    mock_gateway.get_calendar_events.return_value = {"status": "TIMEOUT", "events": []}
    # Gmail succeeded
    mock_gateway.get_unread_emails.return_value = {"status": "OK", "total_unread": 3, "messages": []}
    # Tasks succeeded
    mock_tasks.list_tasks.return_value = []

    service = MorningBriefingService(task_provider=mock_tasks)
    result = await service.generate_real_briefing(user_info, client=mock_gateway)

    # Calendar shows error
    assert "Không thể tải lịch Google" in result
    # Gmail still shows success
    assert "3" in result
    # Tasks still shows success
    assert "không có công việc nào chưa hoàn thành" in result


@pytest.mark.asyncio
async def test_legacy_briefing_removes_mock_data():
    service = MorningBriefingService()
    profile = EmployeeProfile(
        user_id="test",
        full_name="Nguyễn Văn A",
        job_title="Trưởng phòng Kinh doanh",
        department="Kinh doanh",
        company_email="a@example.com",
        roles=["sales_manager"],
    )

    # Without a live Odoo client, legacy briefing must NOT report fake numbers (like "7 cơ hội" or "2 báo giá lớn")
    res = await service.generate_briefing(profile, odoo_client=None)
    text = res.to_markdown()
    assert "7 cơ hội" not in text
    assert "2 báo giá lớn" not in text
    assert "Chưa kết nối dữ liệu" in text or "chưa có kết nối" in text.lower()
