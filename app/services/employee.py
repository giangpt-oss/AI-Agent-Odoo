import logging
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.employee import Employee
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Mock database cache cho testing hoặc khi database chưa seed dữ liệu
DEV_EMPLOYEES_STORE: dict[int, dict[str, Any]] = {}



class EmployeeService:
    """Quản lý thông tin định danh và quyền hạn của nhân viên qua Telegram Chat ID."""

    @staticmethod
    async def resolve_employee_identity(chat_id: int) -> dict[str, Any] | None:
        """Tìm kiếm nhân viên dựa trên Telegram Chat ID."""
        from app.services.identity_store import identity_store
        linked = identity_store.get(chat_id)
        if linked and linked["profile"].get("is_active"):
            return linked["profile"]
        return None

    @staticmethod
    async def lookup_and_link_from_odoo(chat_id: int, identifier: str) -> dict[str, Any] | None:
        """Tra cứu nhân viên trực tiếp từ Odoo Cloud theo Email hoặc Số điện thoại,
        tự động trích xuất vai trò & quyền hạn, và liên kết với Telegram Chat ID.
        """
        # An email/phone lookup is not proof of account ownership.
        raise PermissionError("Đăng nhập Odoo để chứng minh quyền sở hữu tài khoản.")



employee_service = EmployeeService()

