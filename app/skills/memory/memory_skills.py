from typing import Any
from app.skills.base import BaseSkill, SkillCategory, OperationType, RiskLevel
from app.models.context import SkillExecutionContext

from app.memory.models import MemoryRecord, MemoryCategory, MemorySourceType, MemoryScope
from app.memory.service import memory_service, SensitiveMemoryError

class MemoryCreateSkill(BaseSkill):
    name = "memory_create"
    description = "Ghi nhớ một sở thích, cài đặt, hoặc bối cảnh của người dùng (Ví dụ: định dạng báo cáo mặc định, giọng văn email)."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.WRITE
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["WRITE_MEMORY"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string", 
                "enum": [MemoryCategory.PREFERENCE, MemoryCategory.SETTING, MemoryCategory.WORKFLOW, MemoryCategory.COMMUNICATION_STYLE, MemoryCategory.PROJECT_CONTEXT, MemoryCategory.NAMING_CONVENTION, MemoryCategory.TEMPORARY_CONTEXT]
            },
            "key": {"type": "string", "description": "Tên định danh (VD: default_report_format)"},
            "value": {"type": "object", "description": "Giá trị lưu trữ"},
            "summary": {"type": "string", "description": "Mô tả ngắn gọn"},
            "is_global": {"type": "boolean", "description": "Nếu true, áp dụng cho mọi workspace"},
            "expires_at": {"type": "string", "description": "ISO format date nếu là temporary"}
        },
        "required": ["category", "key", "value", "summary"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        try:
            scope = MemoryScope.GLOBAL if kwargs.get("is_global") else MemoryScope.WORKSPACE
            workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
            
            record = MemoryRecord(
                user_id=context.session.user_id,
                workspace_id=workspace_id if scope == MemoryScope.WORKSPACE else None,
                scope=scope,
                category=kwargs["category"],
                key=kwargs["key"],
                value=kwargs["value"],
                summary=kwargs["summary"],
                source_type=MemorySourceType.USER_EXPLICIT,
                expires_at=kwargs.get("expires_at")
            )
            
            saved = memory_service.save_memory(record)
            return {"status": "SUCCESS", "message": f"Đã ghi nhớ: {saved.summary}", "memory_id": saved.memory_id}
        except SensitiveMemoryError as e:
            return {"status": "ERROR", "message": str(e)}

class MemorySearchSkill(BaseSkill):
    name = "memory_search"
    description = "Tìm kiếm các sở thích, cài đặt, hoặc bối cảnh đã được ghi nhớ."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.READ
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["READ_MEMORY"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"}
        },
        "required": ["query"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        results = memory_service.search_memories(kwargs["query"], context.session.user_id, workspace_id)
        return {
            "status": "SUCCESS",
            "memories": [{"id": r.memory_id, "key": r.key, "summary": r.summary, "value": r.value} for r in results]
        }

class MemoryDeleteSkill(BaseSkill):
    name = "memory_delete"
    description = "Xóa (quên) một sở thích, cài đặt hoặc bối cảnh đã ghi nhớ."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.DESTRUCTIVE
    risk_level = RiskLevel.LOW
    requires_confirmation = True
    capabilities = ["DELETE_MEMORY"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string"}
        },
        "required": ["memory_id"]
    }
    output_schema = {"type": "object"}
    
    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        mem = memory_service.get_memory(kwargs["memory_id"], context.session.user_id)
        if not mem: return {"warning": "Memory không tồn tại hoặc không có quyền truy cập."}
        return {"warning": f"Bạn có chắc muốn quên: {mem.summary}?"}
        
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        success = memory_service.delete_memory(kwargs["memory_id"], context.session.user_id)
        if success:
            return {"status": "SUCCESS", "message": "Đã xóa memory thành công."}
        return {"status": "ERROR", "message": "Không thể xóa memory (không tồn tại hoặc không đủ quyền)."}
