import pytest
from app.models.context import SkillExecutionContext, UserSessionContext
from app.skills.meetings.processing_skills import MeetingPreparationSkill, MeetingNotesProcessSkill, MeetingMinutesSkill

@pytest.fixture
def context():
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test"))

@pytest.mark.asyncio
async def test_meeting_preparation(context):
    skill = MeetingPreparationSkill()
    res = await skill.execute(context, meeting_id="m1")
    assert res["status"] == "SUCCESS"
    assert "meeting_summary" in res["preparation"]

@pytest.mark.asyncio
async def test_meeting_notes_processing(context):
    skill = MeetingNotesProcessSkill()
    transcript = "Đây là cuộc họp.\n" * 50 + "\nQuyết định: Chọn phương án A.\n" + "Deadline: 15/10 cho việc X."
    
    # Simple mock for ai_client
    class MockModels:
        def generate_content(self, model, contents, config):
            class MockResponse:
                text = '{"topics": [], "decisions": [{"decision": "Quyết định test", "source_excerpt": "Quyết định:"}], "action_items": []}'
            return MockResponse()
            
    class MockClient:
        models = MockModels()
        
    context.providers["ai_client"] = MockClient()
    
    res = await skill.execute(context, transcript=transcript)
    assert res["status"] == "SUCCESS"
    data = res["structured_data"]
    
    assert len(data["decisions"]) > 0
    
@pytest.mark.asyncio
async def test_meeting_minutes_generation(context):
    skill = MeetingMinutesSkill()
    processed = {
        "summary": "Tóm tắt test",
        "decisions": [{"decision": "Test decision"}],
        "action_items": [{"task": "Test task"}]
    }
    res = await skill.execute(context, meeting_id="m1", processed_notes=processed)
    assert res["status"] == "SUCCESS"
    md = res["markdown"]
    assert "Test decision" in md
    assert "Test task" in md
