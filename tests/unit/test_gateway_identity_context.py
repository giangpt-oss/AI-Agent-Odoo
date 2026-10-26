import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.gateway_context import GatewayUserContext, build_gateway_user_context, GatewayPermission
from app.api.v1.internal import format_whoami
from app.main import create_application
from app.services.gateway_client import GatewayServiceError


@pytest.fixture
def gateway_settings():
    return Settings(
        INTEGRATION_MODE="gateway",
        GATEWAY_TO_AGENT_SECRET="gateway_secret_for_identity_tests_min32",
        AGENT_TO_GATEWAY_SECRET="agent_secret_for_identity_tests_min32",
    )


@pytest.fixture
def client(gateway_settings):
    app = create_application(settings=gateway_settings)
    return TestClient(app)


class TestGatewayUserContext:
    def test_build_context_with_complete_info(self):
        raw = {
            "user_id": 10,
            "tenant": "https://erp.example.com",
            "database": "odoo_db",
            "odoo_uid": 201,
            "telegram_user_id": 9991,
            "chat_id": 9991,
            "name": "Nguyễn Văn A",
            "login": "a.nguyen@example.com",
            "tz": "Asia/Ho_Chi_Minh",
            "job_title": "Kỹ sư AI",
            "department": "Khối Công nghệ",
            "permissions": ["tasks.read", "calendar.read"],
            "authorization_checked_at": "2026-10-26T08:00:00Z",
            # Credentials that should NEVER be accepted or leaked
            "password": "secret_password",
            "api_key": "raw_odoo_key",
        }

        ctx = build_gateway_user_context(raw)
        assert ctx.user_id == 10
        assert ctx.tenant == "https://erp.example.com"
        assert ctx.database == "odoo_db"
        assert ctx.odoo_uid == 201
        assert ctx.name == "Nguyễn Văn A"
        assert ctx.login == "a.nguyen@example.com"
        assert ctx.timezone == "Asia/Ho_Chi_Minh"
        assert ctx.display_job_title == "Kỹ sư AI"
        assert ctx.display_department == "Khối Công nghệ"
        assert ctx.has_permission("tasks.read") is True
        assert ctx.has_permission("calendar.read") is True
        assert ctx.has_permission("crm.read") is False
        assert ctx.authorization_checked_at == "2026-10-26T08:00:00Z"
        assert ctx.owner_key == "erp.example.com:odoo_db:201"
        assert not hasattr(ctx, "password")
        assert not hasattr(ctx, "api_key")

    def test_missing_job_title_and_department_displays_no_info(self):
        raw = {
            "user_id": 11,
            "tenant": "https://erp.example.com",
            "database": "odoo_db",
            "odoo_uid": 202,
            "telegram_user_id": 9992,
            "chat_id": 9992,
            "name": "Trần Thị B",
            "login": "b.tran@example.com",
            "job_title": None,
            "department": None,
        }

        ctx = build_gateway_user_context(raw)
        # Bắt buộc: Thiếu chức danh hiển thị “Chưa có thông tin”; không coi mặc định “Nhân viên” là dữ liệu đã xác minh
        assert ctx.display_job_title == "Chưa có thông tin"
        assert ctx.display_department == "Chưa có thông tin"

    def test_job_title_does_not_infer_permissions(self):
        raw = {
            "user_id": 12,
            "tenant": "https://erp.example.com",
            "database": "odoo_db",
            "odoo_uid": 1,
            "telegram_user_id": 9993,
            "chat_id": 9993,
            "name": "Giám Đốc C",
            "login": "admin@example.com",
            "job_title": "Tổng Giám đốc",
            "department": "Ban Giám đốc",
            "permissions": [],  # Gateway không cấp quyền nào
        }

        ctx = build_gateway_user_context(raw)
        # Bắt buộc: Không dùng chức danh “giám đốc” hay login để suy ra quyền admin
        assert ctx.has_permission(GatewayPermission.TASKS_READ) is False
        assert ctx.has_permission(GatewayPermission.CRM_WRITE) is False
        assert ctx.has_permission("admin") is False


