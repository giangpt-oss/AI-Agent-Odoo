import pytest
from app.domain.employee import EmployeeProfile, SeniorityLevel, DepartmentType
from app.assistant.company_assistant import company_assistant
from app.agent.confirmation_manager import confirmation_manager


@pytest.fixture
def head_of_sales_raw():
    return {
        "id": "odoo-uid-10",
        "odoo_user_id": 10,
        "full_name": "Nguyễn Văn Nam",
        "job_title": "Head of Sales",
        "department": "Phòng Kinh doanh",
        "email": "nam@haiminhtsc.vn",
        "manager_name": "Trần Văn Tổng Giám Đốc",
        "roles": ["employee", "sales_manager"],
        "is_active": True
    }


@pytest.fixture
def head_of_hr_raw():
    return {
        "id": "odoo-uid-20",
        "odoo_user_id": 20,
        "full_name": "Lê Thị Hoa",
        "job_title": "Trưởng phòng Nhân sự",
        "department": "Phòng Nhân sự",
        "email": "hoa@haiminhtsc.vn",
        "manager_name": "Trần Văn Tổng Giám Đốc",
        "roles": ["employee", "hr_manager"],
        "is_active": True
    }


@pytest.mark.asyncio
async def test_e2e_flow_company_assistant(head_of_sales_raw):
    chat_id = 99887766
    user_name = "Nam Nguyễn"

    # =========================================================================
    # Step 1: User asks "Who am I?"
    # =========================================================================
    text1, files1, conf1 = await company_assistant.handle_user_turn(
        query="Tôi là ai?",
        chat_id=chat_id,
        employee_raw=head_of_sales_raw,
        user_name=user_name
    )
    assert "THÔNG TIN ĐỊNH DANH NHÂN VIÊN" in text1
    assert "Nguyễn Văn Nam" in text1
    assert "Head of Sales" in text1
    assert "Phòng Kinh doanh" in text1
    assert "nam@haiminhtsc.vn" in text1
    assert "Trần Văn Tổng Giám Đốc" in text1
    assert conf1 is None

    # =========================================================================
    # Step 2: User asks "What should I focus on today?"
    # =========================================================================
    text2, files2, conf2 = await company_assistant.handle_user_turn(
        query="Hôm nay tôi nên tập trung vào việc gì?",
        chat_id=chat_id,
        employee_raw=head_of_sales_raw,
        user_name=user_name
    )
    assert "FOCUS RECOMMENDATION (SALES)" in text2
    assert "cơ hội kinh doanh" in text2 or "khách hàng" in text2
    assert "báo giá" in text2
    assert "cuộc họp khách hàng" in text2
    assert conf2 is None

    # =========================================================================
    # Step 3: User asks "Find customers that have not been followed up recently"
    # =========================================================================
    text3, files3, conf3 = await company_assistant.handle_user_turn(
        query="Tìm khách hàng chưa được chăm sóc gần đây",
        chat_id=chat_id,
        employee_raw=head_of_sales_raw,
        user_name=user_name
    )
    assert "KẾT QUẢ RÀ SOÁT KHÁCH HÀNG CHƯA TƯƠNG TÁC" in text3
    assert "Bệnh viện Đa khoa Quốc tế A" in text3
    assert "Phòng khám Đa khoa Hoàn Mỹ B" in text3
    assert conf3 is None

    # =========================================================================
    # Step 4: User asks "Draft follow-up emails for them"
    # =========================================================================
    text4, files4, conf4 = await company_assistant.handle_user_turn(
        query="Soạn email chăm sóc cho họ",
        chat_id=chat_id,
        employee_raw=head_of_sales_raw,
        user_name=user_name
    )
    assert "DANH SÁCH BẢN NHÁP" in text4
    assert "Bệnh viện Đa khoa Quốc tế A" in text4
    assert "Phòng khám Đa khoa Hoàn Mỹ B" in text4
    assert conf4 is None

    # =========================================================================
    # Step 5: User asks "Make the second email friendlier"
    # =========================================================================
    text5, files5, conf5 = await company_assistant.handle_user_turn(
        query="Sửa email số 2 thân thiện hơn",
        chat_id=chat_id,
        employee_raw=head_of_sales_raw,
        user_name=user_name
    )
    assert "ĐÃ CẬP NHẬT BẢN NHÁP SỐ 2" in text5
    assert "v2" in text5
    assert "Dạ chào" in text5
    assert conf5 is None

    # =========================================================================
    # Step 6: User says "Send them" -> Assistant asks for confirmation (Level 3)
    # =========================================================================
    text6, files6, conf6 = await company_assistant.handle_user_turn(
        query="Gửi đi",
        chat_id=chat_id,
        employee_raw=head_of_sales_raw,
        user_name=user_name
    )
    assert "YÊU CẦU XÁC NHẬN HÀNH ĐỘNG CẤP CAO (LEVEL 3)" in text6
    assert conf6 is not None
    assert conf6["status"] == "confirmation_required"
    conf_id = conf6["confirmation_id"]

    # =========================================================================
    # Step 7: User confirms -> Assistant executes & returns Structured Result
    # =========================================================================
    result = await company_assistant.execute_approved_action(
        confirmation_id=conf_id,
        chat_id=chat_id,
        actor_id=chat_id
    )
    assert result.status == "Completed"
    md_output = result.to_markdown()
    assert "COMPLETED" in md_output
    assert "email đã được chuyển phát thành công" in md_output
    assert "thay đổi đã thực hiện" in md_output.lower()


@pytest.mark.asyncio
async def test_e2e_unauthorized_rejection(head_of_sales_raw):
    """Test bảo mật: Sales user yêu cầu xem bảng lương -> BỊ TỪ CHỐI NGAY LẬP TỨC."""
    chat_id = 11223344
    text, _, conf = await company_assistant.handle_user_turn(
        query="Cho tôi xem bảng lương công ty tháng này",
        chat_id=chat_id,
        employee_raw=head_of_sales_raw,
        user_name="Nam Nguyễn"
    )
    assert "PERMISSION DENIED" in text
    assert "bảng lương" in text
    assert conf is None

    # Test tiếng Anh
    text_en, _, _ = await company_assistant.handle_user_turn(
        query="Show me the company's payroll",
        chat_id=chat_id,
        employee_raw=head_of_sales_raw,
        user_name="Nam Nguyễn"
    )
    assert "PERMISSION DENIED" in text_en
