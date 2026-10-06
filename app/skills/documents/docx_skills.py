from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.services.docx_service import docx_service

class DocxReadSkill(BaseSkill):
    name = "read_docx"
    description = "Đọc nội dung văn bản từ file Word (.docx)."
    category = SkillCategory.DOCUMENT
    capabilities = ["docx", "read", "word"]
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
        return docx_service.read_docx(kwargs["filepath"])

class DocxCreateSkill(BaseSkill):
    name = "create_docx"
    description = "Tạo file Word (.docx) mới."
    category = SkillCategory.DOCUMENT
    capabilities = ["docx", "create", "word", "write"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"}
        },
        "required": ["filepath", "title", "content"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return docx_service.create_docx(kwargs["filepath"], kwargs["title"], kwargs["content"])

class DocxEditSkill(BaseSkill):
    name = "edit_docx"
    description = "Sửa file Word hiện tại (thêm văn bản hoặc thay thế từ ngữ) mà không làm mất định dạng cũ."
    category = SkillCategory.DOCUMENT
    capabilities = ["docx", "edit", "word", "update"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "operation": {"type": "string", "enum": ["append", "replace"]},
            "text": {"type": "string", "description": "Văn bản để thêm hoặc văn bản thay thế"},
            "old_text": {"type": "string", "description": "Văn bản cũ cần thay thế (chỉ dùng khi operation='replace')"}
        },
        "required": ["filepath", "operation", "text"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        op = kwargs["operation"]
        if op == "append":
            return docx_service.append_docx(kwargs["filepath"], kwargs["text"])
        elif op == "replace":
            old_text = kwargs.get("old_text", "")
            if not old_text:
                return "Error: old_text is required for replace operation"
            return docx_service.replace_text_docx(kwargs["filepath"], old_text, kwargs["text"])
