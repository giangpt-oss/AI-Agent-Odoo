from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("roles", "expected_permission"),
    [
        (["admin", "employee"], "Toàn quyền Quản trị"),
        (["employee"], "Odoo CRM: Không có quyền"),
    ],
)
async def test_who_am_i_reply(monkeypatch, roles, expected_permission):
    monkeypatch.setenv("DEBUG", "true")
    from scripts import run_telegram_bot_polling as bot
    from app.services.odoo_auth_service import odoo_auth_service

    employee = {
        "full_name": "Nguyen Van A",
        "email": "a@example.com",
        "roles": roles,
        "odoo_user_id": 42,
    }
    monkeypatch.setattr(
        bot.employee_service,
        "resolve_employee_identity",
        AsyncMock(return_value=employee),
    )
    monkeypatch.setattr(odoo_auth_service, "get_verification_url", lambda _: "")

    reply = await bot.handle_user_query("Tôi là ai?", "A", 123)

    assert "Nguyen Van A" in reply
    assert "UID: 42" in reply
    assert expected_permission in reply
