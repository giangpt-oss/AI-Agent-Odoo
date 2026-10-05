"""Test Tool system abstraction, registry, input validation, and execution."""
import pytest
from pydantic import ValidationError
from langchain_core.messages import HumanMessage

from app.tools.registry import tool_registry
from app.tools.odoo_tools import GetSalesOrdersTool, CreateSalesOrderTool
from app.tools.base import ToolContext
from app.agent.graph import agent_runnable


def test_tool_registry_registration_and_filtering():
    tools = tool_registry.all_tools()
    tool_names = [t.name for t in tools]
    assert "get_sales_orders" in tool_names
    assert "create_sales_order" in tool_names
    assert "search_gmail" in tool_names

    # Kiểm tra lọc tool theo role
    sales_tools = tool_registry.list_tools_for_roles(["sales_user"])
    sales_tool_names = [t.name for t in sales_tools]
    assert "get_sales_orders" in sales_tool_names
    assert "create_sales_order" not in sales_tool_names  # Yêu cầu sales_write

    manager_tools = tool_registry.list_tools_for_roles(["sales_manager"])
    manager_tool_names = [t.name for t in manager_tools]
    assert "create_sales_order" in manager_tool_names
    print("\n[OK] Tool registry and role filtering verified")


def test_tool_input_validation():
    tool = GetSalesOrdersTool()
    # Hợp lệ
    valid = tool.validate_args(limit=10, query="ABC")
    assert valid.limit == 10

    # Không hợp lệ: limit vượt quá 50
    with pytest.raises(ValidationError):
        tool.validate_args(limit=100)
    print("[OK] Pydantic input schema validation verified")


@pytest.mark.asyncio
async def test_langgraph_full_flow_with_tool_execution():
    """User có quyền gọi xem đơn hàng -> LangGraph phân tích -> Cho phép -> Gọi Tool -> Định dạng kết quả."""
    initial_state = {
        "session_id": "sess-flow-001",
        "employee_id": "emp-001",
        "telegram_chat_id": 111222,
        "roles": ["sales_user"],
        "messages": [HumanMessage(content="Xem danh sách đơn hàng công ty ABC")],
        "current_intent": None,
        "extracted_slots": {},
        "missing_slots": [],
        "pending_tool_name": None,
        "pending_tool_args": None,
        "is_write_action": False,
        "permission_granted": False,
        "denial_reason": None,
        "requires_user_confirmation": False,
        "user_confirmed": None,
        "tool_result": None,
        "final_response": None,
        "error": None,
    }

    from unittest.mock import AsyncMock, patch
    config = {"configurable": {"thread_id": "thread-flow-001"}}
    mock_records = [
        {"id": 101, "name": "SO00101", "amount_total": 45000000},
        {"id": 102, "name": "SO00102", "amount_total": 12000000},
    ]

    with patch("app.connectors.odoo.connector.OdooConnector.search_read", new_callable=AsyncMock) as mock_odoo:
        mock_odoo.return_value = mock_records
        final_state = await agent_runnable.ainvoke(initial_state, config=config)

        assert final_state["permission_granted"] is True
        assert final_state["tool_result"] is not None
        assert final_state["tool_result"]["success"] is True
        assert len(final_state["tool_result"]["data"]) == 2
        assert "SO00101" in str(final_state["final_response"])
    print("[OK] LangGraph end-to-end tool execution flow verified")
