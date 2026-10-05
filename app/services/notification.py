import logging
from typing import Any
from app.connectors.telegram.client import get_telegram_connector
from app.services.employee import DEV_EMPLOYEES_STORE

logger = logging.getLogger(__name__)


class NotificationService:
    """Quản lý việc gửi thông báo chủ động (Passive Notification) và Broadcast qua Telegram."""

    def __init__(self):
        self.telegram = get_telegram_connector()

    async def send_to_employee(self, chat_id: int, message: str) -> bool:
        """Gửi thông báo trực tiếp 1-1 tới một nhân viên."""
        return await self.telegram.send_message(chat_id=chat_id, text=message)

    async def broadcast_to_roles(self, target_roles: list[str], message: str) -> dict[str, Any]:
        """Gửi thông báo broadcast tới tất cả nhân viên có vai trò nằm trong target_roles."""
        sent_count = 0
        failed_count = 0
        target_roles_set = set(target_roles)

        # Lấy danh sách nhân viên sở hữu roles mục tiêu
        for chat_id, emp in DEV_EMPLOYEES_STORE.items():
            emp_roles = set(emp.get("roles", []))
            if emp_roles.intersection(target_roles_set):
                success = await self.telegram.send_message(
                    chat_id=chat_id,
                    text=f"📢 [THÔNG BÁO HỆ THỐNG]\n\n{message}"
                )
                if success:
                    sent_count += 1
                else:
                    failed_count += 1

        return {
            "total_sent": sent_count,
            "total_failed": failed_count,
            "target_roles": target_roles,
        }

    async def handle_odoo_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        """Tiếp nhận sự kiện từ Odoo Cloud Automated Action và thông báo cho người liên quan."""
        record_name = payload.get("record_name", "Không rõ")
        assigned_chat_id = payload.get("assigned_chat_id")

        if event_type == "sale_order_confirmed":
            amount = payload.get("amount", 0)
            msg = (
                f"🎉 **Đơn hàng đã được xác nhận!**\n\n"
                f"- Mã đơn: `{record_name}`\n"
                f"- Giá trị: `{amount:,.0f} VNĐ`\n"
                f"Vui lòng kiểm tra tiến độ xuất kho."
            )
        elif event_type == "invoice_paid":
            msg = f"💰 **Hóa đơn `{record_name}` đã hoàn tất thanh toán.**"
        else:
            msg = f"ℹ️ **Sự kiện Odoo `{event_type}`:** Bản ghi `{record_name}` vừa được cập nhật."

        if assigned_chat_id:
            return await self.send_to_employee(assigned_chat_id, msg)
        else:
            # Mặc định gửi cho quản lý bán hàng
            await self.broadcast_to_roles(["sales_manager"], msg)
            return True


notification_service = NotificationService()
