import pytest
from app.domain.employee import EmployeeProfile, SeniorityLevel, DepartmentType
from app.domain.roles import StandardRole, RoleClassifier
from app.domain.risk_levels import ActionRiskLevel, get_skill_risk_level
from app.domain.capabilities import BusinessCapability, CAPABILITY_REGISTRY
from app.domain.task_models import DraftSession, DraftItem, StructuredTaskResult
from app.services.employee_profile_service import employee_profile_service


def test_employee_profile_safe_identity():
    profile = EmployeeProfile(
        user_id="odoo-1",
        full_name="Nguyễn Văn Nam",
        job_title="Trưởng phòng Kinh doanh",
        department="Phòng Kinh doanh",
        department_type=DepartmentType.SALES,
        seniority=SeniorityLevel.HEAD,
        manager_name="Trần Văn Giám Đốc",
        company_email="nam@haiminhtsc.vn",
        roles=["employee", "sales_manager"],
        permissions=["READ_ODOO_CRM", "READ_FILES"]
    )

    safe = profile.to_safe_identity_dict()
    assert safe["name"] == "Nguyễn Văn Nam"
    assert safe["job_position"] == "Trưởng phòng Kinh doanh"
    assert safe["department"] == "Phòng Kinh doanh"
    assert safe["seniority"] == "HEAD"
    assert safe["manager"] == "Trần Văn Giám Đốc"
    assert safe["company_email"] == "nam@haiminhtsc.vn"
    assert "roles" not in safe
    assert "permissions" not in safe

    text = profile.format_who_am_i_response()
    assert "Nguyễn Văn Nam" in text
    assert "Trưởng phòng Kinh doanh" in text
    assert "Trần Văn Giám Đốc" in text


def test_role_classifier():
    # 1. Sales Head
    sales_profile = employee_profile_service.build_profile_from_dict({
        "id": "1",
        "full_name": "Nguyễn Văn Nam",
        "job_title": "Head of Sales",
        "department": "Kinh doanh",
        "roles": ["employee", "sales_manager"]
    })
    assert RoleClassifier.classify(sales_profile) == StandardRole.HEAD_OF_SALES

    # 2. HR Head
    hr_profile = employee_profile_service.build_profile_from_dict({
        "id": "2",
        "full_name": "Lê Thị Hoa",
        "job_title": "Trưởng phòng Nhân sự",
        "department": "Nhân sự",
        "roles": ["employee", "hr_manager"]
    })
    assert RoleClassifier.classify(hr_profile) == StandardRole.HEAD_OF_HR

    # 3. Chief Accountant
    acc_profile = employee_profile_service.build_profile_from_dict({
        "id": "3",
        "full_name": "Phạm Văn Minh",
        "job_title": "Kế toán trưởng",
        "department": "Kế toán",
        "roles": ["employee", "account_manager"]
    })
    assert RoleClassifier.classify(acc_profile) == StandardRole.CHIEF_ACCOUNTANT


def test_risk_levels():
    assert get_skill_risk_level("get_user_profile") == ActionRiskLevel.LEVEL_0_READ_ONLY
    assert get_skill_risk_level("draft_email") == ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS
    assert get_skill_risk_level("create_task") == ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE
    assert get_skill_risk_level("send_email") == ActionRiskLevel.LEVEL_3_HIGH_IMPACT
    assert get_skill_risk_level("delete_task") == ActionRiskLevel.LEVEL_3_HIGH_IMPACT


def test_structured_task_result():
    res = StructuredTaskResult(
        status="Completed",
        summary="Đã kiểm tra danh sách cơ hội kinh doanh tồn đọng.",
        results_data=["23 khách hàng được kiểm tra", "7 khách hàng quá hạn follow-up > 5 ngày"],
        changes_performed=["Đã tạo 7 bản nháp email"],
        attention_required=["Khách hàng ABC chưa có email"],
        next_suggested_steps=["Bạn có muốn gửi email cho 6 khách hàng còn lại không?"]
    )
    md = res.to_markdown()
    assert "COMPLETED" in md
    assert "23 khách hàng được kiểm tra" in md
    assert "Đã tạo 7 bản nháp email" in md
    assert "Khách hàng ABC chưa có email" in md
    assert "Bạn có muốn gửi email" in md
