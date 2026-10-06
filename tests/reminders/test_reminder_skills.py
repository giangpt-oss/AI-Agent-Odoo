import pytest
from app.models.context import SkillExecutionContext, UserSessionContext
from app.skills.reminders.reminder_skills import ReminderCreateSkill, ReminderListSkill, ReminderUpdateSkill, ReminderCancelSkill

@pytest.fixture
def context():
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test", chat_id=123))

@pytest.mark.asyncio
async def test_reminder_crud_flow(context):
    # Create
    create_skill = ReminderCreateSkill()
    res = await create_skill.execute(context, title="Test reminder", time_text="2026-10-15T10:00:00Z")
    assert res["status"] == "SUCCESS"
    rem_id = res["reminder"]["id"]
    assert res["reminder"]["status"] == "SCHEDULED"
    
    # List
    list_skill = ReminderListSkill()
    res_list = await list_skill.execute(context, status="SCHEDULED")
    assert any(r["id"] == rem_id for r in res_list)
    
    # Update
    update_skill = ReminderUpdateSkill()
    res_update = await update_skill.execute(context, reminder_id=rem_id, updates={"title": "Updated", "time_text": "2026-10-16T10:00:00Z"})
    assert res_update["reminder"]["title"] == "Updated"
    
    # Cancel
    cancel_skill = ReminderCancelSkill()
    res_cancel = await cancel_skill.execute(context, reminder_id=rem_id)
    assert res_cancel["status"] == "SUCCESS"
    
    res_list2 = await list_skill.execute(context, status="CANCELLED")
    assert any(r["id"] == rem_id for r in res_list2)
