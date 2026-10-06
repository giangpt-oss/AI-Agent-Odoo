"""Tests for User Self-Confirmation Workflow in LangGraph."""
import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import HumanMessage
from app.agent.graph import agent_runnable


@pytest.mark.asyncio
async def test_write_action_triggers_user_confirmation():
    """Hành động ghi dữ liệu phải dừng lại và yêu cầu người dùng xác nhận thông số."""
    thread_id = "thread-confirm-001"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "session_id": "sess-c1",
        "employee_id": "emp-admin",
        "telegram_chat_id": 777777,
        "roles": ["sales_manager"],
        "messages": [HumanMessage(content="Tạo đơn hàng partner_id=42")],
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

    state_after_turn1 = await agent_runnable.ainvoke(initial_state, config=config)

    # Đảm bảo dừng lại chờ xác nhận
    assert state_after_turn1["permission_granted"] is True
    assert state_after_turn1["requires_user_confirmation"] is True
    assert state_after_turn1["tool_result"] is None
    assert "Xác nhận thực hiện hành động ghi dữ liệu" in state_after_turn1["final_response"]
    print("\n[OK] Write action prompted user for confirmation before executing")


@pytest.mark.asyncio
async def test_user_confirms_and_executes():
    """Người dùng nhắn 'Đồng ý' -> Hệ thống tiến hành thực thi Tool."""
    thread_id = "thread-confirm-002"
    config = {"configurable": {"thread_id": thread_id}}

    # Turn 1: Yêu cầu tạo
    initial_state = {
        "session_id": "sess-c2",
        "employee_id": "emp-admin",
        "telegram_chat_id": 777777,
        "roles": ["sales_manager"],
        "messages": [HumanMessage(content="Tạo đơn hàng partner_id=42")],
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
    await agent_runnable.ainvoke(initial_state, config=config)

    # Turn 2: Người dùng xác nhận "Đồng ý"
    confirm_state = {
        "messages": [HumanMessage(content="Đồng ý")],
        "pending_tool_name": "create_sales_order",
        "is_write_action": True,
        "pending_tool_args": {"partner_id": 42},
    }

    with patch("app.tools.odoo_tools.get_odoo_connector") as mock_get_conn:
        mock_conn = AsyncMock()
        mock_conn.create_record.return_value = 8888
        mock_get_conn.return_value = mock_conn

        final_state = await agent_runnable.ainvoke(confirm_state, config=config)

        assert final_state["user_confirmed"] is True
        assert final_state["tool_result"] is not None
        assert final_state["tool_result"]["data"]["order_id"] == 8888
        assert "Kết quả từ hệ thống" in final_state["final_response"]
    print("[OK] User confirmed write action executed successfully")


@pytest.mark.asyncio
async def test_user_cancels_action():
    """Người dùng nhắn 'Hủy' -> Hệ thống dừng lại và không gọi Tool."""
    thread_id = "thread-confirm-003"
    config = {"configurable": {"thread_id": thread_id}}

    cancel_state = {
        "messages": [HumanMessage(content="Hủy bỏ")],
        "pending_tool_name": "create_sales_order",
        "is_write_action": True,
        "roles": ["sales_manager"],
        "permission_granted": True,
        "pending_tool_args": {"partner_id": 42},
        "confirmation_requested_at": __import__("time").time(),
        "confirmation_payload": {"tool": "create_sales_order", "arguments": {"partner_id": 42}},
    }

    final_state = await agent_runnable.ainvoke(cancel_state, config=config)

    assert final_state.get("user_confirmed") is False
    assert final_state.get("tool_result") is None
    assert "Bạn đã hủy bỏ hành động" in final_state["final_response"]
    print("[OK] User cancellation handled safely without calling external API")
