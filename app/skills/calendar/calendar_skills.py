from typing import Any, List, Dict
from datetime import datetime
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.providers.calendar.fake_calendar import FakeCalendarProvider
from app.services.audit import audit_logger
from app.core.exceptions import SkillValidationError

# In a real system, the provider would be fetched from context or factory based on user preferences.
def get_calendar_provider(context: SkillExecutionContext):
    provider = context.providers.get("calendar")
    if provider is None:
        raise RuntimeError("Calendar provider is not connected. Please connect an account first.")
    return provider

class CalendarListEventsSkill(BaseSkill):
    name = "list_calendar_events"
    description = "Liệt kê các sự kiện trên lịch trong một khoảng thời gian."
    category = SkillCategory.UTILITY
    capabilities = ["calendar", "read", "events"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "start_time": {"type": "string", "description": "ISO format datetime"},
            "end_time": {"type": "string", "description": "ISO format datetime"},
            "limit": {"type": "integer", "default": 10},
            "query": {"type": "string", "description": "Từ khóa tìm kiếm (tùy chọn)"}
        },
        "required": ["start_time", "end_time"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_calendar_provider(context)
        try:
            start_dt = datetime.fromisoformat(kwargs["start_time"].replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(kwargs["end_time"].replace("Z", "+00:00"))
        except ValueError:
            raise SkillValidationError("Invalid datetime format. Please use ISO format.")
            
        return provider.list_events(start_dt, end_dt, kwargs.get("limit", 10), kwargs.get("query"))

class CalendarGetEventSkill(BaseSkill):
    name = "get_calendar_event"
    description = "Lấy thông tin chi tiết của một sự kiện cụ thể."
    category = SkillCategory.UTILITY
    capabilities = ["calendar", "read", "event"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"}
        },
        "required": ["event_id"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_calendar_provider(context)
        return provider.get_event(kwargs["event_id"])

class CalendarFindFreeTimeSkill(BaseSkill):
    name = "find_free_time"
    description = "Tìm khoảng thời gian rảnh trên lịch."
    category = SkillCategory.UTILITY
    capabilities = ["calendar", "read", "freebusy"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "start_time": {"type": "string", "description": "ISO format datetime"},
            "end_time": {"type": "string", "description": "ISO format datetime"},
            "duration_minutes": {"type": "integer"}
        },
        "required": ["start_time", "end_time", "duration_minutes"]
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_calendar_provider(context)
        start_dt = datetime.fromisoformat(kwargs["start_time"].replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(kwargs["end_time"].replace("Z", "+00:00"))
        return provider.find_free_time(start_dt, end_dt, kwargs["duration_minutes"])

class CalendarCreateEventSkill(BaseSkill):
    name = "create_calendar_event"
    description = "Tạo một sự kiện mới trên lịch. Yêu cầu xác nhận."
    category = SkillCategory.UTILITY
    capabilities = ["calendar", "write", "create"]
    operation_type = OperationType.EXTERNAL_ACTION
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start_time": {"type": "string", "description": "ISO format datetime"},
            "end_time": {"type": "string", "description": "ISO format datetime"},
            "timezone": {"type": "string", "description": "Timezone name (e.g. Asia/Ho_Chi_Minh)"},
            "location": {"type": "string"},
            "description": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["title", "start_time", "end_time", "timezone"]
    }
    output_schema = {"type": "object"}

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        return {
            "action": "CREATE_EVENT",
            "title": kwargs["title"],
            "start": kwargs["start_time"],
            "end": kwargs["end_time"],
            "timezone": kwargs["timezone"],
            "attendees": kwargs.get("attendees", [])
        }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_calendar_provider(context)
        start_dt = datetime.fromisoformat(kwargs["start_time"].replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(kwargs["end_time"].replace("Z", "+00:00"))
        
        evt_id = provider.create_event(
            title=kwargs["title"],
            start_time=start_dt,
            end_time=end_dt,
            timezone=kwargs["timezone"],
            location=kwargs.get("location"),
            description=kwargs.get("description"),
            attendees=kwargs.get("attendees")
        )
        
        audit_logger.log_external_action(
            action="CREATE_EVENT",
            skill=self.name,
            provider="FakeCalendarProvider",
            resource_id=evt_id,
            user_id=context.session.user_id,
            status="SUCCESS"
        )
        return {"status": "SUCCESS", "event_id": evt_id}

class CalendarUpdateEventSkill(BaseSkill):
    name = "update_calendar_event"
    description = "Cập nhật một sự kiện đã có. Yêu cầu xác nhận."
    category = SkillCategory.UTILITY
    capabilities = ["calendar", "write", "update"]
    operation_type = OperationType.EXTERNAL_ACTION
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "updates": {"type": "object", "description": "Dictionary of fields to update"}
        },
        "required": ["event_id", "updates"]
    }
    output_schema = {"type": "object"}

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_calendar_provider(context)
        old_event = provider.get_event(kwargs["event_id"])
        
        return {
            "action": "UPDATE_EVENT",
            "event_id": kwargs["event_id"],
            "before": old_event,
            "after_updates": kwargs["updates"]
        }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_calendar_provider(context)
        evt_id = provider.update_event(kwargs["event_id"], kwargs["updates"])
        
        audit_logger.log_external_action(
            action="UPDATE_EVENT",
            skill=self.name,
            provider="FakeCalendarProvider",
            resource_id=evt_id,
            user_id=context.session.user_id,
            status="SUCCESS"
        )
        return {"status": "SUCCESS", "event_id": evt_id}

class CalendarCancelEventSkill(BaseSkill):
    name = "cancel_calendar_event"
    description = "Hủy một sự kiện. Yêu cầu xác nhận."
    category = SkillCategory.UTILITY
    capabilities = ["calendar", "write", "delete"]
    operation_type = OperationType.DESTRUCTIVE
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"}
        },
        "required": ["event_id"]
    }
    output_schema = {"type": "object"}

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_calendar_provider(context)
        event = provider.get_event(kwargs["event_id"])
        
        warning = ""
        if event.get("attendees"):
            warning = "CẢNH BÁO: Hủy sự kiện này có thể gửi email thông báo cho những người tham gia."
            
        return {
            "action": "CANCEL_EVENT",
            "event_id": kwargs["event_id"],
            "event_title": event.get("title"),
            "warning": warning
        }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_calendar_provider(context)
        success = provider.cancel_event(kwargs["event_id"])
        
        if success:
            audit_logger.log_external_action(
                action="CANCEL_EVENT",
                skill=self.name,
                provider="FakeCalendarProvider",
                resource_id=kwargs["event_id"],
                user_id=context.session.user_id,
                status="SUCCESS"
            )
            return {"status": "SUCCESS"}
        else:
            return {"status": "FAILED", "message": "Event not found or could not be cancelled."}
