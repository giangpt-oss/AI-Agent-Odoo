import logging
from typing import List, Set, Tuple
from app.domain.employee import EmployeeProfile, SeniorityLevel, DepartmentType
from app.domain.roles import StandardRole, RoleClassifier
from app.domain.capabilities import BusinessCapability, CAPABILITY_REGISTRY

logger = logging.getLogger(__name__)


class CapabilityService:
    """Quản lý và thẩm định quyền truy cập AI Capabilities theo Vai trò, Cấp bậc và Phân quyền Odoo thực tế."""

    # Role to Candidate Capabilities Mapping
    ROLE_CAPABILITY_MAP: dict[StandardRole, list[BusinessCapability]] = {
        StandardRole.HEAD_OF_SALES: [
            BusinessCapability.CUSTOMER_INTELLIGENCE,
            BusinessCapability.LEAD_MANAGEMENT,
            BusinessCapability.OPPORTUNITY_MANAGEMENT,
            BusinessCapability.PIPELINE_ANALYSIS,
            BusinessCapability.QUOTATION_MANAGEMENT,
            BusinessCapability.SALES_REPORTING,
            BusinessCapability.SALES_FORECASTING,
            BusinessCapability.CUSTOMER_FOLLOW_UP,
            BusinessCapability.EMAIL_ASSISTANCE,
            BusinessCapability.CALENDAR_ASSISTANCE,
            BusinessCapability.MEETING_PREPARATION,
            BusinessCapability.TASK_MANAGEMENT,
            BusinessCapability.DOCUMENT_PROCESSING,
            BusinessCapability.KNOWLEDGE_SEARCH,
        ],
        StandardRole.HEAD_OF_HR: [
            BusinessCapability.EMPLOYEE_INFORMATION,
            BusinessCapability.RECRUITMENT_ASSISTANCE,
            BusinessCapability.ONBOARDING,
            BusinessCapability.LEAVE_AND_ATTENDANCE,
            BusinessCapability.HR_DOCUMENT_DRAFTING,
            BusinessCapability.WORKFORCE_REPORTING,
            BusinessCapability.EMPLOYEE_ADMINISTRATION,
            BusinessCapability.EMAIL_ASSISTANCE,
            BusinessCapability.CALENDAR_ASSISTANCE,
            BusinessCapability.MEETING_PREPARATION,
            BusinessCapability.TASK_MANAGEMENT,
            BusinessCapability.DOCUMENT_PROCESSING,
            BusinessCapability.KNOWLEDGE_SEARCH,
        ],
        StandardRole.CHIEF_ACCOUNTANT: [
            BusinessCapability.INVOICE_MANAGEMENT,
            BusinessCapability.ACCOUNTS_RECEIVABLE,
            BusinessCapability.ACCOUNTS_PAYABLE,
            BusinessCapability.RECONCILIATION_SUPPORT,
            BusinessCapability.FINANCIAL_REPORTING,
            BusinessCapability.PAYMENT_MONITORING,
            BusinessCapability.BUDGET_ANALYSIS,
            BusinessCapability.EMAIL_ASSISTANCE,
            BusinessCapability.CALENDAR_ASSISTANCE,
            BusinessCapability.MEETING_PREPARATION,
            BusinessCapability.TASK_MANAGEMENT,
            BusinessCapability.DOCUMENT_PROCESSING,
            BusinessCapability.KNOWLEDGE_SEARCH,
        ],
        StandardRole.EXECUTIVE_DIRECTOR: [
            # Executive has cross-department capabilities subject to explicit confirmation
            *list(BusinessCapability),
        ],
        StandardRole.GENERAL_EMPLOYEE: [
            BusinessCapability.EMAIL_ASSISTANCE,
            BusinessCapability.CALENDAR_ASSISTANCE,
            BusinessCapability.MEETING_PREPARATION,
            BusinessCapability.TASK_MANAGEMENT,
            BusinessCapability.DOCUMENT_PROCESSING,
            BusinessCapability.KNOWLEDGE_SEARCH,
        ],
    }

    def get_candidate_capabilities_for_profile(self, profile: EmployeeProfile) -> List[BusinessCapability]:
        role = RoleClassifier.classify(profile)
        candidates = self.ROLE_CAPABILITY_MAP.get(role, self.ROLE_CAPABILITY_MAP[StandardRole.GENERAL_EMPLOYEE])
        return candidates

    def get_allowed_capabilities(self, profile: EmployeeProfile) -> List[BusinessCapability]:
        """Lọc các capabilities mà user thực sự được cấp quyền dựa trên permissions thực tế."""
        candidates = self.get_candidate_capabilities_for_profile(profile)
        user_perms = set(profile.permissions)
        is_admin = bool({"admin", "ceo"}.intersection(set(profile.roles)))

        allowed = []
        for cap in candidates:
            cap_def = CAPABILITY_REGISTRY.get(cap)
            if not cap_def:
                continue
            if is_admin:
                allowed.append(cap)
                continue
            # Kiểm tra xem user có đủ tất cả required permissions của capability này không
            if all(p in user_perms for p in cap_def.required_permissions):
                allowed.append(cap)
        return allowed

    def get_allowed_skills(self, profile: EmployeeProfile) -> Set[str]:
        """Tập hợp tất cả skill names tương ứng với các capabilities được phép."""
        allowed_caps = self.get_allowed_capabilities(profile)
        skills = set()
        for cap in allowed_caps:
            cap_def = CAPABILITY_REGISTRY.get(cap)
            if cap_def:
                skills.update(cap_def.associated_skills)
        return skills

    def validate_request_authorization(self, profile: EmployeeProfile, query: str) -> Tuple[bool, str]:
        """Kiểm tra ranh giới bảo mật nghiêm ngặt ngay lập tức:
        AI có thể technically làm được nhưng User có được phép không?
        Nếu không được phép -> REJECT NGAY LẬP TỨC mà không leo thang quyền.
        """
        q = query.lower()
        role = RoleClassifier.classify(profile)
        is_admin = bool({"admin", "ceo"}.intersection(set(profile.roles)))
        if is_admin:
            return True, "AUTHORIZED"

        # 1. Kiểm tra yêu cầu bảng lương / chi phí nhân sự nhạy cảm
        payroll_keywords = ["bảng lương", "payroll", "tiền lương", "salary", "lương nhân viên", "thu nhập nhân sự"]
        if any(k in q for k in payroll_keywords):
            if role != StandardRole.HEAD_OF_HR and "READ_ODOO_HR" not in profile.permissions:
                return (
                    False,
                    "⛔ **TỪ CHỐI TRUY CẬP (PERMISSION DENIED):**\n"
                    "Bạn không có quyền truy cập dữ liệu bảng lương và thu nhập nhân sự.\n"
                    "Hệ thống không hỗ trợ tự ý nâng quyền truy cập."
                )

        # 2. Kiểm tra yêu cầu sổ sách kế toán / tài chính ngân hàng
        finance_keywords = ["sổ quỹ", "tài khoản ngân hàng", "bank balance", "sổ cái", "kết quả tài chính chi tiết"]
        if any(k in q for k in finance_keywords):
            if role != StandardRole.CHIEF_ACCOUNTANT and "ACCOUNTING" not in profile.department_type.value:
                return (
                    False,
                    "⛔ **TỪ CHỐI TRUY CẬP (PERMISSION DENIED):**\n"
                    "Bạn không có quyền truy cập dữ liệu sổ quỹ và tài chính ngân hàng công ty.\n"
                    "Yêu cầu này vượt quá thẩm quyền của vị trí hiện tại."
                )

        # 3. Kiểm tra yêu cầu thông tin mật HR từ Sales
        if role == StandardRole.HEAD_OF_SALES:
            if any(k in q for k in ["hồ sơ cá nhân nhân viên", "hợp đồng lao động", "kỷ luật nhân sự"]):
                return (
                    False,
                    "⛔ **TỪ CHỐI TRUY CẬP (PERMISSION DENIED):**\n"
                    "Vị trí Trưởng phòng Kinh doanh không được phép tra cứu hồ sơ kỷ luật hoặc hợp đồng lao động nhân sự."
                )

        # 4. Kiểm tra yêu cầu pipeline khách hàng từ HR không có quyền Sales
        if role == StandardRole.HEAD_OF_HR and "READ_ODOO_CRM" not in profile.permissions:
            if any(k in q for k in ["pipeline", "cơ hội bán hàng", "báo giá chi tiết"]):
                return (
                    False,
                    "⛔ **TỪ CHỐI TRUY CẬP (PERMISSION DENIED):**\n"
                    "Vị trí Trưởng phòng Nhân sự không có quyền truy cập chi tiết phễu bán hàng (CRM Pipeline)."
                )

        return True, "AUTHORIZED"


capability_service = CapabilityService()
