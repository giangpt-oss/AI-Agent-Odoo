import pytest
from app.models.context import SkillExecutionContext, UserSessionContext
from app.skills.meetings.meeting_skills import MeetingCreateSkill, MeetingGetSkill, MeetingListSkill, MeetingUpdateSkill, MeetingCompleteSkill

@pytest.fixture
def context():
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test"))

@pytest.mark.asyncio
async def test_meeting_crud_flow(context):
    create_skill = MeetingCreateSkill()
    res = await create_skill.execute(context, title="Test Meeting", start_time_text="2026-10-15T15:00:00Z", participants=["John", "Doe"])
    assert res["status"] == "SUCCESS"
    m_id = res["meeting"]["id"]
    
    get_skill = MeetingGetSkill()
    res_get = await get_skill.execute(context, meeting_id=m_id)
    assert res_get["meeting"]["title"] == "Test Meeting"
    
    list_skill = MeetingListSkill()
    res_list = await list_skill.execute(context, title="Test")
    assert any(m["id"] == m_id for m in res_list)
    
    update_skill = MeetingUpdateSkill()
    res_upd = await update_skill.execute(context, meeting_id=m_id, updates={"status": "IN_PROGRESS"})
    assert res_upd["meeting"]["status"] == "IN_PROGRESS"
    
    complete_skill = MeetingCompleteSkill()
    res_comp = await complete_skill.execute(context, meeting_id=m_id)
    assert res_comp["meeting"]["status"] == "COMPLETED"
