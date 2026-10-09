from typing import Any, List, Dict
import logging
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

logger = logging.getLogger(__name__)

class OdooUniversalQuerySkill(BaseSkill):
    name = "query_odoo_records"
    description = (
        "Truy vấn vạn năng (Universal Query) dữ liệu từ BẤT KỲ model nào trên Odoo Cloud ERP "
        "(ví dụ: purchase.order - đơn mua hàng, stock.picking - phiếu kho/vận chuyển, "
        "account.move - hóa đơn, product.template - sản phẩm, res.partner - đối tác, "
        "crm.lead - cơ hội, hr.employee - nhân sự, hoặc bất kỳ bảng nào khác trong Odoo). "
        "Dùng khi người dùng hỏi về số lượng, trạng thái, danh sách bản ghi của bất kỳ phân hệ nào trên Odoo."
    )
    category = SkillCategory.ODOO
    capabilities = [
        "odoo", "query", "universal_query", "tra cứu odoo", "dữ liệu odoo",
        "đơn mua", "phiếu kho", "nhập xuất kho", "hóa đơn", "tài chính", "bất kỳ thông tin odoo"
    ]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "Tên Model kỹ thuật trên Odoo (ví dụ: 'purchase.order', 'stock.picking', 'account.move', 'product.template', 'res.partner', 'crm.lead', 'hr.employee', v.v.)"
            },
            "domain": {
                "type": "string",
                "description": "Bộ lọc Odoo domain dạng chuỗi JSON (ví dụ: '[[\"state\", \"=\", \"purchase\"]]' hoặc '[]' để lấy tất cả)",
                "default": "[]"
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách tên các trường cần lấy (ví dụ: ['name', 'date_order', 'amount_total', 'state']). Bỏ trống để hệ thống tự chọn các trường cơ bản.",
                "default": []
            },
            "limit": {
                "type": "integer",
                "description": "Số lượng bản ghi tối đa (mặc định 20)",
                "default": 20
            },
            "count_only": {
                "type": "boolean",
                "description": "Đặt thành True nếu người dùng chỉ hỏi tổng số lượng (ví dụ: 'có bao nhiêu đơn mua')",
                "default": False
            }
        },
        "required": ["model"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        model = kwargs.get("model", "").strip()
        raw_domain = kwargs.get("domain", "[]")
        domain = []
        if isinstance(raw_domain, str) and raw_domain.strip():
            try:
                import json
                domain = json.loads(raw_domain)
            except Exception:
                domain = []
        elif isinstance(raw_domain, list):
            domain = raw_domain

        fields = kwargs.get("fields", [])
        limit = kwargs.get("limit", 20)
        count_only = kwargs.get("count_only", False)

        odoo_client = context.providers.get("odoo")
        if not odoo_client:
            return {"status": "error", "message": "Odoo client not configured."}

        try:
            # 1. Kiểm tra tổng số lượng bản ghi
            total_count = await odoo_client.execute_kw(
                model,
                "search_count",
                [domain],
                {}
            )
            
            if count_only:
                return {
                    "model": model,
                    "total_count": total_count,
                    "message": f"Tổng số bản ghi trong model '{model}' là {total_count}."
                }

            # 2. Nếu không chỉ định fields, lấy fields_get để chọn các trường an toàn
            query_fields = list(fields)
            if not query_fields:
                try:
                    all_fields = await odoo_client.execute_kw(model, "fields_get", [], {"attributes": ["type", "string"]})
                    preferred = ["name", "display_name", "date_order", "date", "partner_id", "user_id", "amount_total", "state", "origin", "location_id", "location_dest_id", "scheduled_date"]
                    query_fields = [f for f in preferred if f in all_fields]
                    if not query_fields and "name" in all_fields:
                        query_fields = ["name"]
                    elif not query_fields:
                        query_fields = ["display_name"] if "display_name" in all_fields else list(all_fields.keys())[:5]
                except Exception:
                    query_fields = ["name", "display_name"]

            # 3. Đọc dữ liệu bản ghi
            records = await odoo_client.execute_kw(
                model,
                "search_read",
                [domain],
                {"fields": query_fields, "limit": limit, "order": "id desc"}
            )

            # 4. Chuẩn hóa bản ghi (chuyển Many2one [id, name] thành chuỗi dễ đọc)
            clean_records = []
            for r in records:
                item = {}
                for k, v in r.items():
                    if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], str):
                        item[k] = v[1]
                    else:
                        item[k] = v
                clean_records.append(item)

            return {
                "model": model,
                "total_count": total_count,
                "returned_count": len(clean_records),
                "records": clean_records
            }
        except Exception as e:
            err_str = str(e)
            logger.warning(f"Universal Odoo query on '{model}' failed: {err_str}")
            if "doesn't exist" in err_str or "Object" in err_str:
                return {
                    "status": "error",
                    "error_type": "MODULE_NOT_INSTALLED",
                    "message": f"Phân hệ hoặc bảng '{model}' chưa được cài đặt trên hệ thống Odoo của công ty."
                }
            if "AccessDenied" in err_str or "AccessError" in err_str:
                return {
                    "status": "error",
                    "error_type": "PERMISSION_DENIED",
                    "message": f"Tài khoản Odoo của bạn không có quyền xem dữ liệu bảng '{model}'."
                }
            return {
                "status": "error",
                "message": f"Không thể truy vấn bảng '{model}': {err_str}"
            }
