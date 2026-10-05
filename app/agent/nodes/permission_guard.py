from typing import Any
from app.agent.state import AgentState
from app.security.permissions import permission_engine
from app.services.audit import audit_service


async def permission_guard_node(state: AgentState) -> dict[str, Any]:
    """Kiểm tra quyền hạn người dùng trước khi gọi Tool (Layer 1 Security).
    Sử dụng PermissionEngine: Mọi yêu cầu vượt quyền đều TỰ ĐỘNG TỪ CHỐI NGAY LẬP TỨC
    và được ghi vết vào Audit Trail.
    """
    intent = state.get("current_intent") or "general_chat"
    tool_name = state.get("pending_tool_name") or intent
    user_roles = state.get("roles", [])
    chat_id = state.get("telegram_chat_id", 0)
    session_id = state.get("session_id", "no-session")

    # Đánh giá qua Permission Engine
    decision = permission_engine.evaluate(tool_name=tool_name, user_roles=user_roles)

    if not decision.allowed:
        # Ghi vết vi phạm phân quyền vào Audit Log
        await audit_service.log_event(
            telegram_chat_id=chat_id,
            request_id=session_id,
            detected_intent=intent,
            tool_name=tool_name,
            status="PERMISSION_DENIED",
            error_message=decision.reason,
        )

        return {
            "permission_granted": False,
            "denial_reason": decision.reason,
            "pending_tool_name": None,
            "is_write_action": decision.is_write,
        }

    return {
        "permission_granted": True,
        "denial_reason": None,
        "is_write_action": decision.is_write,
    }
