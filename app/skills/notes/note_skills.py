from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.providers.notes.local import LocalNoteRepository

def get_note_repository(context: SkillExecutionContext):
    return LocalNoteRepository()

class NoteCreateSkill(BaseSkill):
    name = "create_note"
    description = "Tạo một ghi chú mới (markdown-first)."
    category = SkillCategory.UTILITY
    capabilities = ["note", "write", "create"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string", "description": "Nội dung ghi chú theo chuẩn Markdown"},
            "note_type": {"type": "string", "enum": ["GENERAL", "MEETING", "STUDY", "RESEARCH", "DAILY", "PROJECT"], "default": "GENERAL"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "meeting_id": {"type": "string"},
            "project": {"type": "string"}
        },
        "required": ["title", "content"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_note_repository(context)
        note = await repo.create_note(
            title=kwargs["title"],
            content=kwargs["content"],
            note_type=kwargs.get("note_type", "GENERAL"),
            tags=kwargs.get("tags", []),
            meeting_id=kwargs.get("meeting_id"),
            project=kwargs.get("project")
        )
        return {"status": "SUCCESS", "note": note}

class NoteGetSkill(BaseSkill):
    name = "get_note"
    description = "Lấy nội dung chi tiết của ghi chú."
    category = SkillCategory.UTILITY
    capabilities = ["note", "read", "get"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "note_id": {"type": "string"}
        },
        "required": ["note_id"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_note_repository(context)
        note = await repo.get_note(kwargs["note_id"])
        return {"status": "SUCCESS", "note": note}

class NoteListSkill(BaseSkill):
    name = "list_notes"
    description = "Danh sách các ghi chú."
    category = SkillCategory.UTILITY
    capabilities = ["note", "read", "list"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "note_type": {"type": "string"},
            "meeting_id": {"type": "string"},
            "project": {"type": "string"}
        }
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_note_repository(context)
        return await repo.list_notes(kwargs)

class NoteSearchSkill(BaseSkill):
    name = "search_notes"
    description = "Tìm kiếm ghi chú theo từ khóa."
    category = SkillCategory.UTILITY
    capabilities = ["note", "read", "search"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"}
        },
        "required": ["keyword"]
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_note_repository(context)
        return await repo.list_notes({"keyword": kwargs["keyword"]})

class NoteUpdateSkill(BaseSkill):
    name = "update_note"
    description = "Cập nhật nội dung ghi chú."
    category = SkillCategory.UTILITY
    capabilities = ["note", "write", "update"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "note_id": {"type": "string"},
            "updates": {"type": "object"}
        },
        "required": ["note_id", "updates"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_note_repository(context)
        note = await repo.update_note(kwargs["note_id"], kwargs["updates"])
        return {"status": "SUCCESS", "note": note}
