import pytest
import datetime
from app.memory.models import MemoryRecord, MemoryCategory, MemorySourceType, MemoryStatus, MemoryScope
from app.memory.service import memory_service, SensitiveMemoryError
from app.memory.context import memory_context_service

@pytest.fixture
def test_user():
    return "user_123"

@pytest.fixture
def test_workspace():
    return "workspace_abc"

def test_sensitive_memory_blocked(test_user, test_workspace):
    record = MemoryRecord(
        user_id=test_user,
        workspace_id=test_workspace,
        scope=MemoryScope.WORKSPACE,
        category=MemoryCategory.PREFERENCE,
        key="email_login",
        value={"password": "MySuperSecretPassword123!"},
        summary="User's email password",
        source_type=MemorySourceType.USER_EXPLICIT
    )
    with pytest.raises(SensitiveMemoryError):
        memory_service.save_memory(record)
        
    record2 = MemoryRecord(
        user_id=test_user,
        workspace_id=test_workspace,
        scope=MemoryScope.WORKSPACE,
        category=MemoryCategory.SETTING,
        key="api_key",
        value={"key": "sk-1234567890abcdef1234567890abcdef"},
        summary="OpenAI API Key",
        source_type=MemorySourceType.USER_EXPLICIT
    )
    with pytest.raises(SensitiveMemoryError):
        memory_service.save_memory(record2)

def test_memory_supersede_conflict_resolution(test_user, test_workspace):
    record1 = MemoryRecord(
        user_id=test_user,
        workspace_id=test_workspace,
        scope=MemoryScope.WORKSPACE,
        category=MemoryCategory.PREFERENCE,
        key="report_format",
        value={"format": "DOCX"},
        summary="User prefers DOCX",
        source_type=MemorySourceType.USER_EXPLICIT
    )
    r1 = memory_service.save_memory(record1)
    
    # Later, user changes preference to PDF
    record2 = MemoryRecord(
        user_id=test_user,
        workspace_id=test_workspace,
        scope=MemoryScope.WORKSPACE,
        category=MemoryCategory.PREFERENCE,
        key="report_format",
        value={"format": "PDF"},
        summary="User prefers PDF",
        source_type=MemorySourceType.USER_EXPLICIT
    )
    r2 = memory_service.save_memory(record2)
    
    # Assert r1 is superseded
    r1_db = memory_service.get_memory(r1.memory_id, test_user)
    assert r1_db.status == MemoryStatus.SUPERSEDED
    
    # Assert r2 is active
    r2_db = memory_service.get_memory(r2.memory_id, test_user)
    assert r2_db.status == MemoryStatus.ACTIVE
    
    # r2 should link to r1
    assert r2_db.previous_memory_id == r1.memory_id
    
    # Search should only return the active one
    results = memory_service.search_memories("format", test_user, test_workspace)
    assert len([r for r in results if r.key == "report_format"]) == 1
    assert results[0].value["format"] == "PDF"

def test_multi_user_workspace_isolation(test_user, test_workspace):
    record = MemoryRecord(
        user_id=test_user,
        workspace_id=test_workspace,
        scope=MemoryScope.WORKSPACE,
        category=MemoryCategory.PREFERENCE,
        key="isolated_key",
        value={"val": 1},
        summary="Isolated mem",
        source_type=MemorySourceType.USER_EXPLICIT
    )
    memory_service.save_memory(record)
    
    # Different user
    results = memory_service.search_memories("Isolated", "user_456", test_workspace)
    assert len(results) == 0
    
    # Same user, different workspace
    results2 = memory_service.search_memories("Isolated", test_user, "workspace_xyz")
    assert len(results2) == 0

def test_temporary_memory_expiry(test_user, test_workspace):
    # Create an expired memory
    past = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()
    record = MemoryRecord(
        user_id=test_user,
        workspace_id=test_workspace,
        scope=MemoryScope.WORKSPACE,
        category=MemoryCategory.TEMPORARY_CONTEXT,
        key="temp_project",
        value={"project": "X"},
        summary="Temp project",
        source_type=MemorySourceType.USER_EXPLICIT,
        expires_at=past
    )
    mem = memory_service.save_memory(record)
    
    # When listed/searched, it should be expired and not returned
    results = memory_service.search_memories("Temp project", test_user, test_workspace)
    assert len([r for r in results if r.memory_id == mem.memory_id]) == 0
    
    db_mem = memory_service.get_memory(mem.memory_id, test_user)
    assert db_mem.status == MemoryStatus.EXPIRED

def test_context_injector(test_user, test_workspace):
    # Ensure memory_context_service builds the correct prompt block
    record = MemoryRecord(
        user_id=test_user,
        workspace_id=test_workspace,
        scope=MemoryScope.WORKSPACE,
        category=MemoryCategory.COMMUNICATION_STYLE,
        key="email_tone",
        value={"tone": "professional"},
        summary="Always write emails professionally",
        source_type=MemorySourceType.USER_EXPLICIT
    )
    memory_service.save_memory(record)
    
    context_str = memory_context_service.get_relevant_context(test_user, test_workspace, "email")
    assert "professional" in context_str
    assert "email_tone" in context_str
    
    # Document intent should not include communication style
    context_str_doc = memory_context_service.get_relevant_context(test_user, test_workspace, "document")
    assert "email_tone" not in context_str_doc
