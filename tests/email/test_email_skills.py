import pytest
import time
from app.models.context import SkillExecutionContext, UserSessionContext
from app.skills.email.email_skills import EmailSearchSkill, EmailDraftSkill, EmailSendSkill
from app.agent.confirmation_manager import confirmation_manager, ConfirmationState
from app.providers.email.fake_email import FakeEmailProvider
import app.skills.email.email_skills as email_skills_module

@pytest.fixture
def fake_provider():
    provider = FakeEmailProvider()
    return provider

@pytest.fixture
def context(fake_provider, monkeypatch):
    monkeypatch.setattr(email_skills_module, 'get_email_provider', lambda c: fake_provider)
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test", chat_id=123))

@pytest.mark.asyncio
async def test_email_search(context):
    skill = EmailSearchSkill()
    res = await skill.execute(context, query="báo cáo")
    assert "messages" in res
    assert len(res["messages"]) >= 1
    assert "báo cáo" in res["messages"][0]["subject"].lower()

@pytest.mark.asyncio
async def test_email_draft(context, fake_provider):
    skill = EmailDraftSkill()
    res = await skill.execute(context, to=["test@company.com"], subject="Draft test", body="Hello")
    assert res["status"] == "SUCCESS"
    assert "draft_id" in res
    assert len(fake_provider.drafts) == 1

@pytest.mark.asyncio
async def test_email_send_confirmation_flow(context, fake_provider):
    skill = EmailSendSkill()
    args = {"to": ["test@company.com"], "subject": "Test send", "body": "Hello send"}
    
    # 1. Send requires confirmation, it won't execute normally unless approved.
    # In orchestrator, we call preview and create confirmation request.
    preview_data = await skill.preview(context, **args)
    record = confirmation_manager.create_request(skill.name, args, preview_data, context)
    
    # Check that provider has NOT sent yet
    assert len(fake_provider.sent) == 0
    
    # 2. Approve
    confirmation_manager.approve_confirmation(record.id, user_id="test", chat_id=123)
    
    # 3. Orchestrator executes after checking approval
    is_approved = confirmation_manager.check_and_consume_approval(skill.name, args, confirmation_id=record.id, user_id="test", chat_id=123)
    assert is_approved == True
    
    res = await skill.execute(context, **args)
    assert res["status"] == "SUCCESS"
    assert len(fake_provider.sent) == 1
    
    # 4. Check replay (approve again)
    # The record should be EXECUTED
    is_approved_again = confirmation_manager.check_and_consume_approval(skill.name, args, confirmation_id=record.id, user_id="test", chat_id=123)
    assert is_approved_again == False
    
    # Cannot approve an already executed record
    assert confirmation_manager.approve_confirmation(record.id, user_id="test", chat_id=123) == False
    
@pytest.mark.asyncio
async def test_email_send_reject_flow(context, fake_provider):
    skill = EmailSendSkill()
    args = {"to": ["test2@company.com"], "subject": "Test reject", "body": "Hello reject"}
    
    preview_data = await skill.preview(context, **args)
    record = confirmation_manager.create_request(skill.name, args, preview_data, context)
    
    assert len(fake_provider.sent) == 0
    
    # Reject
    confirmation_manager.reject_confirmation(record.id)
    is_approved = confirmation_manager.check_and_consume_approval(skill.name, args, confirmation_id=record.id, user_id="test", chat_id=123)
    assert is_approved == False
    # execution is bypassed in orchestrator if False
    assert len(fake_provider.sent) == 0
