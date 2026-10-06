from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.services.presentation_service import presentation_service

class PresentationReadSkill(BaseSkill):
    name = "read_presentation"
    description = "Đọc nội dung text và notes từ file PowerPoint (.pptx)."
    category = SkillCategory.DOCUMENT
    capabilities = ["pptx", "read", "powerpoint", "presentation"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"}
        },
        "required": ["filepath"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return presentation_service.read_pptx(kwargs["filepath"])

class PresentationCreateSkill(BaseSkill):
    name = "create_presentation"
    description = "Tạo file PowerPoint mới với slide tiêu đề."
    category = SkillCategory.DOCUMENT
    capabilities = ["pptx", "create", "powerpoint"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "title": {"type": "string"},
            "subtitle": {"type": "string"}
        },
        "required": ["filepath", "title", "subtitle"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return presentation_service.create_pptx(kwargs["filepath"], kwargs["title"], kwargs["subtitle"])

class PresentationEditSkill(BaseSkill):
    name = "edit_presentation"
    description = "Thêm slide nội dung mới vào file PowerPoint hiện tại."
    category = SkillCategory.DOCUMENT
    capabilities = ["pptx", "edit", "add_slide"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "title": {"type": "string", "description": "Tiêu đề của slide mới"},
            "content": {"type": "string", "description": "Nội dung của slide mới (có thể xuống dòng)"}
        },
        "required": ["filepath", "title", "content"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return presentation_service.add_slide(kwargs["filepath"], kwargs["title"], kwargs["content"])
