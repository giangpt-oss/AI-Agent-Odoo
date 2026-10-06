from typing import Any, List
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.providers.email.fake_email import FakeEmailProvider
from app.services.audit import audit_logger

# In a real system, the provider would be fetched from context or factory based on user preferences.
# For P1A, we will use FakeEmailProvider directly per test-driven instruction unless configured otherwise.
def get_email_provider(context: SkillExecutionContext):
    provider = context.providers.get("email")
    if provider is None:
        raise RuntimeError("Email provider is not connected. Please connect an account first.")
    return provider

class EmailSearchSkill(BaseSkill):
    name = "search_email"
    description = "Tìm kiếm email trong hộp thư theo từ khoá, người gửi, chủ đề."
    category = SkillCategory.UTILITY
    capabilities = ["email", "search", "read"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Từ khóa tìm kiếm (ví dụ: 'from:boss@company.com subject:báo cáo')"},
            "limit": {"type": "integer", "default": 10, "maximum": 50}
        },
        "required": ["query"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_email_provider(context)
        return provider.search_messages(kwargs["query"], kwargs.get("limit", 10))

class EmailReadSkill(BaseSkill):
    name = "read_email"
    description = "Đọc nội dung chi tiết của một email cụ thể."
    category = SkillCategory.UTILITY
    capabilities = ["email", "read"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "ID của email cần đọc"}
        },
        "required": ["message_id"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_email_provider(context)
        return provider.get_message(kwargs["message_id"])

class EmailDraftSkill(BaseSkill):
    name = "draft_email"
    description = "Soạn thảo một email nháp (không gửi)."
    category = SkillCategory.UTILITY
    capabilities = ["email", "write", "draft"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"}},
            "cc": {"type": "array", "items": {"type": "string"}},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "reply_to_message_id": {"type": "string"}
        },
        "required": ["to", "subject", "body"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_email_provider(context)
        draft_id = provider.create_draft(
            to=kwargs["to"],
            subject=kwargs["subject"],
            body=kwargs["body"],
            cc=kwargs.get("cc"),
            reply_to_message_id=kwargs.get("reply_to_message_id")
        )
        return {"status": "SUCCESS", "draft_id": draft_id}

class EmailSendSkill(BaseSkill):
    name = "send_email"
    description = "Gửi một email mới. Bắt buộc phải có xác nhận."
    category = SkillCategory.UTILITY
    capabilities = ["email", "send", "write"]
    operation_type = OperationType.EXTERNAL_ACTION
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"}},
            "cc": {"type": "array", "items": {"type": "string"}},
            "subject": {"type": "string"},
            "body": {"type": "string"}
        },
        "required": ["to", "subject", "body"]
    }
    output_schema = {"type": "object"}

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        return {
            "action": "SEND_EMAIL",
            "to": kwargs["to"],
            "cc": kwargs.get("cc", []),
            "subject": kwargs["subject"],
            "body_preview": kwargs["body"][:200] + "..." if len(kwargs["body"]) > 200 else kwargs["body"]
        }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_email_provider(context)
        msg_id = provider.send_message(
            to=kwargs["to"],
            subject=kwargs["subject"],
            body=kwargs["body"],
            cc=kwargs.get("cc")
        )
        # Log to audit (Requirement 18)
        audit_logger.log_external_action(
            action="SEND_EMAIL",
            skill=self.name,
            provider="FakeEmailProvider",
            resource_id=msg_id,
            user_id=context.session.user_id,
            status="SUCCESS"
        )
        return {"status": "SUCCESS", "message_id": msg_id}

class EmailReplySkill(BaseSkill):
    name = "reply_email"
    description = "Trả lời một email hiện có."
    category = SkillCategory.UTILITY
    capabilities = ["email", "reply", "write"]
    operation_type = OperationType.EXTERNAL_ACTION
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "original_message_id": {"type": "string"},
            "thread_id": {"type": "string"},
            "body": {"type": "string"}
        },
        "required": ["original_message_id", "thread_id", "body"]
    }
    output_schema = {"type": "object"}

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        return {
            "action": "REPLY_EMAIL",
            "reply_to": kwargs["original_message_id"],
            "thread": kwargs["thread_id"],
            "body_preview": kwargs["body"][:200]
        }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        provider = get_email_provider(context)
        msg_id = provider.reply_message(
            original_message_id=kwargs["original_message_id"],
            thread_id=kwargs["thread_id"],
            body=kwargs["body"]
        )
        audit_logger.log_external_action(
            action="REPLY_EMAIL",
            skill=self.name,
            provider="FakeEmailProvider",
            resource_id=msg_id,
            user_id=context.session.user_id,
            status="SUCCESS"
        )
        return {"status": "SUCCESS", "message_id": msg_id}
