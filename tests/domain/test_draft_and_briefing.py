import pytest
from app.domain.employee import EmployeeProfile, SeniorityLevel, DepartmentType
from app.domain.task_models import DraftItem
from app.services.morning_briefing_service import morning_briefing_service
from app.services.draft_manager import draft_manager


@pytest.fixture
def sales_head():
    return EmployeeProfile(
        user_id="sales-user-1",
        full_name="Nguyễn Văn Nam",
        job_title="Head of Sales",
        department="Kinh doanh",
        department_type=DepartmentType.SALES,
        seniority=SeniorityLevel.HEAD,
        company_email="nam@haiminhtsc.vn",
        roles=["employee", "sales_manager"],
        permissions=["READ_ODOO_CRM", "READ_FILES", "READ_EMAIL", "CREATE_EMAIL_DRAFT"]
    )


@pytest.fixture
def hr_head():
    return EmployeeProfile(
        user_id="hr-user-1",
        full_name="Lê Thị Hoa",
        job_title="Trưởng phòng Nhân sự",
        department="Nhân sự",
        department_type=DepartmentType.HR,
        seniority=SeniorityLevel.HEAD,
        company_email="hoa@haiminhtsc.vn",
        roles=["employee", "hr_manager"],
        permissions=["READ_ODOO_HR", "READ_FILES", "READ_EMAIL", "CREATE_EMAIL_DRAFT"]
    )


@pytest.mark.asyncio
async def test_morning_briefing_different_for_roles(sales_head, hr_head):
    sales_briefing = await morning_briefing_service.generate_briefing(sales_head)
    assert "Sales" in sales_briefing.status
    assert any("cơ hội kinh doanh" in item or "khách hàng" in item for item in sales_briefing.results_data)

    hr_briefing = await morning_briefing_service.generate_briefing(hr_head)
    assert "HR" in hr_briefing.status
    assert any("ứng viên" in item or "phép" in item or "nhân sự" in item for item in hr_briefing.results_data)


def test_draft_session_selective_refinement():
    user_id = "test-user-123"
    draft1 = DraftItem(
        title="Email follow-up 1",
        recipient="customer1@company.com",
        subject="Hỏi thăm tiến độ dự án A",
        content="Kính gửi Quý công ty, xin hỏi dự án A hiện tiến độ thế nào?"
    )
    draft2 = DraftItem(
        title="Email follow-up 2",
        recipient="customer2@company.com",
        subject="Báo giá thiết bị y tế B",
        content="Kính gửi Quý khách, xin thông báo báo giá B chuẩn bị hết hạn."
    )

    session = draft_manager.create_or_replace_session(user_id, [draft1, draft2])
    assert len(session.items) == 2

    # Lệnh: 'Make email number 2 more friendly' (chỉ số 1 trong mảng 0-indexed)
    updated = draft_manager.refine_draft_with_instructions(
        user_id=user_id,
        item_index=1,
        instruction="thân thiện hơn"
    )

    assert updated is not None
    assert updated.version == 2
    assert "thân thiện" in updated.metadata["last_revision_note"]
    assert "Dạ chào" in updated.content

    # Email 1 phải giữ nguyên không bị ảnh hưởng!
    assert session.items[0].version == 1
    assert "Dạ chào" not in session.items[0].content
    assert session.items[0].content == "Kính gửi Quý công ty, xin hỏi dự án A hiện tiến độ thế nào?"

    formatted = draft_manager.format_drafts_for_review(session)
    assert "DANH SÁCH BẢN NHÁP (2 mục đang chờ duyệt)" in formatted
    assert "customer1@company.com" in formatted
    assert "customer2@company.com" in formatted
