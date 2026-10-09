from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class OdooHrSkill(BaseSkill):
    name = "get_company_employees"
    description = "Tra cứu quy mô nhân sự, số lượng và danh sách nhân viên từ Odoo HR."
    category = SkillCategory.ODOO
    capabilities = ["odoo", "hr", "employees"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "department": {"type": "string", "description": "Tên phòng ban cần lọc nếu có (ví dụ: 'Kinh doanh', 'Hà Nội', 'Dự án')"},
            "limit": {"type": "integer", "description": "Số lượng nhân sự tối đa cần hiển thị (mặc định 30)", "default": 30},
            "export_to_excel": {"type": "boolean", "description": "Đặt thành True nếu người dùng yêu cầu xuất file Excel", "default": False}
        }
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        department = kwargs.get("department", "")
        limit = kwargs.get("limit", 30)
        export_to_excel = kwargs.get("export_to_excel", False)
        
        roles = context.session.roles
        is_authorized = bool({"admin", "ceo", "hr_user", "hr_manager"}.intersection(set(roles))) or "READ_ODOO_HR" in context.session.permissions
        if not is_authorized:
            return {
                "status": "error",
                "code": 403,
                "message": "Từ chối truy cập: Tài khoản của bạn không thuộc Ban Giám Đốc hoặc Quản trị nhân sự."
            }
        
        odoo_client = context.providers.get("odoo")
        if not odoo_client:
            return {"status": "error", "message": "Odoo client not configured in context providers."}

        domain = []
        if department:
            domain.append(["department_id.name", "ilike", department])

        total_employees_count = await odoo_client.execute_kw(
            "hr.employee",
            "search_count",
            [domain],
            {}
        )
        emps = await odoo_client.execute_kw(
            "hr.employee",
            "search_read",
            [domain],
            {"fields": ["name", "job_title", "department_id", "work_email", "work_phone"], "limit": limit}
        )
        users_count = await odoo_client.execute_kw(
            "res.users",
            "search_count",
            [[["active", "=", True]]],
            {}
        )
        clean_emps = []
        for e in emps:
            dept = e.get("department_id")
            dept_name = dept[1] if isinstance(dept, (list, tuple)) and len(dept) > 1 else "Chưa phân bổ"
            clean_emps.append({
                "name": e.get("name"),
                "job_title": e.get("job_title") or "Nhân viên",
                "department": dept_name,
                "email": e.get("work_email") or "Chưa có",
            })

        excel_info = None
        if export_to_excel and clean_emps:
            from app.services.excel_exporter import excel_exporter
            from pathlib import Path
            headers = ["STT", "Họ và tên", "Chức vụ", "Phòng ban", "Email"]
            rows = [
                [idx, e["name"], e["job_title"], e["department"], e["email"]]
                for idx, e in enumerate(clean_emps, 1)
            ]
            fpath = excel_exporter.create_excel_report(
                title=f"Danh Sách Nhân Sự {f'Phòng {department}' if department else 'Công Ty'}",
                headers=headers,
                rows=rows
            )
            context.previous_outputs.setdefault("generated_excel_files", []).append(fpath)
            excel_info = f"Đã tự động tạo file Excel '{Path(fpath).name}' đính kèm gửi cho người dùng."

        return {
            "total_official_employees": total_employees_count,
            "total_system_users": users_count,
            "count_returned": len(emps),
            "employees": clean_emps,
            "excel_export": excel_info,
        }
