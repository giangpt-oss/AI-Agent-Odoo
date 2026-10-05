from typing import Any
from pydantic import BaseModel, Field
from app.tools.base import BaseTool, ToolContext, ToolResult
from app.connectors.odoo.connector import get_odoo_connector
from app.connectors.odoo.client import OdooAccessDeniedException, OdooAPIException


# ============================================================================
# 1. ĐƠN BÁN HÀNG (SALES ORDERS)
# ============================================================================
class GetSalesOrdersInput(BaseModel):
    query: str | None = Field(default=None, description="Tên đối tác hoặc từ khóa tìm kiếm")
    partner_id: int | None = Field(default=None, description="ID khách hàng trong Odoo nếu có")
    limit: int = Field(default=5, ge=1, le=50, description="Số lượng đơn hàng tối đa cần lấy")


class GetSalesOrdersTool(BaseTool):
    name = "get_sales_orders"
    description = "Tra cứu danh sách đơn bán hàng (Sales Orders / Quotations) trên ERP Odoo"
    args_schema = GetSalesOrdersInput
    required_roles = ["sales_user", "sales_manager", "admin"]
    is_write_action = False

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        validated = self.validate_args(**kwargs)
        connector = get_odoo_connector()

        domain = []
        if validated.partner_id:
            domain.append(["partner_id", "=", validated.partner_id])
        elif validated.query:
            domain.append(["partner_id.name", "ilike", validated.query])

        fields = ["id", "name", "partner_id", "amount_total", "state", "date_order"]

        try:
            records = await connector.search_read(
                model="sale.order",
                domain=domain,
                fields=fields,
                limit=validated.limit,
            )
            return ToolResult(
                success=True,
                data=records,
                metadata={"source": "odoo_cloud", "count": len(records)},
            )
        except OdooAccessDeniedException as e:
            return ToolResult(
                success=False,
                error=f"Layer 2 Security Denied: {str(e)}",
                metadata={"security_layer": 2, "error_type": "ACCESS_DENIED"},
            )
        except OdooAPIException as e:
            return ToolResult(
                success=False,
                error=f"Lỗi hệ thống Odoo: {str(e)}",
                metadata={"error_type": "ODOO_API_ERROR"},
            )


class CreateSalesOrderInput(BaseModel):
    partner_id: int = Field(description="ID khách hàng trong Odoo")
    amount_total: float | None = Field(default=None, description="Tổng giá trị đơn hàng nếu có")
    notes: str | None = Field(default=None, description="Ghi chú đơn hàng")


class CreateSalesOrderTool(BaseTool):
    name = "create_sales_order"
    description = "Tạo báo giá/đơn hàng mới trên ERP Odoo"
    args_schema = CreateSalesOrderInput
    required_roles = ["sales_write", "sales_manager", "admin"]
    is_write_action = True

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        validated = self.validate_args(**kwargs)
        connector = get_odoo_connector()

        values = {
            "partner_id": validated.partner_id,
        }
        if validated.notes:
            values["note"] = validated.notes

        try:
            order_id = await connector.create_record(
                model="sale.order",
                values=values,
            )
            return ToolResult(
                success=True,
                data={"order_id": order_id, "status": "draft"},
                metadata={"source": "odoo_cloud", "action": "created"},
            )
        except OdooAccessDeniedException as e:
            return ToolResult(
                success=False,
                error=f"Layer 2 Security Denied: {str(e)}",
                metadata={"security_layer": 2, "error_type": "ACCESS_DENIED"},
            )
        except OdooAPIException as e:
            return ToolResult(
                success=False,
                error=f"Lỗi tạo đơn Odoo: {str(e)}",
                metadata={"error_type": "ODOO_API_ERROR"},
            )


# ============================================================================
# 2. CƠ HỘI KINH DOANH & PIPELINE (CRM LEADS & OPPORTUNITIES)
# ============================================================================
class GetOpportunitiesInput(BaseModel):
    query: str | None = Field(default=None, description="Tên cơ hội hoặc từ khóa tìm kiếm")
    limit: int = Field(default=10, ge=1, le=50, description="Số lượng cơ hội tối đa cần lấy")


