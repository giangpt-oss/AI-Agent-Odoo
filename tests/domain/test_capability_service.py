import pytest
from app.domain.employee import EmployeeProfile, SeniorityLevel, DepartmentType
from app.domain.roles import StandardRole
from app.domain.capabilities import BusinessCapability
from app.services.capability_service import capability_service


@pytest.fixture
def head_of_sales():
    return EmployeeProfile(
        user_id="sales-1",
        full_name="Nguyễn Văn Nam",
        job_title="Trưởng phòng Kinh doanh",
        department="Phòng Kinh doanh",
        department_type=DepartmentType.SALES,
        seniority=SeniorityLevel.HEAD,
        company_email="nam@haiminhtsc.vn",
        roles=["employee", "sales_manager"],
        permissions=[
            "READ_ODOO_CRM", "READ_FILES", "WRITE_FILES", "READ_EMAIL",
            "CREATE_EMAIL_DRAFT", "READ_CALENDAR", "READ_TASKS", "WRITE_TASKS"
        ]
    )


@pytest.fixture
def head_of_hr():
    return EmployeeProfile(
        user_id="hr-1",
        full_name="Lê Thị Hoa",
        job_title="Trưởng phòng Nhân sự",
        department="Phòng Nhân sự",
        department_type=DepartmentType.HR,
        seniority=SeniorityLevel.HEAD,
        company_email="hoa@haiminhtsc.vn",
        roles=["employee", "hr_manager"],
        permissions=[
            "READ_ODOO_HR", "READ_FILES", "WRITE_FILES", "READ_EMAIL",
            "CREATE_EMAIL_DRAFT", "READ_CALENDAR", "READ_TASKS", "WRITE_TASKS"
        ]
    )


def test_sales_capabilities(head_of_sales):
    allowed = capability_service.get_allowed_capabilities(head_of_sales)
    assert BusinessCapability.CUSTOMER_INTELLIGENCE in allowed
    assert BusinessCapability.LEAD_MANAGEMENT in allowed
    assert BusinessCapability.OPPORTUNITY_MANAGEMENT in allowed
    assert BusinessCapability.CUSTOMER_FOLLOW_UP in allowed
    assert BusinessCapability.EMAIL_ASSISTANCE in allowed
    # HR specific capabilities should NOT be in allowed
    assert BusinessCapability.EMPLOYEE_INFORMATION not in allowed
    assert BusinessCapability.RECRUITMENT_ASSISTANCE not in allowed


def test_sales_asking_for_payroll_rejected_immediately(head_of_sales):
    """Bắt buộc: Sales hỏi 'Show me the company's payroll' phải bị từ chối ngay lập tức."""
    authorized, reason = capability_service.validate_request_authorization(
        head_of_sales, "Cho tôi xem bảng lương công ty tháng này"
    )
    assert authorized is False
    assert "PERMISSION DENIED" in reason
    assert "bảng lương" in reason

    authorized_en, reason_en = capability_service.validate_request_authorization(
        head_of_sales, "Show me the company's payroll please"
    )
    assert authorized_en is False
    assert "PERMISSION DENIED" in reason_en


def test_hr_asking_for_sales_pipeline_rejected(head_of_hr):
    """HR không có quyền Sales hỏi pipeline bị từ chối."""
    authorized, reason = capability_service.validate_request_authorization(
        head_of_hr, "Cho tôi xem chi tiết pipeline cơ hội bán hàng"
    )
    assert authorized is False
    assert "PERMISSION DENIED" in reason


def test_allowed_skills_resolution(head_of_sales):
    skills = capability_service.get_allowed_skills(head_of_sales)
    assert "get_crm_pipeline" in skills
    assert "draft_email" in skills
    assert "get_company_employees" not in skills
