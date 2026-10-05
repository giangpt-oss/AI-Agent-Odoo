from typing import Any
from langchain_core.messages import AIMessage, HumanMessage
from app.agent.state import AgentState


async def confirmation_guard_node(state: AgentState) -> dict[str, Any]:
    """Node xử lý xác nhận 2 bước từ chính người dùng cho các Write Actions.
    Không cần chờ Manager duyệt - chỉ cần chính người dùng xác nhận thông số.
    """
    is_write = state.get("is_write_action", False)
    pending_tool = state.get("pending_tool_name")
    tool_args = state.get("pending_tool_args") or state.get("extracted_slots", {})
    user_confirmed = state.get("user_confirmed")

    # Nếu không phải hành động ghi dữ liệu hoặc đã được xác nhận từ trước -> Cho qua
    if not is_write or user_confirmed is True:
        return {
            "requires_user_confirmation": False,
        }

    # Kiểm tra tin nhắn người dùng mới nhất xem có phải câu trả lời xác nhận không
    messages = state.get("messages", [])
    last_user_msg = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            last_user_msg = str(msg.content).lower().strip()
            break

    # Nếu người dùng bấm/gõ xác nhận
    if any(k in last_user_msg for k in ["đồng ý", "xác nhận", "yes", "ok", "confirm", "tiến hành"]):
        return {
            "requires_user_confirmation": False,
            "user_confirmed": True,
        }

    # Nếu người dùng hủy
    if any(k in last_user_msg for k in ["hủy", "cancel", "thôi", "bỏ qua", "không"]):
        cancel_msg = "🚫 Bạn đã hủy bỏ hành động. Không có thay đổi nào được thực hiện trên hệ thống ERP."
        return {
            "requires_user_confirmation": False,
            "user_confirmed": False,
            "pending_tool_name": None,
            "is_write_action": False,
            "final_response": cancel_msg,
            "messages": [AIMessage(content=cancel_msg)],
        }

    # Chưa xác nhận -> Đặt câu hỏi xác nhận cho người dùng
    confirm_prompt = (
        f"⚠️ **Xác nhận thực hiện hành động ghi dữ liệu**\n\n"
        f"- Thao tác: `{pending_tool}`\n"
        f"- Tham số: `{tool_args}`\n\n"
        f"Bạn có chắc chắn muốn thực hiện hành động này không?\n"
        f"👉 Trả lời: **'Đồng ý'** để tiếp tục hoặc **'Hủy'** để dừng lại."
    )

    return {
        "requires_user_confirmation": True,
        "user_confirmed": None,
        "final_response": confirm_prompt,
        "messages": [AIMessage(content=confirm_prompt)],
    }
