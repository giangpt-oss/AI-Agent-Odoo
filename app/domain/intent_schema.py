from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class IntentType(str, Enum):
    REPLY = "reply"
    READ = "read"
    DRAFT = "draft"
    PREPARE_WRITE = "prepare_write"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"


class DraftPayload(BaseModel):
    draft_type: Literal["email", "task"]
    draft_action: Literal["create", "update", "cancel"]
    recipient: Optional[str] = None
    subject: Optional[str] = None
    content: str
    target_id: Optional[str] = None


class IntentAnalysisResult(BaseModel):
    intent_type: IntentType
    tool_name: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    reply_text: Optional[str] = None
    clarification_prompt: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    candidate_options: list[str] = Field(default_factory=list)
    draft: Optional[DraftPayload] = None

    @model_validator(mode="after")
    def validate_intent_consistency(self) -> "IntentAnalysisResult":
        if self.intent_type in (IntentType.READ, IntentType.PREPARE_WRITE):
            if not self.tool_name or not self.tool_name.strip():
                raise ValueError(f"tool_name bắt buộc phải có khi intent_type là {self.intent_type.value}")

        if self.intent_type == IntentType.CLARIFY:
            if not self.clarification_prompt or not self.clarification_prompt.strip():
                raise ValueError("clarification_prompt bắt buộc phải có khi intent_type là clarify")

        if self.intent_type == IntentType.REPLY:
            if not self.reply_text or not self.reply_text.strip():
                raise ValueError("reply_text bắt buộc phải có khi intent_type là reply")

        if self.intent_type == IntentType.DRAFT:
            if not self.draft:
                raise ValueError("draft payload bắt buộc phải có khi intent_type là draft")

        return self