class TestWhoamiFormatting:
    def test_format_whoami_with_permissions_and_verified_title(self):
        ctx = GatewayUserContext(
            user_id=10,
            tenant="https://erp.example.com",
            database="odoo_db",
            odoo_uid=201,
            telegram_user_id=9991,
            chat_id=9991,
            name="Nguyễn Văn A",
            login="a.nguyen@example.com",
            timezone="Asia/Ho_Chi_Minh",
            job_title="Chuyên viên Phân tích",
            department="Tài chính",
            permissions={"tasks.read", "calendar.read"},
            authorization_checked_at="2026-10-26T08:00:00Z",
        )

        msg = format_whoami(ctx)
        assert "Nguyễn Văn A" in msg
        assert "a.nguyen@example.com" in msg
        assert "Chuyên viên Phân tích" in msg
        assert "Tài chính" in msg
        assert "calendar.read" in msg
        assert "tasks.read" in msg
        assert "2026-10-26T08:00:00Z" in msg

    def test_format_whoami_missing_title_shows_no_info(self):
        ctx = GatewayUserContext(
            user_id=11,
            tenant="https://erp.example.com",
            database="odoo_db",
            odoo_uid=202,
            telegram_user_id=9992,
            chat_id=9992,
            name="Trần Thị B",
            login="b.tran@example.com",
            timezone="Asia/Bangkok",
            job_title=None,
            department=None,
            permissions=set(),
        )

        msg = format_whoami(ctx)
        assert "Chưa có thông tin" in msg
        assert "Nhân viên" not in msg
        assert "Chưa phân bổ" not in msg
        assert "Chưa có quyền bổ sung" in msg


class TestGatewayDispatcherPermissionsAndErrors:
    @pytest.mark.asyncio
    async def test_task_list_refuses_when_permission_missing(self, client, gateway_settings):
        headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
        payload = {
            "request_id": "req-task-perm-1",
            "gateway_user_id": 50,
            "telegram_user_id": 8881,
            "chat_id": 8881,
            "text": "Xem danh sách công việc",
        }

        mock_user = {
            "user_id": 50,
            "tenant": "https://erp.example.com",
            "database": "odoo_db",
            "odoo_uid": 301,
            "telegram_user_id": 8881,
            "chat_id": 8881,
            "name": "Lê Văn D",
            "login": "d.le@example.com",
            "permissions": ["calendar.read"],  # Thiếu tasks.read!
        }

        with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
            mock_res.return_value = mock_user
            resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            msg = data["messages"][0]
            assert "chưa được cấp quyền" in msg or "tasks.read" in msg

    @pytest.mark.asyncio
    async def test_task_list_executes_when_permission_granted(self, client, gateway_settings):
        headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
        payload = {
            "request_id": "req-task-perm-2",
            "gateway_user_id": 51,
            "telegram_user_id": 8882,
            "chat_id": 8882,
            "text": "Xem danh sách công việc",
        }

        mock_user = {
            "user_id": 51,
            "tenant": "https://erp.example.com",
            "database": "odoo_db",
            "odoo_uid": 302,
            "telegram_user_id": 8882,
            "chat_id": 8882,
            "name": "Hoàng Văn E",
            "login": "e.hoang@example.com",
            "permissions": ["tasks.read"],  # Có tasks.read!
        }

        with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
            mock_res.return_value = mock_user
            resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            msg = data["messages"][0]
            assert "Danh sách công việc cá nhân" in msg

    @pytest.mark.asyncio
    async def test_gateway_service_error_does_not_say_wrong_password(self, client, gateway_settings):
        headers = {"Authorization": f"Bearer {gateway_settings.GATEWAY_TO_AGENT_SECRET}"}
        payload = {
            "request_id": "req-gw-err-1",
            "gateway_user_id": 52,
            "telegram_user_id": 8883,
            "chat_id": 8883,
            "text": "/today",
        }

        with patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_res:
            mock_res.side_effect = GatewayServiceError("Gateway upstream connection timeout")
            resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            msg = data["messages"][0]
            # Bắt buộc: Gateway tạm lỗi không được trình bày thành “Sai mật khẩu” hoặc “Mất liên kết”
            assert "Sai mật khẩu" not in msg
            assert "kết nối đến Cổng Gateway" in msg or "sự cố kết nối" in msg
