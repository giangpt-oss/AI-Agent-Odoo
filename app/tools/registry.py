import logging
from typing import Type
from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry quản lý toàn bộ các Tools có sẵn trong hệ thống."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool_instance: BaseTool) -> None:
        """Đăng ký một instance của tool vào registry."""
        if tool_instance.name in self._tools:
            logger.warning(f"Tool {tool_instance.name} is already registered. Overwriting.")
        self._tools[tool_instance.name] = tool_instance
        logger.info(f"Registered tool: {tool_instance.name}")

    def get_tool(self, name: str) -> BaseTool | None:
        """Lấy tool theo tên."""
        return self._tools.get(name)

    def list_tools_for_roles(self, user_roles: list[str]) -> list[BaseTool]:
        """Lấy danh sách các Tool mà user có quyền truy cập."""
        roles_set = set(user_roles)
        available = []
        for tool in self._tools.values():
            if not tool.required_roles or any(r in roles_set for r in tool.required_roles) or ("admin" in roles_set):
                available.append(tool)
        return available

    def all_tools(self) -> list[BaseTool]:
        """Danh sách tất cả các tools."""
        return list(self._tools.values())


# Singleton Tool Registry
tool_registry = ToolRegistry()
