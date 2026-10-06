from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.providers.meetings.local import LocalMeetingRepository
from app.services.time_parser import time_parser

def get_meeting_repository(context: SkillExecutionContext):
    return LocalMeetingRepository()

class MeetingCreateSkill(BaseSkill):
    name = "create_meeting"
    description = "Tạo một cuộc họp mới trong hệ thống."
    category = SkillCategory.UTILITY
    capabilities = ["meeting", "write", "create"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start_time_text": {"type": "string", "description": "Thời gian bắt đầu (vd: 'chiều mai 2h')"},
            "end_time_text": {"type": "string", "description": "Thời gian kết thúc (tùy chọn)"},
            "participants": {"type": "array", "items": {"type": "string"}, "description": "Danh sách người tham gia"},
            "agenda": {"type": "array", "items": {"type": "string"}, "description": "Nội dung/agenda"},
            "source_type": {"type": "string", "description": "Nguồn gốc (vd: 'calendar', 'manual')"},
            "source_ids": {"type": "array", "items": {"type": "string"}, "description": "Danh sách ID nguồn (calendar_event_id, ...)"}
        },
        "required": ["title"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_meeting_repository(context)
        tz = getattr(context.session, "metadata", {}).get("timezone", "Asia/Ho_Chi_Minh") if context.session else "Asia/Ho_Chi_Minh"
        
        start_at = None
        end_at = None
        
        if kwargs.get("start_time_text"):
            parsed = time_parser.parse_semantic_time(kwargs["start_time_text"], tz)
            if parsed["is_ambiguous"]:
                return {"status": "AMBIGUOUS_TIME", "message": "Không rõ thời gian bắt đầu."}
            start_at = parsed["datetime"]
            tz = parsed["timezone"] # inherit timezone from parsed start
            
        if kwargs.get("end_time_text"):
            parsed_end = time_parser.parse_semantic_time(kwargs["end_time_text"], tz)
            if not parsed_end["is_ambiguous"]:
                end_at = parsed_end["datetime"]

        meeting = await repo.create_meeting(
            title=kwargs["title"],
            start_at=start_at,
            end_at=end_at,
            timezone=tz,
            participants=kwargs.get("participants", []),
            agenda=kwargs.get("agenda", []),
            source_type=kwargs.get("source_type", "manual"),
            source_ids=kwargs.get("source_ids", [])
        )
        return {"status": "SUCCESS", "meeting": meeting}

class MeetingGetSkill(BaseSkill):
    name = "get_meeting"
    description = "Lấy thông tin chi tiết một cuộc họp."
    category = SkillCategory.UTILITY
    capabilities = ["meeting", "read", "get"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"}
        },
        "required": ["meeting_id"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_meeting_repository(context)
        meeting = await repo.get_meeting(kwargs["meeting_id"])
        return {"status": "SUCCESS", "meeting": meeting}

class MeetingListSkill(BaseSkill):
    name = "list_meetings"
    description = "Tìm kiếm và liệt kê cuộc họp."
    category = SkillCategory.UTILITY
    capabilities = ["meeting", "read", "list", "search"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "participant": {"type": "string"},
            "status": {"type": "string", "enum": ["PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"]}
        }
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_meeting_repository(context)
        return await repo.list_meetings(kwargs)

class MeetingUpdateSkill(BaseSkill):
    name = "update_meeting"
    description = "Cập nhật thông tin cuộc họp."
    category = SkillCategory.UTILITY
    capabilities = ["meeting", "write", "update"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
            "updates": {"type": "object"}
        },
        "required": ["meeting_id", "updates"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_meeting_repository(context)
        meeting = await repo.update_meeting(kwargs["meeting_id"], kwargs["updates"])
        return {"status": "SUCCESS", "meeting": meeting}

class MeetingCompleteSkill(BaseSkill):
    name = "complete_meeting"
    description = "Đánh dấu cuộc họp đã hoàn thành."
    category = SkillCategory.UTILITY
    capabilities = ["meeting", "write", "complete"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"}
        },
        "required": ["meeting_id"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        repo = get_meeting_repository(context)
        meeting = await repo.update_meeting(kwargs["meeting_id"], {"status": "COMPLETED"})
        return {"status": "SUCCESS", "meeting": meeting}
