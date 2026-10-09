from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class OdooOrderSkill(BaseSkill):
    name = "get_sale_orders"
    description = "Tra cứu tổng số lượng, danh sách đơn hàng bán (Sales Orders), báo giá, trạng thái đơn hàng (báo giá/đã xác nhận) từ Odoo Sales (sale.order)."
    category = SkillCategory.ODOO
    capabilities = ["odoo", "sale_order", "đơn hàng", "bán hàng", "sales_order", "orders", "báo giá"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Tên khách hàng hoặc mã đơn hàng (VD: S00058)", "default": ""},
            "state": {"type": "string", "description": "Lọc trạng thái: 'draft' (Báo giá), 'sale' (Đơn đã xác nhận), 'cancel' (Đã hủy), để trống để lấy tất cả", "default": ""},
            "limit": {"type": "integer", "description": "Số lượng đơn hàng tối đa cần lấy (mặc định 20)", "default": 20},
            "export_to_excel": {"type": "boolean", "description": "Đặt thành True nếu người dùng yêu cầu xuất file Excel", "default": False}
        }
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        query = kwargs.get("query", "")
        state = kwargs.get("state", "")
        limit = kwargs.get("limit", 20)
        export_to_excel = kwargs.get("export_to_excel", False)

        odoo_client = context.providers.get("odoo")
        if not odoo_client:
            return {"status": "error", "message": "Odoo client not configured."}

        domain = []
        if state:
            domain.append(["state", "=", state])
        if query:
            domain.extend(["|", ["name", "ilike", query], ["partner_id.name", "ilike", query]])

        total_count = await odoo_client.execute_kw(
            "sale.order",
            "search_count",
            [domain],
            {}
        )
        records = await odoo_client.execute_kw(
            "sale.order",
            "search_read",
            [domain],
            {"fields": ["name", "partner_id", "date_order", "amount_total", "state", "user_id"], "limit": limit, "order": "id desc"}
        )

        state_mapping = {
            "draft": "Dự thảo / Báo giá",
            "sent": "Đã gửi báo giá",
            "sale": "Đơn hàng bán (Đã xác nhận)",
            "done": "Đã khóa / Hoàn tất",
            "cancel": "Đã hủy"
        }

        orders_list = []
        for o in records:
            partner = o.get("partner_id")
            partner_name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "Không xác định"
            salesperson = o.get("user_id")
            salesperson_name = salesperson[1] if isinstance(salesperson, (list, tuple)) and len(salesperson) > 1 else "Chưa phân công"
            st = o.get("state", "")
            orders_list.append({
                "order_number": o.get("name"),
                "customer": partner_name,
                "amount_total": o.get("amount_total", 0.0),
                "state": state_mapping.get(st, st),
                "date_order": str(o.get("date_order", ""))[:10],
                "salesperson": salesperson_name
            })

        excel_info = None
        if export_to_excel and orders_list:
            from app.services.excel_exporter import excel_exporter
            from pathlib import Path
            headers = ["STT", "Số đơn hàng", "Khách hàng", "Tổng tiền (VNĐ)", "Trạng thái", "Ngày tạo", "Nhân viên phụ trách"]
            rows = [
                [idx, ord_item["order_number"], ord_item["customer"], f"{ord_item['amount_total']:,.0f}", ord_item["state"], ord_item["date_order"], ord_item["salesperson"]]
                for idx, ord_item in enumerate(orders_list, 1)
            ]
            fpath = excel_exporter.create_excel_report(
                title="Báo Cáo Đơn Hàng Bán Odoo",
                headers=headers,
                rows=rows
            )
            context.previous_outputs.setdefault("generated_excel_files", []).append(fpath)
            excel_info = f"Đã tự động tạo file Excel '{Path(fpath).name}' đính kèm gửi cho người dùng."

        return {
            "total_count": total_count,
            "count_returned": len(records),
            "orders": orders_list,
            "excel_export": excel_info,
        }
