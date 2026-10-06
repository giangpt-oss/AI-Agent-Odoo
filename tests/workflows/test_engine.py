import pytest
import asyncio
from typing import Any
from app.workflows.models import Workflow, WorkflowStep, ExecutionStatus
from app.workflows.engine import WorkflowEngine, WorkflowError
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType, RiskLevel
from app.skills.registry import SkillRegistry
from app.models.context import SkillExecutionContext

# Fake Skills for testing
class FakeMathSkill(BaseSkill):
    name = "fake_math"
    description = ""
    category = SkillCategory.SYSTEM
    operation_type = OperationType.READ
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = []
    input_schema = {}
    output_schema = {}
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return {"result": int(kwargs.get("val1", 0)) + int(kwargs.get("val2", 0))}

class FakeWriteSkill(BaseSkill):
    name = "fake_write"
    description = ""
    category = SkillCategory.SYSTEM
    operation_type = OperationType.WRITE
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = True # Pause expected
    capabilities = []
    input_schema = {}
    output_schema = {}
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return {"status": "Written"}

@pytest.fixture
def test_registry():
    r = SkillRegistry()
    r.register(FakeMathSkill())
    r.register(FakeWriteSkill())
    return r

@pytest.fixture
def workflow_engine(test_registry):
    return WorkflowEngine(test_registry)

@pytest.mark.asyncio
async def test_workflow_validation(workflow_engine):
    # Skill doesn't exist
    w1 = Workflow(
        name="W1",
        user_id="user1",
        steps=[WorkflowStep(step_id="1", skill="non_existent", output_key="out")]
    )
    with pytest.raises(WorkflowError, match="not found"):
        workflow_engine._validate_workflow(w1)
        
    # Cycle / missing input mapping check
    w2 = Workflow(
        name="W2",
        user_id="user1",
        inputs_schema={"input_a": {}},
        steps=[WorkflowStep(step_id="1", skill="fake_math", input_mapping={"val1": "{{input_b}}"}, output_key="out")]
    )
    with pytest.raises(WorkflowError, match="Cyclic dependency or missing input"):
        workflow_engine._validate_workflow(w2)

@pytest.mark.asyncio
async def test_workflow_execution_and_pause(workflow_engine):
    w = Workflow(
        name="W3",
        user_id="user_test",
        inputs_schema={"start_val": {"required": True}},
        steps=[
            WorkflowStep(
                step_id="step1",
                skill="fake_math",
                input_mapping={"val1": "{{start_val}}", "val2": "5"},
                output_key="math_out"
            ),
            WorkflowStep(
                step_id="step2",
                skill="fake_write",
                input_mapping={"data": "{{math_out}}"},
                output_key="write_out"
            )
        ]
    )
    # Save to store
    from app.providers.workflows.sqlite import workflow_store
    workflow_store.save_workflow(w)
    
    # Run
    exec1 = await workflow_engine.start_execution(w, "user_test", "ws_test", {"start_val": 10})
    
    # Should pause at step2 due to requires_confirmation
    assert exec1.status == ExecutionStatus.WAITING_CONFIRMATION
    assert exec1.current_step_index == 1
    assert exec1.waiting_skill == "fake_write"
    
    # Context should have step1 output
    assert "math_out" in exec1.context_data
    assert exec1.context_data["math_out"]["result"] == 15
    
    # Resume
    exec2 = await workflow_engine.resume_execution(exec1.execution_id, "user_test", {"status": "Written"})
    
    # Should complete
    assert exec2.status == ExecutionStatus.COMPLETED
    assert exec2.current_step_index == 2
    assert exec2.context_data["write_out"]["status"] == "Written"

@pytest.mark.asyncio
async def test_workflow_condition(workflow_engine):
    w = Workflow(
        name="W4",
        user_id="user_test",
        inputs_schema={"should_run": {}},
        steps=[
            WorkflowStep(
                step_id="step1",
                skill="fake_math",
                condition="should_run",
                input_mapping={"val1": "1", "val2": "1"},
                output_key="math_out"
            )
        ]
    )
    
    # Run with false condition (should skip step1)
    exec1 = await workflow_engine.start_execution(w, "user_test", "ws_test", {"should_run": False})
    assert exec1.status == ExecutionStatus.COMPLETED
    assert "math_out" not in exec1.context_data
    
    # Run with true condition (should run step1)
    exec2 = await workflow_engine.start_execution(w, "user_test", "ws_test", {"should_run": True})
    assert exec2.status == ExecutionStatus.COMPLETED
    assert exec2.context_data["math_out"]["result"] == 2
