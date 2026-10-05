from typing import Any
from langchain_core.messages import HumanMessage
from app.agent.state import AgentState


async def analyzer_node(state: AgentState) -> dict[str, Any]:
    """Phân tích tin nhắn của người dùng để bóc tách:
    - Intent chính
    - Các tham số (slot filling)
    - Nhận biết có phải Write Action không
    """
    messages = state.get("messages", [])
    if not messages:
        return {
            "current_intent": "unknown",
            "extracted_slots": {},
            "missing_slots": [],
            "is_write_action": False,
            "pending_tool_name": None,
            "pending_tool_args": None,
        }

    # Lấy tin nhắn người dùng mới nhất
    last_user_msg = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            last_user_msg = str(msg.content)
            break

    lower_msg = last_user_msg.lower()

    # Kiểm tra nếu là phản hồi cho bước xác nhận (Đồng ý / Hủy)
    if state.get("requires_user_confirmation") or (state.get("pending_tool_name") and any(k in lower_msg for k in ["đồng ý", "xác nhận", "yes", "ok", "confirm", "hủy", "cancel", "thôi"])):
        return {
            "current_intent": state.get("current_intent") or "confirm_action",
            "is_write_action": True,
            "pending_tool_name": state.get("pending_tool_name"),
            "pending_tool_args": state.get("pending_tool_args"),
            "extracted_slots": state.get("extracted_slots", {}),
            "missing_slots": [],
            "requires_user_confirmation": False,
        }
    if any(k in lower_msg for k in ["đơn hàng", "sales order", "sale order", "đơn bán"]):
        if any(w in lower_msg for w in ["tạo", "thêm", "create", "new"]):
            # Write action: Tạo đơn hàng
            return {
                "current_intent": "create_sales_order",
                "is_write_action": True,
                "pending_tool_name": "create_sales_order",
                "pending_tool_args": {"partner_id": 1, "amount_total": 50000000.0},
                "extracted_slots": {"partner_id": 1, "amount_total": 50000000.0},
                "missing_slots": [],
                "requires_user_confirmation": True,
            }
        else:
            # Read action: Xem đơn hàng
            return {
                "current_intent": "get_sales_orders",
                "is_write_action": False,
                "pending_tool_name": "get_sales_orders",
                "pending_tool_args": {"query": last_user_msg, "limit": 5},
                "extracted_slots": {"query": last_user_msg, "limit": 5},
                "missing_slots": [],
                "requires_user_confirmation": False,
            }

    if any(k in lower_msg for k in ["hóa đơn", "invoice"]):
        return {
            "current_intent": "get_invoices",
            "is_write_action": False,
            "pending_tool_name": "get_invoices",
            "extracted_slots": {"query": last_user_msg},
            "missing_slots": [],
            "requires_user_confirmation": False,
        }

    if any(k in lower_msg for k in ["email", "gmail", "thư"]):
        return {
            "current_intent": "search_gmail",
            "is_write_action": False,
            "pending_tool_name": "search_gmail",
            "extracted_slots": {"query": last_user_msg},
            "missing_slots": [],
            "requires_user_confirmation": False,
        }

    # Trường hợp câu hỏi chung / chưa khớp tool
    return {
        "current_intent": "general_chat",
        "is_write_action": False,
        "pending_tool_name": None,
        "pending_tool_args": None,
        "extracted_slots": {},
        "missing_slots": [],
        "requires_user_confirmation": False,
    }
