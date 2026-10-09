from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class OdooProductSkill(BaseSkill):
    name = "get_products_and_inventory"
    description = "Tra cứu tổng số lượng, danh mục sản phẩm, giá bán, mã hàng (SKU), đơn vị tính từ Odoo Products (product.template)."
    category = SkillCategory.ODOO
    capabilities = ["odoo", "products", "inventory", "sản phẩm", "hàng hóa", "giá bán", "mặt hàng", "tồn kho"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Tên sản phẩm, từ khóa hoặc mã SKU cần tìm kiếm", "default": ""},
            "limit": {"type": "integer", "description": "Số lượng sản phẩm tối đa cần lấy (mặc định 20)", "default": 20},
            "export_to_excel": {"type": "boolean", "description": "Đặt thành True nếu người dùng yêu cầu xuất file Excel", "default": False}
        }
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        query = kwargs.get("query", "")
        limit = kwargs.get("limit", 20)
        export_to_excel = kwargs.get("export_to_excel", False)

        odoo_client = context.providers.get("odoo")
        if not odoo_client:
            return {"status": "error", "message": "Odoo client not configured."}

        domain = []
        if query:
            domain = ["|", ["name", "ilike", query], ["default_code", "ilike", query]]

        total_count = await odoo_client.execute_kw(
            "product.template",
            "search_count",
            [domain],
            {}
        )
        records = await odoo_client.execute_kw(
            "product.template",
            "search_read",
            [domain],
            {"fields": ["name", "default_code", "list_price", "type", "categ_id"], "limit": limit, "order": "id desc"}
        )

        products_list = []
        for p in records:
            categ = p.get("categ_id")
            categ_name = categ[1] if isinstance(categ, (list, tuple)) and len(categ) > 1 else "Chung"
            products_list.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "default_code": p.get("default_code") or "Không có",
                "list_price": p.get("list_price", 0.0),
                "category": categ_name
            })

        excel_info = None
        if export_to_excel and products_list:
            from app.services.excel_exporter import excel_exporter
            from pathlib import Path
            headers = ["STT", "Tên sản phẩm", "Mã SKU", "Đơn giá niêm yết (VNĐ)", "Nhóm sản phẩm"]
            rows = [
                [idx, p["name"], p["default_code"], f"{p['list_price']:,.0f}", p["category"]]
                for idx, p in enumerate(products_list, 1)
            ]
            fpath = excel_exporter.create_excel_report(
                title="Báo Cáo Danh Mục Sản Phẩm Odoo",
                headers=headers,
                rows=rows
            )
            context.previous_outputs.setdefault("generated_excel_files", []).append(fpath)
            excel_info = f"Đã tự động tạo file Excel '{Path(fpath).name}' đính kèm gửi cho người dùng."

        return {
            "total_count": total_count,
            "count_returned": len(records),
            "products": products_list,
            "excel_export": excel_info,
        }
