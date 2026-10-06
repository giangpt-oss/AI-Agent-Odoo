import pytest
from app.models.context import SkillExecutionContext, UserSessionContext
from app.skills.notes.note_skills import NoteCreateSkill, NoteGetSkill, NoteListSkill, NoteSearchSkill, NoteUpdateSkill

@pytest.fixture
def context():
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test"))

@pytest.mark.asyncio
async def test_note_crud_flow(context):
    create_skill = NoteCreateSkill()
    res = await create_skill.execute(context, title="Test Note", content="# Heading\nContent here", note_type="GENERAL")
    assert res["status"] == "SUCCESS"
    n_id = res["note"]["id"]
    
    get_skill = NoteGetSkill()
    res_get = await get_skill.execute(context, note_id=n_id)
    assert res_get["note"]["content"].startswith("# Heading")
    
    list_skill = NoteListSkill()
    res_list = await list_skill.execute(context, note_type="GENERAL")
    assert any(n["id"] == n_id for n in res_list)
    
    search_skill = NoteSearchSkill()
    res_search = await search_skill.execute(context, keyword="Heading")
    assert any(n["id"] == n_id for n in res_search)
    
    update_skill = NoteUpdateSkill()
    res_upd = await update_skill.execute(context, note_id=n_id, updates={"title": "Updated Note"})
    assert res_upd["note"]["title"] == "Updated Note"
