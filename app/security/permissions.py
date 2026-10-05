import logging
from enum import Enum
from typing import NamedTuple

logger = logging.getLogger(__name__)


class PermissionType(str, Enum):
    # Odoo Sales
    SALES_READ = "sales.order.read"
    SALES_WRITE = "sales.order.write"
    # Odoo Invoicing / Accounting
    ACCOUNT_READ = "account.move.read"
    ACCOUNT_WRITE = "account.move.write"
    # Google Workspace
    MAIL_READ = "mail.read"
    MAIL_SEND = "mail.send"
    CALENDAR_READ = "calendar.read"
    CALENDAR_WRITE = "calendar.write"
    # System
    SYSTEM_ADMIN = "*"


# Bảng ánh xạ Role nhân viên -> Danh sách Quyền cụ thể
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {PermissionType.SYSTEM_ADMIN},
    "sales_manager": {
        PermissionType.SALES_READ,
        PermissionType.SALES_WRITE,
        PermissionType.ACCOUNT_READ,
    },
    "sales_user": {
        PermissionType.SALES_READ,
    },
    "sales_write": {
        PermissionType.SALES_READ,
        PermissionType.SALES_WRITE,
    },
    "warehouse_user": {
        # Chỉ xem kho, không có quyền bán hàng hay kế toán
        "inventory.read",
    },
    "employee": {
        PermissionType.MAIL_READ,
        PermissionType.CALENDAR_READ,
    },
}

# Bảng ánh xạ Tool/Action -> Quyền bắt buộc
TOOL_REQUIRED_PERMISSIONS: dict[str, str] = {
    "get_sales_orders": PermissionType.SALES_READ,
    "create_sales_order": PermissionType.SALES_WRITE,
    "get_invoices": PermissionType.ACCOUNT_READ,
    "search_gmail": PermissionType.MAIL_READ,
}


class PermissionDecision(NamedTuple):
    allowed: bool
    reason: str | None
    is_write: bool


class PermissionEngine:
    """Hạt nhân kiểm soát phân quyền Layer 1 trước khi bất kỳ Tool nào được gọi."""

    @staticmethod
    def evaluate(tool_name: str, user_roles: list[str]) -> PermissionDecision:
        """Đánh giá xem tập hợp roles của nhân viên có đủ quyền thực thi tool_name hay không."""
        # Chat thông thường không cần quyền
        if not tool_name or tool_name == "general_chat":
            return PermissionDecision(allowed=True, reason=None, is_write=False)

        required_perm = TOOL_REQUIRED_PERMISSIONS.get(tool_name)
        if not required_perm:
            # Nếu tool chưa được định nghĩa phân quyền, mặc định từ chối để an toàn
            return PermissionDecision(
                allowed=False,
                reason=f"[Tự động từ chối] Công cụ '{tool_name}' chưa được cấp phép trong chính sách hệ thống.",
                is_write=False,
            )

        is_write = "write" in required_perm or "send" in required_perm

        # Gom tất cả permissions mà user sở hữu từ các roles
        user_perms: set[str] = set()
        for role in user_roles:
            user_perms.update(ROLE_PERMISSIONS.get(role, set()))

        # Admin có toàn quyền
        if PermissionType.SYSTEM_ADMIN in user_perms or "*" in user_perms:
            return PermissionDecision(allowed=True, reason=None, is_write=is_write)

        # Kiểm tra quyền cụ thể
        if required_perm in user_perms:
            return PermissionDecision(allowed=True, reason=None, is_write=is_write)

        req_val = required_perm.value if hasattr(required_perm, "value") else str(required_perm)
        user_vals = [p.value if hasattr(p, "value") else str(p) for p in user_perms]
        reason = (
            f"❌ [Tự động từ chối] Hành động '{tool_name}' yêu cầu quyền '{req_val}'. "
            f"Tài khoản của bạn chỉ sở hữu các quyền: {sorted(user_vals) or 'Không có'}."
        )
        return PermissionDecision(allowed=False, reason=reason, is_write=is_write)


permission_engine = PermissionEngine()
