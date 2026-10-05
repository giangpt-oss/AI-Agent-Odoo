import logging
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.employee import Employee
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Mock database cache cho testing hoặc khi database chưa seed dữ liệu
DEV_EMPLOYEES_STORE: dict[int, dict[str, Any]] = {
    999999: {
        "id": "emp-uuid-sales-01",
        "email": "sales_rep@company.com",
        "full_name": "Nguyễn Văn Sales",
        "roles": ["sales_user", "employee"],
        "is_active": True,
    },
    888888: {
        "id": "emp-uuid-wh-01",
        "email": "warehouse_user@company.com",
        "full_name": "Trần Văn Kho",
        "roles": ["warehouse_user", "employee"],
        "is_active": True,
    },
    777777: {
        "id": "emp-uuid-admin-01",
        "email": "admin@company.com",
        "full_name": "Lê Quản Trị",
        "roles": ["admin", "sales_manager", "sales_write", "sales_user", "employee"],
        "is_active": True,
    },
}


class EmployeeService:
    """Quản lý thông tin định danh và quyền hạn của nhân viên qua Telegram Chat ID."""

    @staticmethod
    async def resolve_employee_identity(chat_id: int) -> dict[str, Any] | None:
        """Tìm kiếm nhân viên dựa trên Telegram Chat ID."""
        # 1. Thử truy vấn từ PostgreSQL nếu có session
        try:
            async with AsyncSessionLocal() as session:
                query = select(Employee).where(
                    Employee.telegram_chat_id == chat_id,
                    Employee.is_active.is_(True)
                )
                result = await session.execute(query)
                employee = result.scalar_one_or_none()
                if employee:
                    return {
                        "id": str(employee.id),
                        "email": employee.email,
                        "full_name": employee.full_name,
                        "roles": employee.roles,
                        "is_active": employee.is_active,
                        "odoo_user_id": employee.odoo_user_id,
                    }
        except Exception as e:
            logger.debug(f"Direct DB query fallback: {e}")

        # 2. Fallback sang store cấu hình sẵn (hỗ trợ dev/test)
        if chat_id in DEV_EMPLOYEES_STORE:
            return DEV_EMPLOYEES_STORE[chat_id]

        return None


employee_service = EmployeeService()
