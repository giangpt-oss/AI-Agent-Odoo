import pytest
from app.services.verification_service import verification_service


def test_quality_review_job_description():
    # JD incomplete
    bad_jd = "Chúng tôi tuyển lập trình viên Python lương cao. Đi làm ngay tại Hà Nội."
    ok, issues = verification_service.review_quality("jd", bad_jd)
    assert ok is False
    assert any("Nhiệm vụ" in i or "Trách nhiệm" in i for i in issues)
    assert any("Yêu cầu" in i for i in issues)

    # JD complete
    good_jd = (
        "Tuyển dụng: Kỹ sư Python Backend\n\n"
        "1. Trách nhiệm công việc:\n- Phát triển hệ thống AI Assistant\n- Kết nối API Odoo ERP\n\n"
        "2. Yêu cầu năng lực & kỹ năng:\n- Tối thiểu 3 năm kinh nghiệm Python, FastAPI\n- Thành thạo SQL, Git\n\n"
        "3. Quyền lợi và chế độ đãi ngộ:\n- Mức lương cạnh tranh, thưởng dự án, bảo hiểm đầy đủ."
    )
    ok_good, issues_good = verification_service.review_quality("jd", good_jd)
    assert ok_good is True
    assert len(issues_good) == 0


def test_quality_review_email():
    bad_email = "gửi báo giá cho tôi gấp"
    ok, issues = verification_service.review_quality("email", bad_email)
    assert ok is False
    assert any("lời chào" in i for i in issues)

    good_email = (
        "Kính gửi Quý khách hàng,\n\n"
        "Em gửi anh bảng báo giá chi tiết thiết bị y tế theo yêu cầu sáng nay.\n"
        "Anh vui lòng xem file đính kèm và phản hồi giúp em nhé ạ.\n\n"
        "Trân trọng cảm ơn anh,\nNguyễn Văn Nam - Trưởng phòng Kinh doanh"
    )
    ok_good, issues_good = verification_service.review_quality("email", good_email)
    assert ok_good is True


@pytest.mark.asyncio
async def test_execution_review():
    # 1. Email success
    ok, msg, audit = await verification_service.verify_execution("send_email", {"status": "SUCCESS", "message_id": "msg_001"})
    assert ok is True
    assert audit["verified"] is True
    assert "msg_001" in audit["notes"]

    # 2. Email failure
    ok_fail, msg_fail, _ = await verification_service.verify_execution("send_email", {"status": "ERROR", "error": "SMTP failed"})
    assert ok_fail is False

    # 3. Callback verification for Odoo record
    async def mock_record_verifier():
        return True

    ok_rec, _, audit_rec = await verification_service.verify_execution("update_record", {"id": 123}, target_verifier_fn=mock_record_verifier)
    assert ok_rec is True
    assert audit_rec["verified"] is True
