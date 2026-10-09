from typing import Annotated, Any
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def trim_messages_reducer(existing: list[BaseMessage], new: list[BaseMessage]) -> list[BaseMessage]:
    """Giới hạn lịch sử hội thoại tối đa 20 tin nhắn gần nhất để ngăn MemorySaver phình to RAM."""
    all_msgs = add_messages(existing, new)
    MAX_HISTORY = 20
    if len(all_msgs) > MAX_HISTORY:
        return all_msgs[-MAX_HISTORY:]
    return all_msgs


class AgentState(TypedDict):
    """Trạng thái lõi của AI Agent truyền qua lại giữa các nodes trong LangGraph."""

    # 1. Định danh ngữ cảnh
    session_id: str
    employee_id: str
    telegram_chat_id: int
    roles: list[str]  # Ví dụ: ["sales_user", "inventory_viewer"]

    # 2. Lịch sử tin nhắn (Được kiểm soát độ dài tối đa 20 messages)
    messages: Annotated[list[BaseMessage], trim_messages_reducer]

    # 3. Ý định & Tham số trích xuất (Slot-filling)
    current_intent: str | None
    extracted_slots: dict[str, Any]
    missing_slots: list[str]

    # 4. Tool được đề xuất
    pending_tool_name: str | None
    pending_tool_args: dict[str, Any] | None

    # 5. Phân quyền & Kiểm soát rủi ro
    is_write_action: bool
    permission_granted: bool
    denial_reason: str | None

    # 6. Xác nhận từ chính người dùng (User Self-Confirmation)
    requires_user_confirmation: bool
    user_confirmed: bool | None

    # 7. Kết quả thực thi
    tool_result: dict[str, Any] | None
    final_response: str | None
    error: str | None

    confirmation_requested_at: float | None
    confirmation_payload: dict[str, Any] | None
