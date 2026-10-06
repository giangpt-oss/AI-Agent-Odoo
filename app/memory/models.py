from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
import uuid

class MemoryCategory:
    PREFERENCE = "PREFERENCE"
    SETTING = "SETTING"
    WORKFLOW = "WORKFLOW"
    PROJECT_CONTEXT = "PROJECT_CONTEXT"
    RESOURCE_REFERENCE = "RESOURCE_REFERENCE"
    NAMING_CONVENTION = "NAMING_CONVENTION"
    COMMUNICATION_STYLE = "COMMUNICATION_STYLE"
    TEMPORARY_CONTEXT = "TEMPORARY_CONTEXT"

class MemoryStatus:
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    DELETED = "DELETED"

class MemoryScope:
    GLOBAL = "GLOBAL"
    WORKSPACE = "WORKSPACE"
    PROJECT = "PROJECT"

class MemorySourceType:
    USER_EXPLICIT = "USER_EXPLICIT"
    USER_SETTINGS = "USER_SETTINGS"
    CONFIRMED_WORKFLOW = "CONFIRMED_WORKFLOW"
    CONFIRMED_PROJECT_CONTEXT = "CONFIRMED_PROJECT_CONTEXT"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"

class PrivacyLevel:
    NORMAL = "NORMAL"
    PRIVATE = "PRIVATE"
    SENSITIVE = "SENSITIVE"

class MemoryRecord(BaseModel):
    memory_id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:8]}")
    user_id: str
    workspace_id: Optional[str] = None
    project_id: Optional[str] = None
    scope: str = MemoryScope.WORKSPACE
    category: str
    key: str
    value: Dict[str, Any]
    summary: str = ""
    source_type: str
    source_id: Optional[str] = None
    confidence: float = 1.0
    privacy_level: str = PrivacyLevel.NORMAL
    status: str = MemoryStatus.ACTIVE
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    expires_at: Optional[str] = None
    previous_memory_id: Optional[str] = None

class MemoryCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: f"cand_{uuid.uuid4().hex[:8]}")
    user_id: str
    proposed_category: str
    proposed_key: str
    proposed_value: Dict[str, Any]
    reason: str = ""
    confidence: float = 0.5
    requires_confirmation: bool = True
