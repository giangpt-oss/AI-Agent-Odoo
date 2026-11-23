from pydantic import BaseModel, Field
from typing import Any
from app.tools.base import BaseTool, ToolContext, ToolResult
from app.services.gateway_client import gateway_client


class GetUserConnectionsInput(BaseModel):
    user_id: int = Field(description="ID người dùng cần lấy thông tin kết nối")


class GetUserConnectionsTool(BaseTool):
    name = "get_user_connections"
    description = "Lấy danh sách kết nối Google, Telegram, Odoo của người dùng. Trả về tài khoản đang dùng (active) và các tài khoản khác."
    args_schema = GetUserConnectionsInput
    required_roles = ["employee", "sales_user", "admin"]
    is_write_action = False

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        validated = self.validate_args(**kwargs)
        user_id = kwargs.get("user_id") or context.employee_id
        if not user_id:
            return ToolResult(success=False, error="Thiếu user_id")

        try:
            resp = await gateway_client.get_user_connections(user_id)

            if resp.get("status") == "ERROR":
                return ToolResult(success=False, error=resp.get("message", "Lỗi Gateway"))

            # Xử lý response: lọc tài khoản active
            accounts = resp.get("google", {}).get("accounts", []) if "google" in resp else resp.get("accounts", [])
            active_accounts = [acc for acc in accounts if acc.get("is_active") or acc.get("status") == "connected"]
            inactive_accounts = [acc for acc in accounts if not acc.get("is_active") and acc.get("status") != "connected"]

            if not accounts:
                return ToolResult(
                    success=True,
                    data="Bạn chưa liên kết tài khoản Google nào.",
                    metadata={"accounts": []}
                )

            active_email = None
            for acc in active_accounts:
                email = acc.get("email") or acc.get("emailAddress")
                if email:
                    active_email = email
                    break

            response_lines = []
            if active_email:
                response_lines.append(f"Hiện tại bạn đang dùng Gmail: {active_email} (active).")
            else:
                response_lines.append("Hiện tại chưa có tài khoản Gmail nào đang active.")

            other_accounts = [acc.get("email") or acc.get("emailAddress") for acc in inactive_accounts if acc.get("email") or acc.get("emailAddress")]
            if other_accounts:
                response_lines.append(f"Các tài khoản khác ({len(other_accounts)}): {', '.join(other_accounts)}")

            response_text = "\n".join(response_lines)

            return ToolResult(
                success=True,
                data=response_text,
                metadata={"accounts": accounts, "active": active_email}
            )

        except Exception as e:
            return ToolResult(success=False, error=f"Lỗi khi lấy kết nối: {str(e)}")