from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime
import uuid

class TemplateFormat:
    DOCX = "DOCX"
    MD = "MD"
    EMAIL = "EMAIL"
    NOTE = "NOTE"

class Template(BaseModel):
    template_id: str = Field(default_factory=lambda: f"tpl_{uuid.uuid4().hex[:8]}")
    name: str
    description: str = ""
    template_type: str = "DOCUMENT"
    version: int = 1
    format: str = TemplateFormat.DOCX
    schema_def: Dict[str, Any] = Field(default_factory=dict) # {"month": {"type": "string", "required": True}}
    content: str = "" # Holds markdown, HTML, or paths to template binaries
    user_id: str
    workspace_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    status: str = "ACTIVE" # ACTIVE, ARCHIVED

class StepErrorPolicy:
    STOP = "STOP"
    SKIP = "SKIP"
    RETRY = "RETRY"
    FALLBACK = "FALLBACK"

class WorkflowStep(BaseModel):
    step_id: str
    skill: str
    input_mapping: Dict[str, str] = Field(default_factory=dict) # {"target_key": "{{source_key}}"}
    output_key: str
    condition: Optional[str] = None
    on_error: str = StepErrorPolicy.STOP

class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: f"wf_{uuid.uuid4().hex[:8]}")
    name: str
    description: str = ""
    version: int = 1
    inputs_schema: Dict[str, Any] = Field(default_factory=dict)
    steps: List[WorkflowStep] = Field(default_factory=list)
    user_id: str
    workspace_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    status: str = "ACTIVE" # ACTIVE, ARCHIVED

class ExecutionStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class WorkflowExecution(BaseModel):
    execution_id: str = Field(default_factory=lambda: f"exec_{uuid.uuid4().hex[:8]}")
    workflow_id: str
    workflow_version: int
    user_id: str
    workspace_id: Optional[str] = None
    status: str = ExecutionStatus.PENDING
    current_step_index: int = 0
    context_data: Dict[str, Any] = Field(default_factory=dict) # Holds variables
    artifacts: List[Dict[str, str]] = Field(default_factory=list)
    error_msg: Optional[str] = None
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    finished_at: Optional[str] = None
    waiting_skill: Optional[str] = None
    waiting_payload: Optional[Dict[str, Any]] = None
