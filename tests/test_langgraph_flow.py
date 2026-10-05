"""Test LangGraph StateGraph, Intent Analysis, and Permission Auto-Rejection."""
import pytest
from langchain_core.messages import HumanMessage
from app.agent.graph import agent_runnable


@pytest.mark.asyncio
async def test_langgraph_read_permission_granted():
    """Test luồng xem đơn hàng với user CÓ QUYỀN sales_user."""
    initial_state = {
        "session_id": "sess-001",
        "employee_id": "emp-123",
        "telegram_chat_id": 999999,
        "roles": ["sales_user"],
        "messages": [HumanMessage(content="Cho tôi xem 5 đơn hàng gần nhất của khách ABC")],
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
    config = {"configurable": {"thread_id": "telegram-chat-999999"}}
    with patch("app.connectors.odoo.connector.OdooConnector.search_read", new_callable=AsyncMock) as mock_odoo:
        mock_odoo.return_value = [{"id": 1, "name": "SO001"}]
        final_state = await agent_runnable.ainvoke(initial_state, config=config)

        assert final_state["current_intent"] == "get_sales_orders"
        assert final_state["permission_granted"] is True
        assert final_state["denial_reason"] is None
        assert "Kết quả từ hệ thống" in final_state["final_response"]
    print("\n[OK] Authorized read request passed through LangGraph successfully")


@pytest.mark.asyncio
async def test_langgraph_unauthorized_auto_rejection():
    """Test luồng tạo đơn hàng với user KHÔNG CÓ QUYỀN (chỉ có quyền warehouse_user).
    Hệ thống PHẢI TỰ ĐỘNG TỪ CHỐI NGAY LẬP TỨC.
    """
    initial_state = {
        "session_id": "sess-002",
        "employee_id": "emp-456",
        "telegram_chat_id": 888888,
        "roles": ["warehouse_user"],  # Không có sales_write hoặc sales_manager
        "messages": [HumanMessage(content="Tạo đơn hàng 50 triệu cho khách XYZ")],
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

    config = {"configurable": {"thread_id": "telegram-chat-888888"}}
    final_state = await agent_runnable.ainvoke(initial_state, config=config)

    assert final_state["current_intent"] == "create_sales_order"
    # Kiểm tra cờ phân quyền bị từ chối
    assert final_state["permission_granted"] is False
    assert final_state["pending_tool_name"] is None
    assert "Tự động từ chối" in final_state["denial_reason"]
    assert "Tự động từ chối" in final_state["final_response"]
    print("[OK] Unauthorized request auto-rejected immediately by Permission Guard")
