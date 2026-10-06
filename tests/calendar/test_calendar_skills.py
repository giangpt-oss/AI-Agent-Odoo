import pytest
from app.models.context import SkillExecutionContext, UserSessionContext
from app.skills.calendar.calendar_skills import CalendarFindFreeTimeSkill, CalendarCreateEventSkill
from app.agent.confirmation_manager import confirmation_manager
from app.providers.calendar.fake_calendar import FakeCalendarProvider
import app.skills.calendar.calendar_skills as calendar_skills_module
from datetime import datetime, timedelta

@pytest.fixture
def fake_provider():
    return FakeCalendarProvider()

@pytest.fixture
def context(fake_provider, monkeypatch):
    monkeypatch.setattr(calendar_skills_module, 'get_calendar_provider', lambda c: fake_provider)
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test", chat_id=123))

@pytest.mark.asyncio
async def test_calendar_find_free_time(context):
    skill = CalendarFindFreeTimeSkill()
    now = datetime.now()
    start = now.isoformat() + "Z"
    end = (now + timedelta(days=1)).isoformat() + "Z"
    
    res = await skill.execute(context, start_time=start, end_time=end, duration_minutes=60)
    assert len(res) == 1
    assert "start" in res[0]

@pytest.mark.asyncio
async def test_calendar_create_event_flow(context, fake_provider):
    skill = CalendarCreateEventSkill()
    now = datetime.now()
    args = {
        "title": "Test meeting",
        "start_time": now.isoformat() + "Z",
        "end_time": (now + timedelta(hours=1)).isoformat() + "Z",
        "timezone": "Asia/Ho_Chi_Minh",
        "attendees": ["test@company.com"]
    }
    
    initial_event_count = len(fake_provider.events)
    
    preview_data = await skill.preview(context, **args)
    record = confirmation_manager.create_request(skill.name, args, preview_data, context)
    
    # Send approve
    confirmation_manager.approve_confirmation(record.id, user_id="test", chat_id=123)
    is_approved = confirmation_manager.check_and_consume_approval(skill.name, args, confirmation_id=record.id, user_id="test", chat_id=123)
    assert is_approved == True
    
    res = await skill.execute(context, **args)
    assert res["status"] == "SUCCESS"
    assert len(fake_provider.events) == initial_event_count + 1
