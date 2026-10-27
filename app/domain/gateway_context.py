from dataclasses import dataclass, field
from typing import Any, Optional, Set
from app.domain.task_identity import build_owner_key


class GatewayPermission:
    """Các quyền nghiệp vụ được Gateway xác minh và ánh xạ."""
    TASKS_READ = "tasks.read"
    TASKS_WRITE = "tasks.write"
    CALENDAR_READ = "calendar.read"
    CALENDAR_WRITE = "calendar.write"
    GMAIL_READ = "gmail.read"
    GMAIL_SEND = "gmail.send"
    CRM_READ = "crm.read"
    CRM_CREATE = "crm.create"
    CRM_WRITE = "crm.write"


@dataclass
class GatewayUserContext:
    """Context thống nhất của người dùng trong Gateway mode.
    
    Quy tắc bắt buộc:
    1. Chức danh/phòng ban dùng để mô tả và cá nhân hóa, không tự cấp quyền.
    2. Không dùng tên đăng nhập, chức danh hay nội dung người dùng để suy ra quyền admin.
    3. Tuyệt đối không lưu trữ credential (mật khẩu, API key, token).
    """
    user_id: int
    tenant: Optional[str] = None
    database: Optional[str] = None
    odoo_uid: Optional[int] = None
    telegram_user_id: int = 0
    chat_id: int = 0
    name: str = "Chưa cập nhật"
    login: str = "Chưa cập nhật"
    timezone: str = "Asia/Bangkok"
    telegram_binding_id: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    manager_name: Optional[str] = None
    permissions: Set[str] = field(default_factory=set)
    authorization_checked_at: Optional[str] = None

    @property
    def owner_key(self) -> str:
        if not self.tenant or not self.database or self.odoo_uid is None:
            return ""
        return build_owner_key(self.tenant, self.database, self.odoo_uid)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def display_job_title(self) -> str:
        if self.job_title and str(self.job_title).strip():
            return str(self.job_title).strip()
        return "Chưa có thông tin"

    @property
    def display_department(self) -> str:
        if self.department and str(self.department).strip():
            return str(self.department).strip()
        return "Chưa có thông tin"

    @property
    def display_manager_name(self) -> str:
        if self.manager_name and str(self.manager_name).strip():
            return str(self.manager_name).strip()
        return "Chưa có thông tin"


def build_gateway_user_context(raw: dict[str, Any]) -> GatewayUserContext:
    """Tạo context an toàn từ dữ liệu Gateway, loại bỏ mọi credential nhạy cảm."""
    raw_perms = raw.get("permissions") or []
    if isinstance(raw_perms, list):
        perms = {str(p).strip() for p in raw_perms if p}
    elif isinstance(raw_perms, set):
        perms = raw_perms
    else:
        perms = set()

    tz = raw.get("tz") or raw.get("timezone") or "Asia/Bangkok"
    odoo_uid_raw = raw.get("odoo_uid")
    odoo_uid = int(odoo_uid_raw) if odoo_uid_raw is not None else None
    tenant = str(raw["tenant"]) if raw.get("tenant") else None
    database = str(raw["database"]) if raw.get("database") else None

    return GatewayUserContext(
        user_id=int(raw["user_id"]),
        tenant=tenant,
        database=database,
        odoo_uid=odoo_uid,
        telegram_user_id=int(raw.get("telegram_user_id") or 0),
        chat_id=int(raw.get("chat_id") or 0),
        name=str(raw.get("name") or "Chưa cập nhật"),
        login=str(raw.get("login") or "Chưa cập nhật"),
        timezone=str(tz),
        telegram_binding_id=raw.get("telegram_binding_id"),
        job_title=raw.get("job_title"),
        department=raw.get("department"),
        manager_name=raw.get("manager_name"),
        permissions=perms,
        authorization_checked_at=raw.get("authorization_checked_at"),
    )
