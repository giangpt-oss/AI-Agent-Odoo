from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.providers.reminders.local import LocalReminderProvider
from app.services.time_parser import time_parser
from app.services.audit import audit_logger

def get_reminder_provider(context: SkillExecutionContext):
    return LocalReminderProvider(user_id=context.session.user_id, chat_id=context.session.chat_id)

class ReminderCreateSkill(BaseSkill):
    name = "create_reminder"
    description = "Tạo một lịch nhắc nhở."
    category = SkillCategory.UTILITY
    capabilities = ["reminder", "write", "create"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Nội dung cần nhắc"},
            "time_text": {"type": "string", "description": "Thời gian (vd: 'chiều mai', '17h')"},
            "task_id": {"type": "string", "description": "ID của task (tùy chọn)"},
            "recurrence": {"type": "string", "enum": ["daily", "weekly", "monthly", "weekdays"], "description": "Chu kỳ lặp lại (tùy chọn)"}
        },
        "required": ["title", "time_text"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_reminder_provider(context)
        tz = getattr(context.session, "metadata", {}).get("timezone", "Asia/Ho_Chi_Minh") if context.session else "Asia/Ho_Chi_Minh"
        
        parsed = time_parser.parse_semantic_time(kwargs["time_text"], tz)
        if parsed["is_ambiguous"]:
            return {"status": "AMBIGUOUS_TIME", "message": "Không rõ thời gian nhắc, vui lòng cụ thể hơn."}
            
        rem = await provider.create_reminder(
            task_id=kwargs.get("task_id"),
            title=kwargs["title"],
            remind_at=parsed["datetime"],
            timezone=parsed["timezone"],
            recurrence=kwargs.get("recurrence")
        )
        
        audit_logger.log_external_action(
            action="CREATE_REMINDER",
            skill=self.name,
            provider="LocalReminderProvider",
            resource_id=rem["id"],
            user_id=context.session.user_id,
            status="SUCCESS"
        )
        return {"status": "SUCCESS", "reminder": rem}

class ReminderListSkill(BaseSkill):
    name = "list_reminders"
    description = "Xem danh sách nhắc nhở."
    category = SkillCategory.UTILITY
    capabilities = ["reminder", "read", "list"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["SCHEDULED", "TRIGGERED", "CANCELLED", "EXPIRED"]}
        }
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_reminder_provider(context)
        return await provider.list_reminders(kwargs.get("status"))

class ReminderUpdateSkill(BaseSkill):
    name = "update_reminder"
    description = "Sửa thông tin nhắc nhở."
    category = SkillCategory.UTILITY
    capabilities = ["reminder", "write", "update"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "reminder_id": {"type": "string"},
            "updates": {"type": "object"}
        },
        "required": ["reminder_id", "updates"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_reminder_provider(context)
        updates = dict(kwargs["updates"])
        
        if "time_text" in updates:
            tz = getattr(context.session, "metadata", {}).get("timezone", "Asia/Ho_Chi_Minh") if context.session else "Asia/Ho_Chi_Minh"
            parsed = time_parser.parse_semantic_time(updates["time_text"], tz)
            if parsed["is_ambiguous"]:
                return {"status": "AMBIGUOUS_TIME", "message": "Vui lòng nhập thời gian cụ thể."}
            if not parsed["is_ambiguous"]:
                updates["remind_at"] = parsed["datetime"]
                updates["timezone"] = parsed["timezone"]
            del updates["time_text"]
            
        rem = await provider.update_reminder(kwargs["reminder_id"], updates)
        return {"status": "SUCCESS", "reminder": rem}

class ReminderCancelSkill(BaseSkill):
    name = "cancel_reminder"
    description = "Hủy bỏ một lịch nhắc."
    category = SkillCategory.UTILITY
    capabilities = ["reminder", "write", "cancel"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "reminder_id": {"type": "string"}
        },
        "required": ["reminder_id"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_reminder_provider(context)
        success = await provider.cancel_reminder(kwargs["reminder_id"])
        
        if success:
            audit_logger.log_external_action(
                action="CANCEL_REMINDER",
                skill=self.name,
                provider="LocalReminderProvider",
                resource_id=kwargs["reminder_id"],
                user_id=context.session.user_id,
                status="SUCCESS"
            )
            return {"status": "SUCCESS"}
        return {"status": "FAILED"}
