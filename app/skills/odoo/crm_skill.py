import logging
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


logger = logging.getLogger(__name__)


class OdooCreateCrmLeadSkill(BaseSkill):
    name = "create_crm_lead"
    description = "Tạo mới cơ hội kinh doanh (Opportunity) trên Odoo CRM cho khách hàng."
    category = SkillCategory.ODOO
    capabilities = ["odoo", "crm", "create_lead", "create_opportunity", "tạo cơ hội"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tên cơ hội kinh doanh (bắt buộc)"},
            "partner_id": {"type": "integer", "description": "ID khách hàng trên Odoo (bắt buộc)"},
            "expected_revenue": {"type": "number", "description": "Doanh thu dự kiến (bắt buộc, VNĐ)"},
            "description": {"type": "string", "description": "Mô tả / ghi chú chi tiết về cơ hội (tùy chọn)", "default": ""}
        },
        "required": ["name", "partner_id", "expected_revenue"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        import httpx
        from app.services.audit import audit_service
        from app.connectors.odoo.client import OdooAccessDeniedException, OdooAPIException

        name = kwargs.get("name", "").strip()
        partner_id = kwargs.get("partner_id")
        expected_revenue = kwargs.get("expected_revenue", 0.0)
        description = kwargs.get("description", "").strip()

        if not name:
            raise ValueError("Tên cơ hội không được để trống.")
        try:
            partner_id = int(partner_id)
            if partner_id <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("ID khách hàng không hợp lệ (phải là số nguyên dương).")

        try:
            expected_revenue = float(expected_revenue)
            if expected_revenue < 0:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("Doanh thu dự kiến không hợp lệ.")

        odoo_client = context.providers.get("odoo")
        if not odoo_client:
            raise RuntimeError("Odoo client chưa được cấu hình trong phiên làm việc.")

        # Đảm bảo đã xác thực tài khoản Odoo
        await odoo_client.authenticate()
        actor_uid = odoo_client.uid

        # 1. Kiểm tra tồn tại của khách hàng trên Odoo & lấy tên khách
        partner_name = f"Khách hàng #{partner_id}"
        try:
            partners = await odoo_client.execute_kw(
                "res.partner",
                "search_read",
                [[["id", "=", partner_id]]],
                {"fields": ["id", "name"], "limit": 1}
            )
            if partners:
                partner_name = partners[0].get("name") or partner_name
            else:
                raise ValueError(f"Không tìm thấy khách hàng có ID #{partner_id} trên Odoo.")
        except (OdooAccessDeniedException, OdooAPIException) as e:
            logger.warning(f"Không thể tra cứu khách hàng #{partner_id}: {e}")
            raise RuntimeError(f"Odoo từ chối quyền truy cập khách hàng: {e}")

        # 2. Chuẩn bị giá trị ghi vào crm.lead
        vals = {
            "name": name,
            "partner_id": partner_id,
            "expected_revenue": expected_revenue,
            "type": "opportunity",
        }
        if actor_uid:
            vals["user_id"] = int(actor_uid)
        if description:
            vals["description"] = description

        # 3. Tạo bản ghi crm.lead
        lead_id = None
        try:
            lead_id_raw = await odoo_client.execute_kw("crm.lead", "create", [vals])
            lead_id = int(lead_id_raw)
        except (httpx.TimeoutException, TimeoutError) as e:
            logger.error(f"Timeout khi gửi lệnh tạo crm.lead lên Odoo: {e}")
            raise RuntimeError(
                "TIMEOUT_UNDETERMINED: Đã gửi lệnh tạo cơ hội tới Odoo nhưng bị quá thời gian chờ (timeout). "
                "Hệ thống không tự động gửi lại lệnh để tránh trùng lặp dữ liệu. "
                "Vui lòng kiểm tra lại danh sách cơ hội trên Odoo trước khi thao tác tiếp."
            )
        except OdooAccessDeniedException as e:
            logger.warning(f"Odoo từ chối quyền tạo crm.lead: {e}")
            raise RuntimeError("PERMISSION_DENIED: Tài khoản Odoo của bạn không có quyền tạo cơ hội kinh doanh trên Odoo ERP.")
        except Exception as e:
            logger.error(f"Lỗi tạo crm.lead trên Odoo: {e}")
            raise RuntimeError(f"Odoo báo lỗi khi tạo cơ hội: {e}")

        # 4. Đọc lại bản ghi để lấy dữ liệu thực tế
        read_back = None
        read_back_error = None
        try:
            records = await odoo_client.execute_kw(
                "crm.lead",
                "read",
                [[lead_id]],
                {"fields": ["name", "partner_id", "expected_revenue", "user_id", "stage_id"]}
            )
            if records:
                read_back = records[0]
        except Exception as e:
            logger.warning(f"Đã tạo cơ hội #{lead_id} thành công nhưng đọc lại thất bại: {e}")
            read_back_error = f"Chưa thể đối chiếu chi tiết sau khi tạo: {e}"

        base_url = odoo_client.base_url.rstrip("/")
        odoo_url = f"{base_url}/web#id={lead_id}&model=crm.lead&view_type=form"

        # 5. Ghi vết kiểm toán (Audit Trail) - Tuyệt đối không log thông tin xác thực
        session = context.session
        user_id_str = session.user_id if session else "unknown"
        audit_service.log_external_action(
            action="CREATE",
            skill="create_crm_lead",
            provider="odoo",
            resource_id=f"crm.lead:{lead_id}",
            user_id=user_id_str,
            status="SUCCESS"
        )

        return {
            "status": "success",
            "lead_id": lead_id,
            "name": name,
            "partner_id": partner_id,
            "partner_name": partner_name,
            "expected_revenue": expected_revenue,
            "currency": "VNĐ",
            "odoo_url": odoo_url,
            "read_back": read_back,
            "read_back_error": read_back_error,
            "user_id": actor_uid
        }