class GetOpportunitiesTool(BaseTool):
    name = "get_opportunities"
    description = "Tra cứu các cơ hội kinh doanh (Leads/Pipeline) trên CRM Odoo bao gồm doanh thu dự kiến và tiến độ"
    args_schema = GetOpportunitiesInput
    required_roles = ["sales_user", "sales_manager", "admin"]
    is_write_action = False

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        validated = self.validate_args(**kwargs)
        connector = get_odoo_connector()

        domain = []
        if validated.query:
            domain.append(["name", "ilike", validated.query])

        fields = ["id", "name", "expected_revenue", "probability", "stage_id", "partner_id", "date_deadline"]

        try:
            records = await connector.search_read(
                model="crm.lead",
                domain=domain,
                fields=fields,
                limit=validated.limit,
            )
            return ToolResult(
                success=True,
                data=records,
                metadata={"source": "odoo_cloud", "count": len(records)},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Lỗi tra cứu CRM Odoo: {str(e)}",
                metadata={"error_type": "ODOO_API_ERROR"},
            )


# ============================================================================
# 3. KHÁCH HÀNG & ĐỐI TÁC (CONTACTS / PARTNERS)
# ============================================================================
class GetPartnersInput(BaseModel):
    query: str | None = Field(default=None, description="Tên đối tác hoặc từ khóa tìm kiếm")
    limit: int = Field(default=10, ge=1, le=50, description="Số lượng khách hàng tối đa")


class GetPartnersTool(BaseTool):
    name = "get_partners"
    description = "Tra cứu danh sách khách hàng và đối tác trên Odoo Contacts"
    args_schema = GetPartnersInput
    required_roles = ["sales_user", "sales_manager", "admin"]
    is_write_action = False

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        validated = self.validate_args(**kwargs)
        connector = get_odoo_connector()

        domain = []
        if validated.query:
            domain.append(["name", "ilike", validated.query])

        fields = ["id", "name", "email", "phone", "city", "customer_rank"]

        try:
            records = await connector.search_read(
                model="res.partner",
                domain=domain,
                fields=fields,
                limit=validated.limit,
            )
            return ToolResult(
                success=True,
                data=records,
                metadata={"source": "odoo_cloud", "count": len(records)},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Lỗi tra cứu khách hàng Odoo: {str(e)}",
                metadata={"error_type": "ODOO_API_ERROR"},
            )


# ============================================================================
# 4. NHÂN SỰ & QUÂN SỐ CÔNG TY (HR EMPLOYEES)
# ============================================================================
class GetEmployeesInput(BaseModel):
    query: str | None = Field(default=None, description="Tên nhân viên hoặc chức danh")
    limit: int = Field(default=20, ge=1, le=100, description="Số lượng nhân viên tối đa")


class GetEmployeesTool(BaseTool):
    name = "get_employees"
    description = "Tra cứu thông tin nhân sự, số lượng nhân viên, chức vụ và phòng ban trên Odoo HR"
    args_schema = GetEmployeesInput
    required_roles = ["admin", "hr_user", "hr_manager", "sales_manager"]
    is_write_action = False

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        validated = self.validate_args(**kwargs)
        connector = get_odoo_connector()

        domain = []
        if validated.query:
            domain.append(["name", "ilike", validated.query])

        fields = ["id", "name", "job_title", "department_id", "work_email", "work_phone"]

        try:
            records = await connector.search_read(
                model="hr.employee",
                domain=domain,
                fields=fields,
                limit=validated.limit,
            )
            return ToolResult(
                success=True,
                data=records,
                metadata={"source": "odoo_cloud", "count": len(records)},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Lỗi tra cứu nhân sự Odoo: {str(e)}",
                metadata={"error_type": "ODOO_API_ERROR"},
            )
