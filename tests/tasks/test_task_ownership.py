import pytest
from app.domain.task_identity import build_owner_key
from app.models.context import SkillExecutionContext, UserSessionContext
from app.providers.tasks.local import LocalTaskProvider
from app.skills.tasks.task_skills import (
    TaskCreateSkill,
    TaskListSkill,
    TaskUpdateSkill,
    TaskCompleteSkill,
    TaskDeleteSkill,
    TaskBulkCreateSkill,
)


def test_build_owner_key():
    k1 = build_owner_key("https://corp.odoo.com/", "erp_db", 42)
    k2 = build_owner_key("corp.odoo.com", "erp_db", "42")
    assert k1 == "corp.odoo.com:erp_db:42"
    assert k1 == k2

    # Different tenant, same UID
    k3 = build_owner_key("https://branch-b.odoo.com", "erp_db", 42)
    assert k1 != k3
    assert k3 == "branch-b.odoo.com:erp_db:42"

    # Missing field returns empty string
    assert build_owner_key("", "erp_db", 42) == ""
    assert build_owner_key("corp.odoo.com", "", 42) == ""
    assert build_owner_key("corp.odoo.com", "erp_db", "") == ""


@pytest.fixture
def provider():
    return LocalTaskProvider()


@pytest.fixture
def context_user_a():
    owner_a = build_owner_key("tenant1.odoo.com", "db1", 101)
    session = UserSessionContext(
        user_id="user_a",
        user_name="Nguyễn Văn A",
        chat_id=1001,
        tenant="tenant1.odoo.com",
        database="db1",
        odoo_uid=101,
        owner_key=owner_a,
    )
    return SkillExecutionContext(session=session)


@pytest.fixture
def context_user_b():
    owner_b = build_owner_key("tenant1.odoo.com", "db1", 202)
    session = UserSessionContext(
        user_id="user_b",
        user_name="Trần Thị B",
        chat_id=2002,
        tenant="tenant1.odoo.com",
        database="db1",
        odoo_uid=202,
        owner_key=owner_b,
    )
    return SkillExecutionContext(session=session)


@pytest.fixture
def context_direct():
    session = UserSessionContext(
        user_id="legacy_user",
        user_name="Legacy Direct User",
        chat_id=9999,
        owner_key=None,
    )
    return SkillExecutionContext(session=session)


@pytest.mark.asyncio
async def test_task_ownership_isolation(provider, context_user_a, context_user_b):
    create_skill = TaskCreateSkill()
    list_skill = TaskListSkill()
    update_skill = TaskUpdateSkill()
    complete_skill = TaskCompleteSkill()
    delete_skill = TaskDeleteSkill()

    # User A creates a task
    res_a = await create_skill.execute(context_user_a, title="Công việc của Nhân viên A")
    assert res_a["status"] == "SUCCESS"
    task_a_id = res_a["task"]["id"]
    assert res_a["task"]["owner_key"] == context_user_a.session.owner_key

    # User B creates a task
    res_b = await create_skill.execute(context_user_b, title="Công việc của Nhân viên B")
    assert res_b["status"] == "SUCCESS"
    task_b_id = res_b["task"]["id"]
    assert res_b["task"]["owner_key"] == context_user_b.session.owner_key

    # 1. Listing: User A only sees Task A, never Task B
    tasks_for_a = await list_skill.execute(context_user_a)
    a_ids = [t["id"] for t in tasks_for_a]
    assert task_a_id in a_ids
    assert task_b_id not in a_ids

    # Listing: User B only sees Task B, never Task A
    tasks_for_b = await list_skill.execute(context_user_b)
    b_ids = [t["id"] for t in tasks_for_b]
    assert task_b_id in b_ids
    assert task_a_id not in b_ids

    # 2. Accessing by ID directly at Provider level
    # User A can get task A
    t_a = await provider.get_task(task_a_id, owner_key=context_user_a.session.owner_key)
    assert t_a["id"] == task_a_id

    # User B CANNOT get task A by ID
    with pytest.raises(Exception):
        await provider.get_task(task_a_id, owner_key=context_user_b.session.owner_key)

    # 3. Modifying: User B CANNOT update Task A
    with pytest.raises(Exception):
        await update_skill.execute(
            context_user_b,
            task_id=task_a_id,
            updates={"title": "Hacked Title"},
        )

    # 4. Completing: User B CANNOT complete Task A
    with pytest.raises(Exception):
        await complete_skill.execute(context_user_b, task_id=task_a_id)

    # 5. Deleting: User B CANNOT delete Task A
    res_del = await delete_skill.execute(context_user_b, task_id=task_a_id)
    assert res_del["status"] == "FAILED"
    # Task A is still intact
    t_a_check = await provider.get_task(task_a_id, owner_key=context_user_a.session.owner_key)
    assert t_a_check["id"] == task_a_id


@pytest.mark.asyncio
async def test_llm_cannot_spoof_owner_key(context_user_a):
    create_skill = TaskCreateSkill()
    # Malicious attempt to spoof owner_key via kwargs
    res = await create_skill.execute(
        context_user_a,
        title="Spoofed Task",
        owner_key="spoofed:other_tenant:999",
    )
    assert res["status"] == "SUCCESS"
    # Must use context.session.owner_key, NOT kwargs
    assert res["task"]["owner_key"] == context_user_a.session.owner_key


@pytest.mark.asyncio
async def test_bulk_create_task_ownership(context_user_a):
    bulk_skill = TaskBulkCreateSkill()
    res = await bulk_skill.execute(
        context_user_a,
        tasks=[
            {"title": "Bulk 1"},
            {"title": "Bulk 2"},
        ],
    )
    assert res["status"] == "SUCCESS"
    assert res["created"] == 2
    for t in res["tasks"]:
        assert t["owner_key"] == context_user_a.session.owner_key


@pytest.mark.asyncio
async def test_legacy_direct_mode_scope(provider, context_user_a, context_direct):
    # Create legacy task with owner_key=None
    legacy_task = await provider.create_task(
        title="Legacy Direct Task",
        description="",
        priority="NORMAL",
        due_at=None,
        timezone="Asia/Ho_Chi_Minh",
        project=None,
        tags=[],
        owner_key=None,
    )
    legacy_id = legacy_task["id"]

    # Gateway user A CANNOT see legacy task
    tasks_a = await provider.list_tasks({}, owner_key=context_user_a.session.owner_key)
    assert legacy_id not in [t["id"] for t in tasks_a]

    # Gateway user A CANNOT get legacy task
    with pytest.raises(Exception):
        await provider.get_task(legacy_id, owner_key=context_user_a.session.owner_key)

    # Direct mode CAN see legacy task
    tasks_direct = await provider.list_tasks({}, owner_key=None)
    assert legacy_id in [t["id"] for t in tasks_direct]

    # Direct mode CANNOT see Gateway user A's task
    res_a = await TaskCreateSkill().execute(context_user_a, title="A's private task")
    a_id = res_a["task"]["id"]
    tasks_direct_after = await provider.list_tasks({}, owner_key=None)
    assert a_id not in [t["id"] for t in tasks_direct_after]
