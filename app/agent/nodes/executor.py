from typing import Any
from app.agent.state import AgentState
from app.tools.base import ToolContext
from app.tools.registry import tool_registry


async def executor_node(state: AgentState) -> dict[str, Any]:
    """Node thực thi Tool được chọn sau khi đã vượt qua Permission Guard."""
    tool_name = state.get("pending_tool_name")
    if not tool_name:
        return {
            "tool_result": None,
            "error": "Không có tool nào được chỉ định để thực thi."
        }

    tool = tool_registry.get_tool(tool_name)
    if not tool:
        return {
            "tool_result": None,
            "error": f"Tool '{tool_name}' không tồn tại trong hệ thống."
        }

    # Tạo ngữ cảnh thực thi an toàn
    context = ToolContext(
        employee_id=state.get("employee_id", "anonymous"),
        telegram_chat_id=state.get("telegram_chat_id", 0),
        roles=state.get("roles", []),
    )

    args = state.get("pending_tool_args") or state.get("extracted_slots", {})

    try:
        result = await tool.execute(context, **args)
        return {
            "tool_result": result.model_dump(),
            "error": result.error,
        }
    except Exception as e:
        return {
            "tool_result": None,
            "error": f"Lỗi khi thực thi tool '{tool_name}': {str(e)}",
        }
