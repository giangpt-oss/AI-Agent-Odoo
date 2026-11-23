from enum import Enum
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class IntentType(str, Enum):
    REPLY = "reply"
    READ = "read"
    DRAFT = "draft"
    PREPARE_WRITE = "prepare_write"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"
    ACTION_CONFIRM = "action_confirm"
    ACTION_CANCEL = "action_cancel"
    ACTION_STATUS = "action_status"
    TOOL_PLAN = "tool_plan"


class ToolPlanStep(BaseModel, extra="forbid"):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
    tool_name: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=7)


def visit_step_parameters(value, resolve):
    if isinstance(value, list):
        return [visit_step_parameters(item, resolve) for item in value]
    if not isinstance(value, dict):
        return value
    if "$step" in value:
        source, path = value["$step"], value.get("path")
        if (set(value) != {"$step", "path"} or not isinstance(source, str)
                or not isinstance(path, list) or not 1 <= len(path) <= 8
                or any(
                    not (isinstance(key, str) and key or type(key) is int and key >= 0)
                    for key in path
                )):
            raise ValueError("Tham chiếu kết quả không hợp lệ.")
        return resolve(source, path)
    return {key: visit_step_parameters(item, resolve) for key, item in value.items()}


class DraftPayload(BaseModel):
    draft_type: Literal["email", "task"]
    draft_action: Literal["create", "update", "cancel"]
    draft_id: Optional[str] = None
    version: int = 1
    recipient: Optional[str] = None
    to: Optional[str] = None
    subject: Optional[str] = None
    content: str
    body: Optional[str] = None
    target_id: Optional[str] = None
    expected_version: Optional[int] = None
    expected_draft_id: Optional[str] = None

    @model_validator(mode="after")
    def sync_field_aliases(self) -> "DraftPayload":
        if self.to and not self.recipient:
            self.recipient = self.to
        elif self.recipient and not self.to:
            self.to = self.recipient

        if self.body and not self.content:
            self.content = self.body
        elif self.content and not self.body:
            self.body = self.content
        return self


class IntentAnalysisResult(BaseModel):
    intent_type: IntentType
    tool_name: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    reply_text: Optional[str] = None
    clarification_prompt: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    candidate_options: list[str] = Field(default_factory=list)
    draft: Optional[DraftPayload] = None
    confirmation_id: Optional[str] = None
    steps: list[ToolPlanStep] = Field(default_factory=list, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Intent type mapping
            itype = data.get("intent_type")
            if itype in ("write", "create", "execute", "mutation", "update"):
                data["intent_type"] = "prepare_write"
            elif itype in ("chat", "answer", "info", "general"):
                data["intent_type"] = "reply"
            elif itype in ("ask", "clarification", "question"):
                data["intent_type"] = "clarify"
            elif itype in ("query", "search", "get", "view", "list"):
                data["intent_type"] = "read"

            if "tool" in data and not data.get("tool_name"):
                data["tool_name"] = data.pop("tool")
            elif "action" in data and not data.get("tool_name") and data.get("action") not in ("unknown", None):
                data["tool_name"] = data.pop("action")

            # Clarification prompt aliases
            if not data.get("clarification_prompt"):
                for key in ("question", "prompt", "clarify_prompt", "ask"):
                    if data.get(key):
                        data["clarification_prompt"] = data.pop(key)
                        break
            if not data.get("clarification_prompt") and data.get("intent_type") == "clarify":
                if data.get("reply_text"):
                    data["clarification_prompt"] = data.get("reply_text")
                elif data.get("message"):
                    data["clarification_prompt"] = data.pop("message")

            # Reply text aliases
            if not data.get("reply_text"):
                for key in ("message", "response", "text", "content"):
                    if data.get(key) and not isinstance(data.get(key), (dict, list)):
                        data["reply_text"] = data.pop(key)
                        break
            if not data.get("reply_text") and data.get("intent_type") == "reply" and data.get("clarification_prompt"):
                data["reply_text"] = data.get("clarification_prompt")
        return data

    @model_validator(mode="after")
    def validate_intent_consistency(self) -> "IntentAnalysisResult":
        if self.intent_type == IntentType.TOOL_PLAN:
            if not self.steps or self.tool_name or self.parameters:
                raise ValueError("Kế hoạch cần danh sách bước riêng.")
            graph = {step.id: step.depends_on for step in self.steps}
            if len(graph) != len(self.steps):
                raise ValueError("ID bước bị trùng.")
            for step in self.steps:
                dependencies = set(step.depends_on)
                if not dependencies <= graph.keys():
                    raise ValueError("Bước phụ thuộc không tồn tại.")

                def validate_reference(source, path):
                    if source not in dependencies:
                        raise ValueError("Tham chiếu phải thuộc bước phụ thuộc trực tiếp.")

                visit_step_parameters(step.parameters, validate_reference)
            try:
                TopologicalSorter(graph).prepare()
            except CycleError as exc:
                raise ValueError("Kế hoạch có phụ thuộc vòng.") from exc
        elif self.steps:
            raise ValueError("Danh sách bước chỉ hợp lệ với tool_plan.")

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

        if self.intent_type in (IntentType.ACTION_CONFIRM, IntentType.ACTION_CANCEL, IntentType.ACTION_STATUS):
            if not self.confirmation_id and self.parameters.get("confirmation_id"):
                self.confirmation_id = str(self.parameters["confirmation_id"])

        return self
