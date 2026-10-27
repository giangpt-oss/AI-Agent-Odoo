import pytest
from app.services.gateway_agent_dispatcher import gateway_agent_dispatcher


def test_crm_create_gateway_contract_formatting():
    """Dùng nguyên response thực tế của Gateway để kiểm tra formatter agent cho crm.create."""
    gateway_resp = {
        "status": "completed",
        "result": {
            "id": 105,
            "name": "Dự án Chuyển đổi số Doanh nghiệp",
        },
    }
    msgs = gateway_agent_dispatcher._format_action_result("crm.create", gateway_resp)
    assert len(msgs) == 1
    content = msgs[0]
    # Phải hiển thị đúng ID và tên cơ hội
    assert "105" in content
    assert "Dự án Chuyển đổi số Doanh nghiệp" in content
    # Tuyệt đối không suy đoán doanh thu là 0 VNĐ khi Gateway không trả trường này
    assert "0 VNĐ" not in content
    assert "thiếu mã cơ hội" not in content


def test_crm_create_with_expected_revenue():
    """Khi Gateway trả về expected_revenue, formatter hiển thị doanh thu."""
    gateway_resp = {
        "status": "completed",
        "result": {
            "id": 106,
            "name": "Hợp đồng ERP",
            "expected_revenue": 250000000.0,
        },
    }
    msgs = gateway_agent_dispatcher._format_action_result("crm.create", gateway_resp)
    content = msgs[0]
    assert "106" in content
    assert "250,000,000 VNĐ" in content


def test_crm_update_gateway_contract_formatting():
    """Dùng nguyên response thực tế của Gateway cho crm.update: {'id': 105, 'updated': True}."""
    gateway_resp = {
        "status": "completed",
        "result": {
            "id": 105,
            "updated": True,
        },
    }
    msgs = gateway_agent_dispatcher._format_action_result("crm.update", gateway_resp)
    assert len(msgs) == 1
    content = msgs[0]
    # Phải hiển thị đúng ID đã cập nhật thành công
    assert "105" in content
    assert "thiếu mã cơ hội" not in content
    assert "Đã cập nhật cơ hội CRM `105`" in content


def test_crm_missing_id_reports_unverified():
    """Khi kết quả completed nhưng thiếu ID, phải cảnh báo chưa xác minh được kết quả."""
    gateway_resp = {
        "status": "completed",
        "result": {},
    }
    msgs1 = gateway_agent_dispatcher._format_action_result("crm.create", gateway_resp)
    assert any("chưa được xác minh" in m for m in msgs1)

    msgs2 = gateway_agent_dispatcher._format_action_result("crm.update", gateway_resp)
    assert any("chưa được xác minh" in m for m in msgs2)
