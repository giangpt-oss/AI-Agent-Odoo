import re
from typing import Any
from langchain_core.messages import HumanMessage
from app.agent.state import AgentState

CONFIRM_WORDS = {"đồng ý", "xác nhận", "yes", "ok", "confirm", "tiến hành"}
CANCEL_WORDS = {"hủy", "huỷ", "hủy bỏ", "cancel", "thôi", "bỏ qua", "không", "không đồng ý"}

async def analyzer_node(state: AgentState) -> dict[str, Any]:
    messages = state.get("messages", [])
    text = next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human"), "")
    lower = text.lower().strip()
    if state.get("confirmation_requested_at") and state.get("pending_tool_name"):
        if lower in CONFIRM_WORDS | CANCEL_WORDS:
            return {"requires_user_confirmation": True, "user_confirmed": None, "tool_result": None, "error": None}

    result = {
        "current_intent": "general_chat", "extracted_slots": {}, "missing_slots": [],
        "pending_tool_name": None, "pending_tool_args": None, "is_write_action": False,
        "requires_user_confirmation": False, "user_confirmed": None, "tool_result": None,
        "error": None, "final_response": None, "confirmation_requested_at": None,
        "confirmation_payload": None,
    }
    if any(k in lower for k in ["đơn hàng", "sales order", "sale order", "đơn bán"]):
        if any(k in lower for k in ["tạo", "thêm", "create", "new"]):
            args = {}
            match = re.search(r"partner_id\s*[=:]\s*([1-9]\d*)", lower)
            if match:
                args["partner_id"] = int(match.group(1))
            amount = re.search(r"(\d+(?:[.,]\d+)?)\s*(triệu|trieu|tỷ|ty|nghìn|nghin)", lower)
            if amount:
                scale = {"triệu": 10**6, "trieu": 10**6, "tỷ": 10**9, "ty": 10**9, "nghìn": 1000, "nghin": 1000}[amount.group(2)]
                args["amount_total"] = float(amount.group(1).replace(",", ".")) * scale
            missing = ([] if "partner_id" in args else ["partner_id"])
            if amount:
                missing.append("order_lines")
            result.update(current_intent="create_sales_order", pending_tool_name="create_sales_order",
                          pending_tool_args=args, extracted_slots=args, missing_slots=missing,
                          is_write_action=True)
            if missing:
                result["final_response"] = "Chưa thể tạo đơn hàng: cần ID khách hàng đã xác minh (partner_id=...) và các dòng sản phẩm/số lượng/đơn giá nếu có giá trị đơn. Bot sẽ không tự chọn khách hoặc tự gán số tiền."
            return result
        result.update(current_intent="get_sales_orders", pending_tool_name="get_sales_orders",
                      pending_tool_args={"limit": 5}, extracted_slots={"limit": 5})
        return result
    if any(k in lower for k in ["email", "gmail", "thư"]):
        result.update(current_intent="search_gmail", pending_tool_name="search_gmail", extracted_slots={"query": text})
    return result
