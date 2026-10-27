import pytest
from app.api.v1.internal import format_connection_status

MOCK_USER = {
    "user_id": 42,
    "name": "Lê Văn Cường",
    "login": "cuong.le@company.com",
    "odoo_uid": 105,
    "tenant": "https://erp.company.com",
    "database": "db_main",
}


def test_format_connection_status_ok_with_full_scopes():
    conns = {
        "status": "OK",
        "user_id": 42,
        "odoo": {"status": "OK", "login": "cuong.le@company.com", "odoo_uid": 105},
        "telegram": {"status": "OK", "telegram_user_id": 999},
        "google": {
            "status": "OK",
            "email": "cuong.le@gmail.com",
            "scopes": [
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
        },
    }
    result = format_connection_status(MOCK_USER, conns)
    assert "**Đã kết nối** (cuong.le@gmail.com)" in result
    assert "✅ Lịch Google" in result
    assert "✅ Gmail (đọc)" in result
    assert "**Đã kết nối** (Odoo UID: 105)" in result
    assert "Trạng thái liên kết: **Đã kết nối**" in result


def test_format_connection_status_ok_missing_scopes():
    conns = {
        "status": "OK",
        "user_id": 42,
        "odoo": {"status": "OK", "login": "cuong.le@company.com", "odoo_uid": 105},
        "telegram": {"status": "OK", "telegram_user_id": 999},
        "google": {
            "status": "OK",
            "email": "cuong.le@gmail.com",
            "scopes": [],
        },
    }
    result = format_connection_status(MOCK_USER, conns)
    # Distinct: status is connected, but scopes are missing
    assert "**Đã kết nối** (cuong.le@gmail.com)" in result
    assert "❌ Thiếu quyền Lịch" in result
    assert "❌ Thiếu quyền Gmail" in result


def test_format_connection_status_not_connected():
    conns = {
        "status": "OK",
        "user_id": 42,
        "odoo": {"status": "NOT_CONNECTED", "login": None, "odoo_uid": None},
        "telegram": {"status": "NOT_CONNECTED", "telegram_user_id": None},
        "google": {
            "status": "NOT_CONNECTED",
            "email": None,
            "scopes": [],
        },
    }
    result = format_connection_status(MOCK_USER, conns)
    assert "Trạng thái: **Chưa kết nối**" in result
    assert "Chưa cấp quyền" in result or "chưa liên kết" in result
    assert "Trạng thái: **Chưa kết nối** (Odoo UID: 105)" in result
    assert "Trạng thái liên kết: **Chưa kết nối**" in result


def test_format_connection_status_reconnect_required():
    conns = {
        "status": "OK",
        "user_id": 42,
        "odoo": {"status": "OK", "login": "cuong.le@company.com", "odoo_uid": 105},
        "telegram": {"status": "OK", "telegram_user_id": 999},
        "google": {
            "status": "RECONNECT_REQUIRED",
            "email": "cuong.le@gmail.com",
            "scopes": [],
        },
    }
    result = format_connection_status(MOCK_USER, conns)
    assert "**Cần kết nối lại**" in result
    assert "kết nối lại" in result.lower()


def test_format_connection_status_error_response():
    conns = {
        "status": "ERROR",
        "message": "Gateway internal error 500",
    }
    result = format_connection_status(MOCK_USER, conns)
    assert "Không thể xác định trạng thái" in result
    # Crucial: error payload must NOT claim "Thiếu quyền"
    assert "Thiếu quyền" not in result
    assert "Không thể xác định quyền" in result


def test_format_connection_status_unknown_status():
    conns = {
        "status": "OK",
        "user_id": 42,
        "odoo": {"status": "UNKNOWN_VAL"},
        "telegram": {"status": "UNKNOWN_VAL"},
        "google": {
            "status": "MYSTERIOUS_STATE",
            "email": "test@example.com",
            "scopes": [],
        },
    }
    result = format_connection_status(MOCK_USER, conns)
    assert "Không thể xác định trạng thái" in result
    assert "Thiếu quyền" not in result
