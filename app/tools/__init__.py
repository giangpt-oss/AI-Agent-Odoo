from app.tools.base import BaseTool, ToolContext, ToolResult
from app.tools.registry import tool_registry, ToolRegistry
from app.tools.odoo_tools import (
    GetSalesOrdersTool,
    CreateSalesOrderTool,
    GetOpportunitiesTool,
    GetPartnersTool,
    GetEmployeesTool,
)
from app.tools.google_tools import SearchGmailTool

# Đăng ký các công cụ mặc định vào Registry
tool_registry.register(GetSalesOrdersTool())
tool_registry.register(CreateSalesOrderTool())
tool_registry.register(GetOpportunitiesTool())
tool_registry.register(GetPartnersTool())
tool_registry.register(GetEmployeesTool())
tool_registry.register(SearchGmailTool())

__all__ = [
    "BaseTool",
    "ToolContext",
    "ToolResult",
    "ToolRegistry",
    "tool_registry",
    "GetSalesOrdersTool",
    "CreateSalesOrderTool",
    "GetOpportunitiesTool",
    "GetPartnersTool",
    "GetEmployeesTool",
    "SearchGmailTool",
]
