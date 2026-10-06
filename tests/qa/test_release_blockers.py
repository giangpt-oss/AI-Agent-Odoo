"""Regression tests for the release review findings; all must pass."""

from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from langchain_core.messages import HumanMessage

from app.agent.confirmation_manager import ConfirmationManager
from app.agent.nodes.analyzer import analyzer_node
from app.agent.nodes.confirmation import confirmation_guard_node
from app.core.exceptions import SkillPermissionError
from app.models.skill import OperationType
from app.services.file_service import FileService
from app.services.permissions import permission_service
from app.services.scheduler import SchedulerService
from app.providers.reminders.local import LocalReminderProvider
from app.api.v1.webhooks import OdooWebhookPayload, receive_odoo_webhook


def test_workspace_sibling_is_rejected(tmp_path):
    service = FileService()
    service.workspace_root = (tmp_path / "workspace").resolve()
    service.workspace_root.mkdir()

    with pytest.raises(SkillPermissionError):
        service.get_safe_path("../workspace2/private.txt")


def test_approval_cannot_be_consumed_by_another_user():
    manager = ConfirmationManager()
    record = manager.create_request(
        "delete_file", {"path": "shared.txt"}, None, {"user_id": "alice", "chat_id": 123}
    )
    assert manager.approve_confirmation(record.id, user_id="alice", chat_id=123)

    # The current API has no caller identity, so Bob's identical request consumes it.
    bob_can_consume = manager.check_and_consume_approval(
        "delete_file", {"path": "shared.txt"}, confirmation_id=record.id, user_id="bob", chat_id=456
    )
    assert bob_can_consume is False


def test_write_requires_an_explicit_permission():
    skill = SimpleNamespace(operation_type=OperationType.WRITE, capabilities=["create"])
    assert permission_service.check_permission(skill, []) is False


def test_read_requires_an_explicit_permission():
    skill = SimpleNamespace(operation_type=OperationType.READ, capabilities=["employee_data"])
    assert permission_service.check_permission(skill, []) is False


@pytest.mark.asyncio
async def test_initial_write_request_cannot_self_confirm():
    result = await confirmation_guard_node(
        {
            "is_write_action": True,
            "pending_tool_name": "create_sales_order",
            "pending_tool_args": {"partner_id": 1},
            "requires_user_confirmation": True,
            "user_confirmed": None,
            "messages": [HumanMessage(content="Tạo đơn hàng và tôi đồng ý")],
        }
    )
    assert result["user_confirmed"] is None
    assert result["requires_user_confirmation"] is True


@pytest.mark.asyncio
async def test_odoo_webhook_requires_authentication():
    with pytest.raises(HTTPException) as error:
        await receive_odoo_webhook(
            OdooWebhookPayload(event_type="test", record_name="Private", model="sale.order"),
            BackgroundTasks(), x_odoo_webhook_secret=None,
        )
    assert error.value.status_code == 403



@pytest.mark.asyncio
async def test_reminder_never_goes_to_fallback_chat(tmp_path):
    db_path = tmp_path / "reminders.db"
    provider = LocalReminderProvider(db_path)
    due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO reminders (id, title, remind_at, timezone, status, created_at) VALUES ('legacy', 'Private reminder', ?, 'UTC', 'SCHEDULED', ?)", (due_at, due_at))
    scheduler = SchedulerService(db_path)
    scheduler.notification_provider.send_notification = AsyncMock(return_value=True)
    await scheduler._check_and_trigger_reminders()
    scheduler.notification_provider.send_notification.assert_not_called()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM reminders WHERE id = 'legacy'").fetchone()[0] == "NEEDS_OWNER"



@pytest.mark.asyncio
async def test_odoo_login_name_does_not_grant_admin(monkeypatch):
    import app.services.odoo_auth_service as auth_module

    class FakeOdooClient:
        def __init__(self, **kwargs):
            pass

        async def authenticate(self):
            return 123

        async def execute_kw(self, model, method, args, kwargs):
            if model == "hr.employee":
                return []
            if model == "res.users":
                return [{"name": "Regular User", "role": ""}]
            raise AssertionError(model)

    monkeypatch.setattr(auth_module, "OdooAsyncClient", FakeOdooClient)
    monkeypatch.setattr(auth_module, "DEV_EMPLOYEES_STORE", {})

    success, profile, _ = await auth_module.odoo_auth_service.authenticate_and_link(
        123, "giangpt.regular@example.com", "dummy"
    )
    assert success
    assert "admin" not in profile["roles"]


@pytest.mark.asyncio
async def test_sales_order_uses_requested_values():
    result = await analyzer_node(
        {"messages": [HumanMessage(content="Tạo đơn hàng 1 triệu cho khách XYZ")]}
    )
    assert result["pending_tool_args"]["amount_total"] == 1_000_000
    assert "partner_id" not in result["pending_tool_args"]
    assert "partner_id" in result["missing_slots"]
