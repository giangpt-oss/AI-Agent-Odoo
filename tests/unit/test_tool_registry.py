import pytest
from app.domain.gateway_context import GatewayPermission
from app.domain.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    build_default_tool_registry,
)


def test_tool_registry_registration():
    registry = ToolRegistry()
    tool = ToolDefinition(
        name="test.read",
        category="test",
        action_type="read",
        required_permission=GatewayPermission.TASKS_READ,
        description="Đọc công việc thử nghiệm",
        parameter_schema={"type": "object", "properties": {}},
    )
    registry.register(tool)
    assert registry.get_tool("test.read") == tool
    assert registry.get_tool("non.existent") is None


def test_tool_registry_default_tools():
    registry = build_default_tool_registry()

    # Phải có đầy đủ các phân hệ cốt lõi
    core_tools = [
        "identity.whoami",
        "connections.status",
        "briefing.today",
        "calendar.events.list",
        "calendar.events.create",
        "calendar.events.update",
        "gmail.messages.unread",
        "gmail.messages.send",
        "crm.leads.search",
        "crm.leads.create",
        "crm.leads.update",
        "tasks.personal.list",
        "tasks.personal.create",
        "tasks.personal.update",
        "tasks.personal.done",
    ]
    for tool_name in core_tools:
        tool = registry.get_tool(tool_name)
        assert tool is not None, f"Tool {tool_name} must be registered"
        assert tool.name == tool_name
        assert tool.action_type in ("read", "write")
        assert len(tool.description) > 0


def test_tool_registry_filtering_by_permissions():
    registry = build_default_tool_registry()

    # User chỉ có quyền tasks.read
    tools = registry.list_tools(user_permissions=["tasks.read"])
    tool_names = [t.name for t in tools]

    # Các công cụ không cần quyền đặc biệt (danh tính, trạng thái, briefing) luôn sẵn sàng
    assert "identity.whoami" in tool_names
    assert "connections.status" in tool_names
    assert "tasks.personal.list" in tool_names

    # Không có quyền write hoặc calendar thì không thể dùng các tool đó
    assert "calendar.events.create" not in tool_names
    assert "tasks.personal.create" not in tool_names


def test_generate_capability_overview_dynamic():
    registry = build_default_tool_registry()

    overview_all = registry.generate_capability_overview(
        user_permissions=["calendar.events", "gmail.readonly", "crm.read", "tasks.read", "tasks.write"]
    )
    assert "Xin chào! Tôi có thể hỗ trợ bạn các công việc sau:" in overview_all
    assert "Điểm tin hàng ngày" in overview_all
    assert "Google Calendar" in overview_all
    assert "Odoo CRM" in overview_all
    assert "Công việc cá nhân" in overview_all
    assert "Bản xem trước" in overview_all or "xác nhận" in overview_all

    # Kiểm tra không chứa các ký tự Markdown thô ** hoặc *
    assert "**" not in overview_all
    assert "*" not in overview_all
