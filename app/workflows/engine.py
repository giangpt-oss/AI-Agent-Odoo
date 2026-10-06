from typing import Dict, Any, List
from app.workflows.models import Workflow, WorkflowExecution, ExecutionStatus, StepErrorPolicy
from app.providers.workflows.sqlite import workflow_store
from app.skills.registry import SkillRegistry
from app.models.context import SkillExecutionContext, UserSessionContext
import datetime
import re

from app.core.exceptions import SkillNotFoundError

class WorkflowError(Exception):
    pass

class WorkflowEngine:
    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def _validate_workflow(self, workflow: Workflow) -> None:
        # Check if skills exist
        for step in workflow.steps:
            try:
                self.registry.get_skill(step.skill)
            except SkillNotFoundError:
                raise WorkflowError(f"Skill '{step.skill}' not found in registry.")
                
        # Simple DAG check: Ensure no cycle by checking output_key dependencies
        available_keys = list(workflow.inputs_schema.keys())
        for step in workflow.steps:
            for v in step.input_mapping.values():
                # Extract {{key}}
                matches = re.findall(r"\{\{([^}]+)\}\}", v)
                for m in matches:
                    if m not in available_keys:
                        raise WorkflowError(f"Cyclic dependency or missing input '{m}' at step '{step.step_id}'.")
            available_keys.append(step.output_key)

    def _resolve_inputs(self, mapping: Dict[str, str], context_data: Dict[str, Any]) -> Dict[str, Any]:
        resolved = {}
        for k, v in mapping.items():
            if isinstance(v, str):
                matches = re.findall(r"\{\{([^}]+)\}\}", v)
                if len(matches) == 1 and f"{{{{{matches[0]}}}}}" == v:
                    # Direct mapping to preserve type
                    resolved[k] = context_data.get(matches[0])
                else:
                    # String interpolation
                    res_v = v
                    for m in matches:
                        res_v = res_v.replace(f"{{{{{m}}}}}", str(context_data.get(m, "")))
                    resolved[k] = res_v
            else:
                resolved[k] = v
        return resolved

    async def start_execution(self, workflow: Workflow, user_id: str, workspace_id: str, inputs: Dict[str, Any]) -> WorkflowExecution:
        self._validate_workflow(workflow)
        
        # Check inputs
        for key, schema in workflow.inputs_schema.items():
            if schema.get("required", False) and key not in inputs:
                raise WorkflowError(f"WORKFLOW_INPUT_REQUIRED: {key}")

        execution = WorkflowExecution(
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            user_id=user_id,
            workspace_id=workspace_id,
            context_data=inputs,
            status=ExecutionStatus.RUNNING
        )
        workflow_store.save_execution(execution)
        return await self._run_loop(execution, workflow)

    async def resume_execution(self, execution_id: str, user_id: str, payload: Dict[str, Any]) -> WorkflowExecution:
        execution = workflow_store.get_execution(execution_id)
        if not execution or execution.user_id != user_id:
            raise WorkflowError("Execution not found or unauthorized.")
            
        if execution.status != ExecutionStatus.WAITING_CONFIRMATION:
            raise WorkflowError("Execution is not waiting for confirmation.")
            
        workflow = workflow_store.get_workflow(execution.workflow_id)
        if not workflow:
            raise WorkflowError("Original workflow not found.")

        # Save result from waiting skill
        step = workflow.steps[execution.current_step_index]
        execution.context_data[step.output_key] = payload
        execution.current_step_index += 1
        execution.status = ExecutionStatus.RUNNING
        execution.waiting_skill = None
        execution.waiting_payload = None
        workflow_store.save_execution(execution)
        
        return await self._run_loop(execution, workflow)

    async def _run_loop(self, execution: WorkflowExecution, workflow: Workflow) -> WorkflowExecution:
        session = UserSessionContext(user_id=execution.user_id, user_name="Workflow User")
        # Ensure workspace metadata is stored somewhere accessible if needed.
        ctx = SkillExecutionContext(session=session)
        
        try:
            while execution.current_step_index < len(workflow.steps):
                step = workflow.steps[execution.current_step_index]
                
                # Check condition
                if step.condition:
                    # Simple boolean check from context
                    cond_val = execution.context_data.get(step.condition)
                    if not cond_val:
                        # Skip step
                        execution.current_step_index += 1
                        continue

                skill = self.registry.get_skill(step.skill)
                inputs = self._resolve_inputs(step.input_mapping, execution.context_data)
                
                if skill.requires_confirmation:
                    # Pause execution
                    execution.status = ExecutionStatus.WAITING_CONFIRMATION
                    execution.waiting_skill = skill.name
                    execution.waiting_payload = inputs
                    workflow_store.save_execution(execution)
                    return execution

                try:
                    result = await skill.execute(context=ctx, **inputs)
                    execution.context_data[step.output_key] = result
                    
                    if isinstance(result, dict) and "artifact_id" in result:
                        execution.artifacts.append({"artifact_id": result["artifact_id"], "step": step.step_id})
                        
                    execution.current_step_index += 1
                    workflow_store.save_execution(execution)
                    
                except Exception as e:
                    if step.on_error == StepErrorPolicy.SKIP:
                        execution.context_data[step.output_key] = {"error": str(e), "skipped": True}
                        execution.current_step_index += 1
                    else:
                        raise e

            execution.status = ExecutionStatus.COMPLETED
            execution.finished_at = datetime.datetime.now().isoformat()
            workflow_store.save_execution(execution)
            return execution
            
        except Exception as e:
            execution.status = ExecutionStatus.FAILED
            execution.error_msg = str(e)
            execution.finished_at = datetime.datetime.now().isoformat()
            workflow_store.save_execution(execution)
            return execution
