from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

from app.domain.gateway_context import GatewayPermission


class ToolDefinition(BaseModel):
    name: str
    category: str
    action_type: Literal["read", "write"]
    required_permission: Optional[str] = None
    description: str
    parameter_schema: dict[str, Any] = Field(default_factory=dict)


class ToolRegistry:
    """Registry duy nhất quản lý toàn bộ công cụ nghiệp vụ của Trợ lý AI."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        if name in self._tools:
            return self._tools[name]
        aliases = {
            "gmail.send": "gmail.messages.send",
            "gmail.unread": "gmail.messages.unread",
            "calendar.create": "calendar.events.create",
            "calendar.update": "calendar.events.update",
            "crm.create": "crm.leads.create",
            "crm.update": "crm.leads.update",
            "crm.write": "crm.leads.update",
            "tasks.create": "tasks.personal.create",
            "tasks.update": "tasks.personal.update",
            "tasks.done": "tasks.personal.done",
            "tasks.complete": "tasks.personal.done",
            "odoo.partners": "odoo.partners.search",
            "odoo.products": "odoo.products.search",
            "odoo.sales": "odoo.sales.search",
            "odoo.purchases": "odoo.purchases.search",
            "odoo.inventory": "odoo.inventory.search",
            "odoo.invoices": "odoo.invoices.search",
            "odoo.employees": "odoo.employees.search",
        }
        canonical = aliases.get(name)
        if canonical and canonical in self._tools:
            return self._tools[canonical]
        return None

    def list_all_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def list_tools(self, user_permissions: list[str]) -> list[ToolDefinition]:
        perms_set = set(user_permissions or [])
        return [
            tool
            for tool in self._tools.values()
            if tool.required_permission is None or tool.required_permission in perms_set
        ]

    def generate_capability_overview(self, user_permissions: Optional[list[str]] = None) -> str:
        """Sinh tài liệu giới thiệu khả năng trực tiếp từ danh mục tool đã đăng ký và lọc theo quyền."""
        perms = set(user_permissions or []) if user_permissions is not None else None
        lines = [
            "👋 Xin chào! Tôi có thể hỗ trợ bạn các công việc sau:",
            "",
            "• 📅 Điểm tin hàng ngày: Gửi /today hoặc hỏi \"Hôm nay tôi có gì?\" để xem lịch họp, email và việc cần làm.",
            "• 👤 Thông tin cá nhân: Hỏi \"Tôi là ai?\" hoặc \"Hồ sơ của tôi\" để xem tài khoản Odoo đã liên kết.",
            "• 🔗 Kết nối dịch vụ: Hỏi \"Trạng thái kết nối\" để kiểm tra liên kết Google Workspace và Odoo.",
        ]

        if perms is None or any("calendar" in p for p in perms):
            lines.append("• 📆 Google Calendar: Hỏi \"Xem lịch\" hoặc tạo sự kiện lịch mới.")

        if perms is None or any("gmail" in p for p in perms):
            lines.append("• ✉️ Hòm thư Gmail: Hỏi \"Thư chưa đọc\" hoặc soạn thảo gửi email công việc.")

        if perms is None or any("crm" in p for p in perms):
            lines.append("• 💼 Odoo CRM: Tra cứu khách hàng/cơ hội qua lệnh /crm search <từ khóa> hoặc tạo cơ hội mới.")

        if perms is None or any("task" in p for p in perms):
            lines.append("• 📋 Công việc cá nhân: Xem việc bằng /task list, tạo việc mới hoặc đánh dấu hoàn thành.")

        lines.append("• 🏢 Dữ liệu Odoo: Tra cứu khách hàng/đối tác, danh mục sản phẩm, đơn bán/mua, kho bãi, hóa đơn hoặc danh bạ nhân viên.")

        lines.extend([
            "",
            "💡 Lưu ý an toàn: Mọi thao tác thêm mới hoặc cập nhật dữ liệu đều luôn gửi Bản xem trước (Preview) để bạn xác nhận trước khi thực hiện.",
        ])
        return "\n".join(lines)


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    # 1. Identity & System (Không yêu cầu quyền đặc biệt)
    registry.register(
        ToolDefinition(
            name="identity.whoami",
            category="identity",
            action_type="read",
            required_permission=None,
            description="Xem thông tin danh tính, phòng ban và quyền hạn của người dùng đã xác thực",
            parameter_schema={"type": "object", "properties": {}},
        )
    )
    registry.register(
        ToolDefinition(
            name="connections.status",
            category="identity",
            action_type="read",
            required_permission=None,
            description="Kiểm tra trạng thái kết nối Google Workspace, Odoo và Telegram",
            parameter_schema={"type": "object", "properties": {}},
        )
    )
    registry.register(
        ToolDefinition(
            name="briefing.today",
            category="briefing",
            action_type="read",
            required_permission=None,
            description="Tổng hợp điểm tin hàng ngày gồm lịch họp hôm nay, thư chưa đọc và việc đến hạn",
            parameter_schema={"type": "object", "properties": {}},
        )
    )

    # 2. Google Calendar
    registry.register(
        ToolDefinition(
            name="calendar.events.list",
            category="calendar",
            action_type="read",
            required_permission=GatewayPermission.CALENDAR_READ,
            description="Tra cứu danh sách sự kiện lịch họp trong khoảng thời gian",
            parameter_schema={
                "type": "object",
                "properties": {
                    "time_min": {"type": "string", "description": "Thời gian bắt đầu ISO"},
                    "time_max": {"type": "string", "description": "Thời gian kết thúc ISO"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="calendar.events.create",
            category="calendar",
            action_type="write",
            required_permission=GatewayPermission.CALENDAR_WRITE,
            description="Tạo sự kiện lịch Google Calendar mới",
            parameter_schema={
                "type": "object",
                "required": ["summary", "start_time", "end_time"],
                "properties": {
                    "summary": {"type": "string", "description": "Tiêu đề cuộc họp"},
                    "start_time": {"type": "string", "description": "Thời gian bắt đầu"},
                    "end_time": {"type": "string", "description": "Thời gian kết thúc"},
                    "description": {"type": "string", "description": "Mô tả chi tiết"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="calendar.events.update",
            category="calendar",
            action_type="write",
            required_permission=GatewayPermission.CALENDAR_WRITE,
            description="Cập nhật hoặc dời thời gian cuộc họp Google Calendar",
            parameter_schema={
                "type": "object",
                "required": ["event_id"],
                "properties": {
                    "event_id": {"type": "string", "description": "ID cuộc họp"},
                    "start_time": {"type": "string", "description": "Thời gian bắt đầu mới"},
                    "end_time": {"type": "string", "description": "Thời gian kết thúc mới"},
                    "summary": {"type": "string", "description": "Tiêu đề mới nếu có"},
                },
            },
        )
    )

    # 3. Gmail
    registry.register(
        ToolDefinition(
            name="gmail.messages.unread",
            category="gmail",
            action_type="read",
            required_permission=GatewayPermission.GMAIL_READ,
            description="Xem danh sách email chưa đọc gần đây trong hòm thư",
            parameter_schema={
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "default": 5},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="gmail.messages.send",
            category="gmail",
            action_type="write",
            required_permission=GatewayPermission.GMAIL_SEND,
            description="Gửi email mới qua tài khoản Gmail",
            parameter_schema={
                "type": "object",
                "required": ["recipient", "subject", "body"],
                "properties": {
                    "recipient": {"type": "string", "description": "Địa chỉ email người nhận"},
                    "subject": {"type": "string", "description": "Tiêu đề email"},
                    "body": {"type": "string", "description": "Nội dung email"},
                },
            },
        )
    )

    # 4. Odoo CRM
    registry.register(
        ToolDefinition(
            name="crm.leads.search",
            category="crm",
            action_type="read",
            required_permission=GatewayPermission.CRM_READ,
            description="Tra cứu cơ hội hoặc khách hàng trong Odoo CRM theo từ khóa",
            parameter_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Tên khách hàng hoặc cơ hội"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="crm.leads.create",
            category="crm",
            action_type="write",
            required_permission=GatewayPermission.CRM_CREATE,
            description="Tạo cơ hội kinh doanh mới trong Odoo CRM",
            parameter_schema={
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "Tên cơ hội / khách hàng"},
                    "phone": {"type": "string", "description": "Số điện thoại"},
                    "email": {"type": "string", "description": "Email liên hệ"},
                    "expected_revenue": {"type": "number", "description": "Doanh thu kỳ vọng"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="crm.leads.update",
            category="crm",
            action_type="write",
            required_permission=GatewayPermission.CRM_WRITE,
            description="Cập nhật giai đoạn hoặc thông tin cơ hội Odoo CRM",
            parameter_schema={
                "type": "object",
                "required": ["lead_id"],
                "properties": {
                    "lead_id": {"type": "integer", "description": "ID cơ hội CRM"},
                    "stage_id": {"type": "integer", "description": "ID giai đoạn mới"},
                    "expected_revenue": {"type": "number", "description": "Doanh thu cập nhật"},
                },
            },
        )
    )

    # 5. Personal Tasks
    registry.register(
        ToolDefinition(
            name="tasks.personal.list",
            category="task",
            action_type="read",
            required_permission=GatewayPermission.TASKS_READ,
            description="Tra cứu danh sách công việc cá nhân của người dùng",
            parameter_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Lọc theo trạng thái"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="tasks.personal.create",
            category="task",
            action_type="write",
            required_permission=GatewayPermission.TASKS_WRITE,
            description="Tạo công việc cá nhân mới",
            parameter_schema={
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string", "description": "Tiêu đề công việc"},
                    "due_date": {"type": "string", "description": "Hạn chót hoàn thành"},
                    "priority": {"type": "string", "description": "Mức độ ưu tiên"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="tasks.personal.update",
            category="task",
            action_type="write",
            required_permission=GatewayPermission.TASKS_WRITE,
            description="Cập nhật tiêu đề hoặc thời hạn công việc cá nhân",
            parameter_schema={
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "task_id": {"type": "string", "description": "Mã công việc"},
                    "title": {"type": "string", "description": "Tiêu đề mới"},
                    "due_date": {"type": "string", "description": "Hạn chót mới"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="tasks.personal.done",
            action_type="write",
            category="task",
            required_permission=GatewayPermission.TASKS_WRITE,
            description="Đánh dấu hoàn thành một công việc cá nhân",
            parameter_schema={
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "task_id": {"type": "string", "description": "Mã công việc hoàn thành"},
                },
            },
        )
    )

    # 6. Odoo Enterprise Data Queries (Quyền được Gateway & Odoo ACL trực tiếp kiểm soát)
    registry.register(
        ToolDefinition(
            name="odoo.partners.search",
            category="odoo",
            action_type="read",
            required_permission=None,
            description="Tìm kiếm thông tin khách hàng, nhà cung cấp hoặc đối tác kinh doanh trên Odoo",
            parameter_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Tên, email hoặc số điện thoại đối tác cần tìm"},
                    "limit": {"type": "integer", "description": "Số lượng bản ghi tối đa (mặc định 10)"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="odoo.products.search",
            category="odoo",
            action_type="read",
            required_permission=None,
            description="Tra cứu danh mục sản phẩm, mã SKU, giá bán niêm yết và số lượng tồn kho trên Odoo",
            parameter_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Tên hoặc mã sản phẩm/barcode cần tìm"},
                    "limit": {"type": "integer", "description": "Số lượng sản phẩm tối đa (mặc định 10)"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="odoo.sales.search",
            category="odoo",
            action_type="read",
            required_permission=None,
            description="Tra cứu danh sách đơn bán hàng và báo giá (sale orders) trên Odoo",
            parameter_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Số đơn hàng hoặc tên khách hàng"},
                    "state": {"type": "string", "description": "Trạng thái đơn (draft, sent, sale, done, cancel)"},
                    "limit": {"type": "integer", "description": "Số lượng đơn tối đa (mặc định 10)"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="odoo.purchases.search",
            category="odoo",
            action_type="read",
            required_permission=None,
            description="Tra cứu danh sách đơn mua hàng từ nhà cung cấp (purchase orders) trên Odoo",
            parameter_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Số PO hoặc tên nhà cung cấp"},
                    "state": {"type": "string", "description": "Trạng thái đơn mua (draft, sent, purchase, done, cancel)"},
                    "limit": {"type": "integer", "description": "Số lượng đơn tối đa (mặc định 10)"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="odoo.inventory.search",
            category="odoo",
            action_type="read",
            required_permission=None,
            description="Tra cứu phiếu điều chuyển kho (nhập, xuất, nội bộ) hoặc số lượng tồn kho trên Odoo",
            parameter_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Mã phiếu kho hoặc nguồn gốc đơn hàng"},
                    "state": {"type": "string", "description": "Trạng thái phiếu kho (assigned, done, confirmed, draft)"},
                    "limit": {"type": "integer", "description": "Số lượng phiếu tối đa (mặc định 10)"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="odoo.invoices.search",
            category="odoo",
            action_type="read",
            required_permission=None,
            description="Tra cứu hóa đơn bán hàng hoặc hóa đơn mua hàng và trạng thái thanh toán trên Odoo",
            parameter_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Số hóa đơn hoặc tên đối tác"},
                    "payment_state": {"type": "string", "description": "Tình trạng thanh toán (not_paid, in_payment, paid, partial)"},
                    "limit": {"type": "integer", "description": "Số lượng hóa đơn tối đa (mặc định 10)"},
                },
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="odoo.employees.search",
            category="odoo",
            action_type="read",
            required_permission=None,
            description="Tra cứu danh bạ nhân viên, chức danh công việc, phòng ban và email nội bộ trên Odoo",
            parameter_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Tên nhân viên, phòng ban hoặc chức danh"},
                    "limit": {"type": "integer", "description": "Số lượng nhân viên tối đa (mặc định 10)"},
                },
            },
        )
    )

    return registry


global_tool_registry = build_default_tool_registry()
