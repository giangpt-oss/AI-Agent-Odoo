from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class OdooCrmSkill(BaseSkill):
    name = "get_crm_pipeline"
    description = "Tra cứu các cơ hội kinh doanh (Pipeline / Leads / Deals) trên CRM Odoo."
    category = SkillCategory.ODOO
    capabilities = ["odoo", "crm", "pipeline"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Từ khóa tìm kiếm tên cơ hội hoặc khách hàng nếu có", "default": ""},
            "limit": {"type": "integer", "description": "Số lượng cơ hội tối đa (mặc định 20)", "default": 20},
            "min_revenue": {"type": "number", "description": "Lọc các cơ hội có doanh thu dự kiến từ mức này trở lên", "default": 0},
            "export_to_excel": {"type": "boolean", "description": "Đặt thành True nếu người dùng yêu cầu xuất file Excel", "default": False}
        }
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        query = kwargs.get("query", "")
        limit = kwargs.get("limit", 20)
        min_revenue = kwargs.get("min_revenue", 0)
        export_to_excel = kwargs.get("export_to_excel", False)
        
        roles = context.session.roles
        is_admin = bool({"admin", "ceo"}.intersection(set(roles)))
        is_sales = bool({"sales_user", "sales_manager", "sales_write"}.intersection(set(roles))) or is_admin
        
        if not is_sales:
            return {
                "status": "error",
                "code": 403,
                "message": "Từ chối truy cập: Bạn không thuộc phòng Kinh doanh hoặc Ban Giám Đốc."
            }
        
        odoo_client = context.providers.get("odoo")
        if not odoo_client:
            return {"status": "error", "message": "Odoo client not configured."}

        domain = []
        if query:
            domain.append(["name", "ilike", query])
        if min_revenue > 0:
            domain.append(["expected_revenue", ">=", min_revenue])

        records = await odoo_client.execute_kw(
            "crm.lead",
            "search_read",
            [domain],
            {
                "fields": ["name", "expected_revenue", "probability", "stage_id", "partner_id"],
                "limit": limit,
                "order": "expected_revenue desc"
            }
        )
        clean_deals = []
        total_rev = 0
        for r in records:
            rev = r.get("expected_revenue") or 0
            total_rev += rev
            stage = r.get("stage_id")
            stage_name = stage[1] if isinstance(stage, (list, tuple)) and len(stage) > 1 else str(stage or "Mới")
            clean_deals.append({
                "name": r.get("name"),
                "expected_revenue": rev,
                "probability": r.get("probability") or 0,
                "stage": stage_name,
            })

        excel_info = None
        if export_to_excel and clean_deals:
            from app.services.excel_exporter import excel_exporter
            from pathlib import Path
            headers = ["STT", "Tên cơ hội kinh doanh", "Doanh thu dự kiến (VNĐ)", "Tỉ lệ chốt (%)", "Tiến độ"]
            rows = [
                [idx, d["name"], d["expected_revenue"], f"{d['probability']}%", d["stage"]]
                for idx, d in enumerate(clean_deals, 1)
            ]
            fpath = excel_exporter.create_excel_report(
                title="Báo Cáo Cơ Hội Kinh Doanh Odoo CRM",
                headers=headers,
                rows=rows
            )
            context.previous_outputs.setdefault("generated_excel_files", []).append(fpath)
            excel_info = f"Đã tự động tạo file Excel '{Path(fpath).name}' đính kèm gửi cho người dùng."

        return {
            "total_pipeline_revenue": total_rev,
            "count": len(clean_deals),
            "deals": clean_deals,
            "excel_export": excel_info,
        }
