import logging
from typing import Any
from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    """Ghi vết kiểm toán (Audit Trail) các hoạt động của Agent và vi phạm quyền hạn."""

    @staticmethod
    async def log_event(
        telegram_chat_id: int,
        request_id: str,
        user_prompt: str | None = None,
        detected_intent: str | None = None,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        status: str = "SUCCESS",
        error_message: str | None = None,
        execution_time_ms: float | None = None,
    ) -> None:
        """Ghi nhận log vào database. Bắt ngoại lệ để không bao giờ làm gián đoạn luồng chính."""
        try:
            async with AsyncSessionLocal() as session:
                log_entry = AuditLog(
                    telegram_chat_id=telegram_chat_id,
                    request_id=request_id,
                    user_prompt=user_prompt,
                    detected_intent=detected_intent,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    status=status,
                    error_message=error_message,
                    execution_time_ms=execution_time_ms,
                )
                session.add(log_entry)
                await session.commit()
        except Exception as e:
            # Fallback ghi ra console log nếu database tạm thời bận
            logger.info(
                f"[AuditLog Fallback] chat_id={telegram_chat_id} req_id={request_id} "
                f"tool={tool_name} status={status} error={error_message} (db_err={e})"
            )


audit_service = AuditService()
