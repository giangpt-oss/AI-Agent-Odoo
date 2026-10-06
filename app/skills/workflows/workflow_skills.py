from typing import Any, Dict
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType, RiskLevel
from app.models.context import SkillExecutionContext

from app.workflows.models import Workflow, WorkflowStep
from app.providers.workflows.sqlite import workflow_store
from app.workflows.engine import WorkflowEngine

class WorkflowCreateSkill(BaseSkill):
    name = "workflow_create"
    description = "Tạo một workflow mới từ các skills hiện có."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.WRITE
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["WRITE_WORKFLOWS"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "inputs_schema": {"type": "object"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string"},
                        "skill": {"type": "string"},
                        "input_mapping": {"type": "object"},
                        "output_key": {"type": "string"}
                    },
                    "required": ["step_id", "skill", "output_key"]
                }
            }
        },
        "required": ["name", "steps"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        
        steps = []
        for s in kwargs["steps"]:
            steps.append(WorkflowStep(
                step_id=s["step_id"],
                skill=s["skill"],
                input_mapping=s.get("input_mapping", {}),
                output_key=s["output_key"]
            ))
            
        wf = Workflow(
            name=kwargs["name"],
            description=kwargs.get("description", ""),
            inputs_schema=kwargs.get("inputs_schema", {}),
            steps=steps,
            user_id=context.session.user_id,
            workspace_id=workspace_id
        )
        
        workflow_store.save_workflow(wf)
        return {"status": "SUCCESS", "message": "Đã tạo workflow.", "workflow_id": wf.workflow_id}

class WorkflowRunSkill(BaseSkill):
    name = "workflow_run"
    description = "Thực thi một workflow."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.WRITE
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = False
    capabilities = ["RUN_WORKFLOWS"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string"},
            "inputs": {"type": "object"}
        },
        "required": ["workflow_id"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        wf = workflow_store.get_workflow(kwargs["workflow_id"])
        if not wf:
            return {"status": "ERROR", "message": "Workflow không tồn tại."}
            
        inputs = kwargs.get("inputs", {})
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        
        try:
            from app.skills.bootstrap import default_registry
            engine = WorkflowEngine(default_registry)
            execution = await engine.start_execution(wf, context.session.user_id, workspace_id, inputs)
            return {
                "status": execution.status,
                "execution_id": execution.execution_id,
                "artifacts": execution.artifacts,
                "error_msg": execution.error_msg
            }
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

class WorkflowListSkill(BaseSkill):
    name = "workflow_list"
    description = "Liệt kê các workflows."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.READ
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["READ_WORKFLOWS"]
    
    input_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        workflows = workflow_store.list_workflows(context.session.user_id, workspace_id)
        return {
            "status": "SUCCESS",
            "workflows": [{"id": w.workflow_id, "name": w.name} for w in workflows]
        }
