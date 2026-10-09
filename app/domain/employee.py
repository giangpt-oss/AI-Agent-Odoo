from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SeniorityLevel(str, Enum):
    STAFF = "STAFF"
    SPECIALIST = "SPECIALIST"
    TEAM_LEAD = "TEAM_LEAD"
    HEAD = "HEAD"
    EXECUTIVE = "EXECUTIVE"


class DepartmentType(str, Enum):
    SALES = "SALES"
    HR = "HR"
    ACCOUNTING = "ACCOUNTING"
    OPERATIONS = "OPERATIONS"
    MANAGEMENT = "MANAGEMENT"
    OTHER = "OTHER"


class EmployeeProfile(BaseModel):
    user_id: str
    full_name: str
    job_title: str
    department: str
    department_type: DepartmentType = DepartmentType.OTHER
    seniority: SeniorityLevel = SeniorityLevel.STAFF
    manager_name: Optional[str] = None
    company_email: str
    odoo_user_id: Optional[int] = None
    roles: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    is_active: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_safe_identity_dict(self) -> Dict[str, Any]:
        """Trả về thông tin cơ bản, an toàn, không chứa dữ liệu nhạy cảm cho câu hỏi 'Who am I?'"""
        return {
            "name": self.full_name,
            "job_position": self.job_title,
            "department": self.department,
            "seniority": self.seniority.value,
            "manager": self.manager_name or "Không xác định",
            "company_email": self.company_email,
        }

    def format_who_am_i_response(self) -> str:
        """Định dạng câu trả lời thân thiện cho câu hỏi 'Tôi là ai?'"""
        manager_line = f"• **Người quản lý trực tiếp:** {self.manager_name}\n" if self.manager_name else ""
        return (
            f"👤 **THÔNG TIN ĐỊNH DANH NHÂN VIÊN**\n"
            f"────────────────────────────\n"
            f"• **Họ và tên:** {self.full_name}\n"
            f"• **Vị trí / Chức danh:** {self.job_title}\n"
            f"• **Phòng ban:** {self.department}\n"
            f"• **Cấp bậc:** {self.seniority.value}\n"
            f"{manager_line}"
            f"• **Email công ty:** `{self.company_email}`\n"
            f"────────────────────────────\n"
            f"💡 *Hệ thống đã nhận diện bạn và sẵn sàng hỗ trợ các tác vụ phù hợp với vị trí của bạn.*"
        )
