from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class UserSessionContext(BaseModel):
    user_id: str
    user_name: str
    roles: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    email: str = "Chưa liên kết"
    chat_id: Optional[int] = None

class FileAttachment(BaseModel):
    filename: str
    summary: str = ""
    text_content: str = ""
    raw_bytes: Optional[bytes] = None
    mime_type: str = ""

class SkillExecutionContext(BaseModel):
    session: UserSessionContext
    attachments: List[FileAttachment] = Field(default_factory=list)
    workspace: Dict[str, Any] = Field(default_factory=dict)
    providers: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    previous_outputs: Dict[str, Any] = Field(default_factory=dict)
