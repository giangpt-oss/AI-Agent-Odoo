import pytest
import asyncio
from app.skills.base import BaseSkill
from app.skills.registry import SkillRegistry
from app.models.skill import SkillCategory, RiskLevel, OperationType
from app.models.context import SkillExecutionContext, UserSessionContext
from app.core.exceptions import SkillError, SkillNotFoundError, SkillExecutionError
from app.agent.router import SkillRouter
from app.agent.orchestrator import AgentOrchestrator

class DummySkill(BaseSkill):
    name = "dummy_skill"
    description = "A dummy skill"
    category = SkillCategory.SYSTEM
    capabilities = ["test", "dummy"]
    operation_type = OperationType.READ
    input_schema = {}
    
    async def execute(self, context, **kwargs):
        return "dummy_result"

class ErrorSkill(BaseSkill):
    name = "error_skill"
    category = SkillCategory.SYSTEM
    capabilities = ["error"]
    operation_type = OperationType.READ
    
    async def execute(self, context, **kwargs):
        raise ValueError("Simulated error")

class DestructiveSkill(BaseSkill):
    name = "destructive_skill"
    category = SkillCategory.SYSTEM
    capabilities = ["destroy"]
    operation_type = OperationType.DESTRUCTIVE
    requires_confirmation = True
    
    async def execute(self, context, **kwargs):
        return "destroyed"
        
    async def preview(self, context, **kwargs):
        return "Will destroy everything"

@pytest.fixture
def registry():
    return SkillRegistry()

@pytest.fixture
def dummy_context():
    return SkillExecutionContext(
        session=UserSessionContext(user_id="test", user_name="Test")
    )

def test_skill_registration(registry):
    skill = DummySkill()
    registry.register(skill)
    assert registry.get_skill("dummy_skill") == skill
    
def test_duplicate_registration(registry):
    registry.register(DummySkill())
    with pytest.raises(SkillError):
        registry.register(DummySkill())

def test_capability_lookup(registry):
    skill1 = DummySkill()
    skill2 = DestructiveSkill()
    registry.register(skill1)
    registry.register(skill2)
    
    res = registry.lookup(capabilities=["test"])
    assert len(res) == 1
    assert res[0].name == "dummy_skill"
    
    res = registry.lookup(category=SkillCategory.SYSTEM)
    assert len(res) == 2

@pytest.mark.asyncio
async def test_skill_execution_error(dummy_context):
    skill = ErrorSkill()
    with pytest.raises(ValueError):
        await skill.execute(dummy_context)

@pytest.mark.asyncio
async def test_risk_confirmation(dummy_context):
    skill = DestructiveSkill()
    assert skill.requires_confirmation is True
    assert skill.operation_type == OperationType.DESTRUCTIVE
    preview = await skill.preview(dummy_context)
    assert preview == "Will destroy everything"
    
    # Read operation shouldn't support preview but returns a standard message
    read_skill = DummySkill()
    read_preview = await read_skill.preview(dummy_context)
    assert read_preview == "No changes to preview (READ operation)."

def test_odoo_skill_backward_compatibility():
    # Verify that Odoo skills are properly defined and importable
    from app.skills.odoo.hr_skill import OdooHrSkill
    from app.skills.odoo.crm_skill import OdooCrmSkill
    skill = OdooHrSkill()
    assert skill.name == "get_company_employees"
    assert skill.category == SkillCategory.ODOO
    
@pytest.mark.asyncio
async def test_router_fallback(registry, dummy_context):
    registry.register(DummySkill())
    # Without ai_client, router should fallback to heuristic
    router = SkillRouter(ai_client=None, registry=registry)
    skills = await router.route("I want to test dummy", dummy_context)
    # Heuristic matches "dummy" capability
    assert any(s.name == "dummy_skill" for s in skills)
