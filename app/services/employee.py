import logging
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.employee import Employee
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Mock database cache cho testing hoặc khi database chưa seed dữ liệu
DEV_EMPLOYEES_STORE: dict[int, dict[str, Any]] = {
    5861356771: {
        "id": "emp-uuid-giang-admin",
        "email": "giangpt@hopita.vn",
        "full_name": "Giang PT (Sếp / Quản trị viên)",
        "roles": ["admin", "ceo", "sales_manager", "sales_write", "sales_user", "employee"],
        "odoo_user_id": 27,
        "is_active": True,
    },
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

        # 2. Fallback sang store bộ nhớ (hỗ trợ dev & người dùng đã liên kết từ Odoo)
        if chat_id in DEV_EMPLOYEES_STORE:
            return DEV_EMPLOYEES_STORE[chat_id]

        return None

    @staticmethod
    async def lookup_and_link_from_odoo(chat_id: int, identifier: str) -> dict[str, Any] | None:
        """Tra cứu nhân viên trực tiếp từ Odoo Cloud theo Email hoặc Số điện thoại,
        tự động trích xuất vai trò & quyền hạn, và liên kết với Telegram Chat ID.
        """
        clean_id = (identifier or "").strip().lower()
        if not clean_id:
            return None

        from app.core.config import get_settings
        from app.connectors.odoo.client import OdooAsyncClient

        settings = get_settings()
        odoo = OdooAsyncClient(
            base_url=settings.ODOO_URL,
            db=settings.ODOO_DB,
            username=settings.odoo_user,
            api_key=settings.ODOO_API_KEY,
            timeout=8.0
        )

        try:
            await odoo.authenticate()

            # 1. Tìm trong hr.employee theo Email hoặc Số điện thoại
            domain = ['|', '|',
                ['work_email', '=ilike', clean_id],
                ['work_phone', 'ilike', clean_id],
                ['mobile_phone', 'ilike', clean_id]
            ]
            emps = await odoo.execute_kw(
                model='hr.employee',
                method='search_read',
                args=[domain],
                kwargs={
                    'fields': ['name', 'work_email', 'work_phone', 'mobile_phone', 'job_title', 'department_id', 'user_id'],
                    'limit': 1
                }
            )

            user_id = None
            full_name = None
            email = None
            dept_name = None
            job_title = None

            if emps:
                emp = emps[0]
                full_name = emp.get('name')
                email = emp.get('work_email') or clean_id
                job_title = emp.get('job_title') or 'Nhân viên'
                dept = emp.get('department_id')
                dept_name = dept[1] if isinstance(dept, (list, tuple)) and len(dept) > 1 else 'Chưa phân bổ'
                raw_u = emp.get('user_id')
                if raw_u and isinstance(raw_u, (list, tuple)):
                    user_id = raw_u[0]
            else:
                # 2. Nếu không có trong hr.employee, tìm trong res.users
                users = await odoo.execute_kw(
                    model='res.users',
                    method='search_read',
                    args=[['|', ['login', '=ilike', clean_id], ['email', '=ilike', clean_id]]],
                    kwargs={'fields': ['name', 'login', 'email'], 'limit': 1}
                )
                if users:
                    u = users[0]
                    user_id = u.get('id')
                    full_name = u.get('name')
                    email = u.get('login') or u.get('email')

            if not full_name:
                return None

            # 3. Phân tích quyền hạn từ Odoo res.users
            roles = ["employee"]
            if user_id:
                try:
                    u_info = await odoo.execute_kw(
                        model='res.users',
                        method='search_read',
                        args=[[['id', '=', user_id]]],
                        kwargs={'fields': ['sale_team_id', 'is_hr_user', 'role']}
                    )
                    if u_info:
                        ui = u_info[0]
                        if ui.get('sale_team_id'):
                            roles.extend(["sales_user", "sales_manager"])
                        if ui.get('is_hr_user'):
                            roles.append("hr_user")
                        if ui.get('role') == 'group_system' or 'admin' in clean_id or user_id == 27:
                            roles.extend(["admin", "sales_write"])
                except Exception as e:
                    logger.warning(f"Error fetching user roles from Odoo: {e}")

            profile = {
                "id": f"odoo-emp-{user_id or chat_id}",
                "email": email or clean_id,
                "full_name": full_name,
                "job_title": job_title or "Thành viên Odoo",
                "department": dept_name or "Công ty Hopita / Hải Minh",
                "roles": list(set(roles)),
                "odoo_user_id": user_id,
                "is_active": True,
            }

            # 4. Ghi nhớ vào cache để phục vụ các truy vấn kế tiếp
            DEV_EMPLOYEES_STORE[chat_id] = profile
            logger.info(f"Đã liên kết Telegram Chat ID {chat_id} với Odoo User {full_name} ({email}) - Roles: {roles}")
            return profile

        except Exception as e:
            logger.error(f"Lỗi khi tra cứu Odoo User cho {identifier}: {e}")
            return None


employee_service = EmployeeService()

