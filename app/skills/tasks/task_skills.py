from typing import Any, List, Dict
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.providers.tasks.local import LocalTaskProvider
from app.services.time_parser import time_parser
from app.services.audit import audit_logger

def get_task_provider(context: SkillExecutionContext):
    return LocalTaskProvider()

class TaskCreateSkill(BaseSkill):
    name = "create_task"
    description = "Tạo một task mới. Nếu có deadline, truyền dưới dạng natural language."
    category = SkillCategory.UTILITY
    capabilities = ["task", "write", "create"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH", "URGENT"], "default": "NORMAL"},
            "due_text": {"type": "string", "description": "Thời gian (vd: 'chiều mai', '17h thứ Sáu'). Có thể trống."},
            "project": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["title"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_task_provider(context)
        
        due_at = None
        tz = getattr(context.session, "metadata", {}).get("timezone", "Asia/Ho_Chi_Minh") if context.session else "Asia/Ho_Chi_Minh"
        
        if kwargs.get("due_text"):
            parsed = time_parser.parse_semantic_time(kwargs["due_text"], tz)
            if parsed["is_ambiguous"]:
                return {"status": "AMBIGUOUS_TIME", "message": "Không rõ thời gian, vui lòng cung cấp thời gian cụ thể hơn (giờ, ngày tháng chuẩn)."}
            due_at = parsed["datetime"]
            tz = parsed["timezone"]
            
        task = await provider.create_task(
            title=kwargs["title"],
            description=kwargs.get("description", ""),
            priority=kwargs.get("priority", "NORMAL"),
            due_at=due_at,
            timezone=tz,
            project=kwargs.get("project"),
            tags=kwargs.get("tags", [])
        )
        
        audit_logger.log_external_action(
            action="CREATE_TASK",
            skill=self.name,
            provider="LocalTaskProvider",
            resource_id=task["id"],
            user_id=context.session.user_id,
            status="SUCCESS"
        )
        return {"status": "SUCCESS", "task": task}

class TaskListSkill(BaseSkill):
    name = "list_tasks"
    description = "Lấy danh sách task dựa trên bộ lọc."
    category = SkillCategory.UTILITY
    capabilities = ["task", "read", "list"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["TODO", "IN_PROGRESS", "WAITING", "DONE", "CANCELLED"]},
            "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH", "URGENT"]}
        }
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_task_provider(context)
        return await provider.list_tasks(kwargs)

class TaskUpdateSkill(BaseSkill):
    name = "update_task"
    description = "Cập nhật task."
    category = SkillCategory.UTILITY
    capabilities = ["task", "write", "update"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "updates": {"type": "object"}
        },
        "required": ["task_id", "updates"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_task_provider(context)
        updates = kwargs["updates"]
        
        if "due_text" in updates:
            tz = getattr(context.session, "metadata", {}).get("timezone", "Asia/Ho_Chi_Minh") if context.session else "Asia/Ho_Chi_Minh"
            parsed = time_parser.parse_semantic_time(updates["due_text"], tz)
            if not parsed["is_ambiguous"]:
                updates["due_at"] = parsed["datetime"]
                updates["timezone"] = parsed["timezone"]
            del updates["due_text"]
            
        task = await provider.update_task(kwargs["task_id"], updates)
        return {"status": "SUCCESS", "task": task}

class TaskCompleteSkill(BaseSkill):
    name = "complete_task"
    description = "Đánh dấu task là DONE."
    category = SkillCategory.UTILITY
    capabilities = ["task", "write", "complete"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"}
        },
        "required": ["task_id"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_task_provider(context)
        task = await provider.complete_task(kwargs["task_id"])
        
        audit_logger.log_external_action(
            action="COMPLETE_TASK",
            skill=self.name,
            provider="LocalTaskProvider",
            resource_id=task["id"],
            user_id=context.session.user_id,
            status="SUCCESS"
        )
        return {"status": "SUCCESS", "task": task}

class TaskDeleteSkill(BaseSkill):
    name = "delete_task"
    description = "Xóa vĩnh viễn một task. Yêu cầu xác nhận."
    category = SkillCategory.UTILITY
    capabilities = ["task", "write", "delete"]
    operation_type = OperationType.DESTRUCTIVE
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"}
        },
        "required": ["task_id"]
    }
    output_schema = {"type": "object"}

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_task_provider(context)
        task = await provider.get_task(kwargs["task_id"])
        return {
            "action": "DELETE_TASK",
            "task_id": kwargs["task_id"],
            "task_title": task["title"],
            "warning": "Hành động này sẽ xóa vĩnh viễn task."
        }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_task_provider(context)
        success = await provider.delete_task(kwargs["task_id"])
        if success:
            audit_logger.log_external_action(
                action="DELETE_TASK",
                skill=self.name,
                provider="LocalTaskProvider",
                resource_id=kwargs["task_id"],
                user_id=context.session.user_id,
                status="SUCCESS"
            )
            return {"status": "SUCCESS"}
        return {"status": "FAILED"}

class TaskBulkCreateSkill(BaseSkill):
    name = "bulk_create_tasks"
    description = "Tạo nhiều task cùng lúc. Yêu cầu xác nhận nếu lớn hơn 3 tasks."
    category = SkillCategory.UTILITY
    capabilities = ["task", "write", "create", "bulk"]
    operation_type = OperationType.WRITE
    requires_confirmation = True # Automatically gate all bulk create for safety
    input_schema = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "due_text": {"type": "string"},
                        "project": {"type": "string"}
                    },
                    "required": ["title"]
                }
            }
        },
        "required": ["tasks"]
    }
    output_schema = {"type": "object"}

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        tasks = kwargs["tasks"]
        return {
            "action": "BULK_CREATE_TASKS",
            "count": len(tasks),
            "preview": [t["title"] for t in tasks],
            "warning": f"Sẽ tạo {len(tasks)} tasks."
        }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        tasks = kwargs["tasks"]
        provider = get_task_provider(context)
        tz = getattr(context.session, "metadata", {}).get("timezone", "Asia/Ho_Chi_Minh") if context.session else "Asia/Ho_Chi_Minh"
        
        results = []
        for t in tasks:
            due_at = None
            if t.get("due_text"):
                parsed = time_parser.parse_semantic_time(t["due_text"], tz)
                if not parsed["is_ambiguous"]:
                    due_at = parsed["datetime"]
                    
            task = await provider.create_task(
                title=t["title"],
                description=t.get("description", ""),
                priority="NORMAL",
                due_at=due_at,
                timezone=tz,
                project=t.get("project"),
                tags=[]
            )
            results.append(task)
            
        audit_logger.log_external_action(
            action="BULK_CREATE_TASKS",
            skill=self.name,
            provider="LocalTaskProvider",
            resource_id=f"bulk_{len(results)}",
            user_id=context.session.user_id,
            status="SUCCESS"
        )
        return {"status": "SUCCESS", "created": len(results), "tasks": results}

