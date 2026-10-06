from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class OdooPartnerSkill(BaseSkill):
    name = "get_partners_and_customers"
    description = "Tra cứu danh bạ khách hàng, nhà cung cấp và đối tác từ Odoo Contacts."
    category = SkillCategory.ODOO
    capabilities = ["odoo", "contacts", "partners"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Tên khách hàng, đối tác, số điện thoại hoặc email cần tìm kiếm", "default": ""},
            "limit": {"type": "integer", "description": "Số lượng đối tác tối đa cần lấy (mặc định 20)", "default": 20},
            "export_to_excel": {"type": "boolean", "description": "Đặt thành True nếu người dùng yêu cầu xuất file Excel", "default": False}
        }
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        query = kwargs.get("query", "")
        limit = kwargs.get("limit", 20)
        export_to_excel = kwargs.get("export_to_excel", False)
        
        roles = context.session.roles
        is_admin = bool({"admin", "ceo"}.intersection(set(roles)))
        is_sales = bool({"sales_user", "sales_manager", "sales_write"}.intersection(set(roles))) or is_admin
        
        if not is_sales:
            return {
                "status": "error",
                "code": 403,
                "message": "Từ chối truy cập: Bạn không có quyền xem thông tin khách hàng/đối tác."
            }

        odoo_client = context.providers.get("odoo")
        if not odoo_client:
            return {"status": "error", "message": "Odoo client not configured."}

        domain = []
        if query:
            domain = ["|", "|", ["name", "ilike", query], ["email", "ilike", query], ["phone", "ilike", query]]
        records = await odoo_client.execute_kw(
            "res.partner",
            "search_read",
            [domain],
            {"fields": ["name", "email", "phone", "city"], "limit": limit, "order": "id desc"}
        )
        partners_list = [
            {
                "name": p.get("name"),
                "email": p.get("email") or "Chưa có",
                "phone": p.get("phone") or "Chưa có",
                "city": p.get("city") or "N/A"
            } for p in records
        ]

        excel_info = None
        if export_to_excel and partners_list:
            from app.services.excel_exporter import excel_exporter
            from pathlib import Path
            headers = ["STT", "Tên khách hàng / Đối tác", "Email", "Số điện thoại", "Tỉnh / Thành phố"]
            rows = [
                [idx, p["name"], p["email"], p["phone"], p["city"]]
                for idx, p in enumerate(partners_list, 1)
            ]
            fpath = excel_exporter.create_excel_report(
                title="Danh Bạ Khách Hàng Và Đối Tác Odoo",
                headers=headers,
                rows=rows
            )
            context.previous_outputs.setdefault("generated_excel_files", []).append(fpath)
            excel_info = f"Đã tự động tạo file Excel '{Path(fpath).name}' đính kèm gửi cho người dùng."

        return {
            "count": len(records),
            "partners": partners_list,
            "excel_export": excel_info,
        }
