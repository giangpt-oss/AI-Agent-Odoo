import logging
from typing import Any, Dict, Optional
from app.domain.employee import EmployeeProfile, SeniorityLevel, DepartmentType
from app.services.permissions import permission_service

logger = logging.getLogger(__name__)


class EmployeeProfileService:
    """Dịch vụ trích xuất và chuẩn hóa hồ sơ định danh nhân viên từ Odoo ERP."""

    @staticmethod
    def parse_seniority(job_title: str, roles: list[str]) -> SeniorityLevel:
        title = (job_title or "").lower()
        roles_set = set(r.lower() for r in (roles or []))

        if any(r in roles_set for r in ["admin", "ceo", "director"]):
            return SeniorityLevel.EXECUTIVE
        if any(k in title for k in ["giám đốc", "cfo", "ceo", "director", "executive", "board"]):
            return SeniorityLevel.EXECUTIVE
        if any(k in title for k in ["trưởng phòng", "head of", "manager", "kế toán trưởng", "chief"]):
            return SeniorityLevel.HEAD
        if any(k in title for k in ["trưởng nhóm", "team lead", "lead", "trưởng ca"]):
            return SeniorityLevel.TEAM_LEAD
        if any(k in title for k in ["chuyên viên", "senior", "specialist"]):
            return SeniorityLevel.SPECIALIST
        return SeniorityLevel.STAFF

    @staticmethod
    def parse_department_type(department: str, job_title: str) -> DepartmentType:
        text = f"{department or ''} {job_title or ''}".lower()
        if any(k in text for k in ["kinh doanh", "sales", "bán hàng", "crm"]):
            return DepartmentType.SALES
        if any(k in text for k in ["nhân sự", "hr", "tuyển dụng", "hành chính nhân sự"]):
            return DepartmentType.HR
        if any(k in text for k in ["kế toán", "tài chính", "accounting", "finance"]):
            return DepartmentType.ACCOUNTING
        if any(k in text for k in ["vận hành", "operations", "kho", "logistics"]):
            return DepartmentType.OPERATIONS
        if any(k in text for k in ["ban giám đốc", "management", "điều hành"]):
            return DepartmentType.MANAGEMENT
        return DepartmentType.OTHER

    async def get_profile_by_chat_id(self, chat_id: int) -> Optional[EmployeeProfile]:
        """Lấy EmployeeProfile từ session Telegram đã liên kết Odoo."""
        from app.services.identity_store import identity_store
        linked = identity_store.get(chat_id)
        if not linked:
            return None
        raw = linked.get("profile", {})
        return self.build_profile_from_dict(raw)

    def build_profile_from_dict(self, raw: Dict[str, Any]) -> EmployeeProfile:
        job_title = raw.get("job_title") or "Nhân viên"
        department = raw.get("department") or "Chưa phân bổ"
        roles = raw.get("roles") or ["employee"]
        seniority = self.parse_seniority(job_title, roles)
        dept_type = self.parse_department_type(department, job_title)
        permissions = permission_service.get_permissions_for_roles(roles)

        return EmployeeProfile(
            user_id=str(raw.get("id") or raw.get("odoo_user_id") or "guest"),
            full_name=raw.get("full_name") or "Người dùng",
            job_title=job_title,
            department=department,
            department_type=dept_type,
            seniority=seniority,
            manager_name=raw.get("manager_name"),
            company_email=raw.get("email") or "Chưa liên kết",
            odoo_user_id=raw.get("odoo_user_id"),
            roles=roles,
            permissions=permissions,
            is_active=raw.get("is_active", True),
            metadata=raw
        )


employee_profile_service = EmployeeProfileService()
