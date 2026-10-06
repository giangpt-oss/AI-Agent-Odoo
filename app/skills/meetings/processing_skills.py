from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.services.meeting_processing import meeting_processor

class MeetingPreparationSkill(BaseSkill):
    name = "prepare_meeting"
    description = "Chuẩn bị cho cuộc họp bằng cách tổng hợp documents, emails và tasks liên quan."
    category = SkillCategory.UTILITY
    capabilities = ["meeting", "read", "prepare"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
            "context_hints": {"type": "array", "items": {"type": "string"}, "description": "Gợi ý để tìm kiếm context"}
        },
        "required": ["meeting_id"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        # Mock logic. In reality it searches repo/email/calendar.
        return {
            "status": "SUCCESS",
            "preparation": {
                "meeting_summary": "Tóm tắt dự kiến",
                "agenda": [],
                "participant_context": [],
                "relevant_documents": [],
                "relevant_email_threads": [],
                "open_items": [],
                "suggested_questions": []
            }
        }

class MeetingNotesProcessSkill(BaseSkill):
    name = "process_meeting_notes"
    description = "Xử lý raw text/transcript của cuộc họp thành cấu trúc JSON decisions và action items."
    category = SkillCategory.UTILITY
    capabilities = ["meeting", "read", "process"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "transcript": {"type": "string"}
        },
        "required": ["transcript"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        ai_client = context.providers.get("ai_client")
        res = await meeting_processor.process_large_transcript(kwargs["transcript"], ai_client)
        return {"status": "SUCCESS", "structured_data": res}

class MeetingMinutesSkill(BaseSkill):
    name = "generate_meeting_minutes"
    description = "Tạo biên bản họp Markdown từ structured notes."
    category = SkillCategory.UTILITY
    capabilities = ["meeting", "read", "minutes"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string"},
            "processed_notes": {"type": "object"}
        },
        "required": ["meeting_id", "processed_notes"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        data = kwargs["processed_notes"]
        
        md = "# Meeting Minutes\n\n"
        md += "## Discussion Summary\n"
        md += data.get("summary", "") + "\n\n"
        
        md += "## Decisions\n"
        for d in data.get("decisions", []):
            md += f"- {d['decision']}\n"
            
        md += "\n## Action Items\n"
        for a in data.get("action_items", []):
            md += f"- {a['task']}\n"
            
        return {"status": "SUCCESS", "markdown": md}
