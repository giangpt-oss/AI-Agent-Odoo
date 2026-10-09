from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class OdooPartnerSkill(BaseSkill):
    name = "get_partners_and_customers"
    description = "Tra cứu thông tin chi tiết, tổng số lượng và danh bạ khách hàng, bệnh viện, công ty, đối tác, nhà cung cấp, thông tin liên hệ từ Odoo Contacts (res.partner)."
    category = SkillCategory.ODOO
    capabilities = [
        "odoo", "contacts", "partners", "customers", "liên hệ", "khách hàng",
        "đối tác", "công ty", "bệnh viện", "doanh nghiệp", "nhà cung cấp", "thông tin công ty"
    ]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Tên khách hàng, tên công ty, bệnh viện, đối tác, số điện thoại, mã số thuế hoặc email cần tìm", "default": ""},
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
            domain = ["|", "|", "|", ["name", "ilike", query], ["email", "ilike", query], ["phone", "ilike", query], ["vat", "ilike", query]]

        total_count = await odoo_client.execute_kw(
            "res.partner",
            "search_count",
            [domain],
            {}
        )
        records = await odoo_client.execute_kw(
            "res.partner",
            "search_read",
            [domain],
            {"fields": ["name", "email", "phone", "street", "city", "vat", "company_type"], "limit": limit, "order": "id desc"}
        )
        partners_list = [
            {
                "name": p.get("name"),
                "email": p.get("email") or "Chưa có",
                "phone": p.get("phone") or "Chưa có",
                "address": f"{p.get('street') or ''}, {p.get('city') or ''}".strip(", "),
                "vat": p.get("vat") or "Chưa cập nhật",
                "type": "Doanh nghiệp / Tổ chức" if p.get("company_type") == "company" else "Cá nhân"
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
            "total_count": total_count,
            "count_returned": len(records),
            "partners": partners_list,
            "excel_export": excel_info,
        }
