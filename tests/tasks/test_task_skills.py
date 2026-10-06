import pytest
from app.models.context import SkillExecutionContext, UserSessionContext
from app.skills.tasks.task_skills import TaskCreateSkill, TaskListSkill, TaskUpdateSkill, TaskCompleteSkill, TaskDeleteSkill
from app.agent.confirmation_manager import confirmation_manager

@pytest.fixture
def context():
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test", chat_id=123))

@pytest.mark.asyncio
async def test_task_crud_flow(context):
    # Create
    create_skill = TaskCreateSkill()
    res = await create_skill.execute(context, title="Test task", due_text="2026-10-15T10:00:00Z")
    assert res["status"] == "SUCCESS"
    task_id = res["task"]["id"]
    assert res["task"]["status"] == "TODO"
    
    # List
    list_skill = TaskListSkill()
    res_list = await list_skill.execute(context, status="TODO")
    assert any(t["id"] == task_id for t in res_list)
    
    # Update
    update_skill = TaskUpdateSkill()
    res_update = await update_skill.execute(context, task_id=task_id, updates={"priority": "HIGH", "due_text": "2026-10-16T10:00:00Z"})
    assert res_update["task"]["priority"] == "HIGH"
    
    # Complete
    complete_skill = TaskCompleteSkill()
    res_complete = await complete_skill.execute(context, task_id=task_id)
    assert res_complete["task"]["status"] == "DONE"
    
    # Delete requires confirmation
    delete_skill = TaskDeleteSkill()
    preview = await delete_skill.preview(context, task_id=task_id)
    record = confirmation_manager.create_request(delete_skill.name, {"task_id": task_id}, preview, context)
    
    confirmation_manager.approve_confirmation(record.id, user_id="test", chat_id=123)
    assert confirmation_manager.check_and_consume_approval(delete_skill.name, {"task_id": task_id}, confirmation_id=record.id, user_id="test", chat_id=123) == True
    
    res_delete = await delete_skill.execute(context, task_id=task_id)
    assert res_delete["status"] == "SUCCESS"
