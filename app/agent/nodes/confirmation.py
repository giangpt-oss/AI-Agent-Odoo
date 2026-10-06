import time
from copy import deepcopy
from typing import Any
from langchain_core.messages import AIMessage, HumanMessage
from app.agent.state import AgentState
from app.agent.nodes.analyzer import CONFIRM_WORDS, CANCEL_WORDS

async def confirmation_guard_node(state: AgentState) -> dict[str, Any]:
    args = state.get("pending_tool_args") or state.get("extracted_slots", {})
    payload = {"tool": state.get("pending_tool_name"), "arguments": args}
    requested_at = state.get("confirmation_requested_at")
    text = next((str(m.content).lower().strip() for m in reversed(state.get("messages", [])) if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human"), "")
    if requested_at and (time.time() - requested_at > 300 or state.get("confirmation_payload") != payload):
        text = "hủy"
    if requested_at and text in CANCEL_WORDS:
        msg = "🚫 Bạn đã hủy bỏ hành động hoặc yêu cầu đã hết hạn. Không có thay đổi nào được thực hiện."
        return {"requires_user_confirmation": False, "user_confirmed": False, "pending_tool_name": None,
                "confirmation_requested_at": None, "confirmation_payload": None,
                "is_write_action": False, "final_response": msg, "messages": [AIMessage(content=msg)]}
    if requested_at and text in CONFIRM_WORDS:
        return {"requires_user_confirmation": False, "user_confirmed": True}
    # A new action ALWAYS asks first, regardless of text in the initial request.
    msg = f"⚠️ **Xác nhận thực hiện hành động ghi dữ liệu**\n- Thao tác: {payload['tool']}\n- Tham số: {args}\nTrả lời 'Đồng ý' hoặc 'Hủy' trong 5 phút."
    return {"requires_user_confirmation": True, "user_confirmed": None,
            "confirmation_requested_at": time.time(), "confirmation_payload": deepcopy(payload),
            "final_response": msg, "messages": [AIMessage(content=msg)]}
