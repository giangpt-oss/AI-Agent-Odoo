from typing import Any
from langchain_core.messages import AIMessage
from app.agent.state import AgentState


async def formatter_node(state: AgentState) -> dict[str, Any]:
    """Chuẩn hóa phản hồi cuối cùng gửi về cho người dùng Telegram."""
    # 1. Nếu bị từ chối phân quyền (Layer 1 Security Auto-Rejection)
    if not state.get("permission_granted", True) and state.get("denial_reason"):
        denial_msg = state["denial_reason"]
        return {
            "final_response": denial_msg,
            "messages": [AIMessage(content=denial_msg)],
        }

    # 2. Nếu đã có final_response từ confirmation_guard (hỏi xác nhận hoặc đã hủy)
    existing_resp = state.get("final_response")
    if existing_resp and (
        state.get("missing_slots")
        or state.get("requires_user_confirmation")
        or state.get("user_confirmed") is False
    ):
        return {
            "final_response": existing_resp,
            "messages": [AIMessage(content=existing_resp)],
        }

    # 3. Nếu có kết quả Tool
    tool_result = state.get("tool_result")
    if tool_result:
        status = "Thành công" if tool_result.get("success") else "Thất bại"
        data = tool_result.get("data") if tool_result.get("success") else tool_result.get("error")
        msg = f"Kết quả từ hệ thống ({status}):\n{data}"
        return {
            "final_response": msg,
            "messages": [AIMessage(content=msg)],
        }

    # 4. Nếu có lỗi
    if state.get("error"):
        err_msg = f"❌ Đã xảy ra lỗi: {state['error']}"
        return {
            "final_response": err_msg,
            "messages": [AIMessage(content=err_msg)],
        }

    # 5. Phản hồi mặc định
    intent = state.get("current_intent")
    default_text = f"Đã nhận diện yêu cầu: [{intent}]. Đang điều phối đến hệ thống chuyên trách..."
    return {
        "final_response": default_text,
        "messages": [AIMessage(content=default_text)],
    }
