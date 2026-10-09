import uuid
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.domain.risk_levels import ActionRiskLevel


class DraftItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: str = "email"  # email | jd | report | document
    title: str = ""
    content: str = ""
    recipient: Optional[str] = None
    subject: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    updated_at: float = Field(default_factory=time.time)


class DraftSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    items: List[DraftItem] = Field(default_factory=list)
    active_index: Optional[int] = None
    is_committed: bool = False
    created_at: float = Field(default_factory=time.time)

    def get_active_item(self) -> Optional[DraftItem]:
        if self.active_index is not None and 0 <= self.active_index < len(self.items):
            return self.items[self.active_index]
        return self.items[0] if self.items else None

    def update_item_content(self, index: int, new_content: str, note: str = "") -> bool:
        if 0 <= index < len(self.items):
            item = self.items[index]
            item.content = new_content
            item.version += 1
            item.updated_at = time.time()
            if note:
                item.metadata["last_revision_note"] = note
            return True
        return False


class ActionProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    skill_name: str
    risk_level: ActionRiskLevel = ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE
    description: str = ""
    target_system: str = "odoo"
    arguments: Dict[str, Any] = Field(default_factory=dict)
    preview_data: Dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = True


class StructuredTaskResult(BaseModel):
    status: str = "Completed"
    summary: str = ""
    results_data: List[str] = Field(default_factory=list)
    changes_performed: List[str] = Field(default_factory=list)
    attention_required: List[str] = Field(default_factory=list)
    next_suggested_steps: List[str] = Field(default_factory=list)
    raw_payload: Optional[Dict[str, Any]] = None

    def to_markdown(self) -> str:
        """Định dạng kết quả theo chuẩn Structured Task Results doanh nghiệp."""
        status_emoji = "✅" if self.status == "Completed" else "⚠️" if "Pending" in self.status else "ℹ️"
        lines = [f"{status_emoji} **{self.status.upper()}**\n"]
        if self.summary:
            lines.append(f"{self.summary}\n")

        if self.results_data:
            lines.append("📊 **Kết quả thực hiện:**")
            for item in self.results_data:
                lines.append(f"• {item}")
            lines.append("")

        if self.changes_performed:
            lines.append("📝 **Thay đổi đã thực hiện:**")
            for item in self.changes_performed:
                lines.append(f"• {item}")
            lines.append("")

        if self.attention_required:
            lines.append("⚠️ **Cần chú ý:**")
            for item in self.attention_required:
                lines.append(f"• {item}")
            lines.append("")

        if self.next_suggested_steps:
            lines.append("💡 **Đề xuất bước tiếp theo:**")
            for item in self.next_suggested_steps:
                lines.append(f"• {item}")
            lines.append("")

        return "\n".join(lines).strip()
