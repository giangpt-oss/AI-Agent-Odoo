import pytest
from unittest.mock import AsyncMock, patch
from app.services.morning_briefing_service import MorningBriefingService


@pytest.mark.asyncio
async def test_today_briefing_skips_tasks_when_tasks_read_permission_missing():
    service = MorningBriefingService()
    user_info = {
        "user_id": 1,
        "tenant": "https://erp.com",
        "database": "db",
        "odoo_uid": 101,
        "name": "Test User",
        "tz": "Asia/Bangkok",
        "permissions": ["calendar.read", "gmail.read"],  # No tasks.read
    }
    with patch("app.services.gateway_client.gateway_client.get_calendar_events", new_callable=AsyncMock) as mock_cal, \
         patch("app.services.gateway_client.gateway_client.get_unread_emails", new_callable=AsyncMock) as mock_mail, \
         patch("app.providers.tasks.local.LocalTaskProvider.list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_cal.return_value = {"status": "OK", "events": []}
        mock_mail.return_value = {"status": "OK", "total_unread": 0, "messages": []}

        briefing = await service.generate_real_briefing(user_info)
        # Should NOT call local tasks list when tasks.read is not granted
        mock_tasks.assert_not_called()
        assert "tasks.read" in briefing or "quyền đọc công việc" in briefing


@pytest.mark.asyncio
async def test_today_briefing_loads_tasks_when_tasks_read_permission_present():
    service = MorningBriefingService()
    user_info = {
        "user_id": 1,
        "tenant": "https://erp.com",
        "database": "db",
        "odoo_uid": 101,
        "name": "Test User",
        "tz": "Asia/Bangkok",
        "permissions": ["tasks.read", "tasks.write"],
    }
    with patch("app.services.gateway_client.gateway_client.get_calendar_events", new_callable=AsyncMock) as mock_cal, \
         patch("app.services.gateway_client.gateway_client.get_unread_emails", new_callable=AsyncMock) as mock_mail, \
         patch("app.providers.tasks.local.LocalTaskProvider.list_tasks", new_callable=AsyncMock) as mock_tasks:
        mock_cal.return_value = {"status": "OK", "events": []}
        mock_mail.return_value = {"status": "OK", "total_unread": 0, "messages": []}
        mock_tasks.return_value = [{"title": "Soạn thảo báo cáo", "status": "TODO"}]

        briefing = await service.generate_real_briefing(user_info)
        mock_tasks.assert_called_once()
        assert "đang tiến hành" in briefing or "Soạn thảo báo cáo" in briefing
