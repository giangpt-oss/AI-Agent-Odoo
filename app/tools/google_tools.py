from pydantic import BaseModel, Field
from app.tools.base import BaseTool, ToolContext, ToolResult
from app.connectors.google.connector import get_google_connector


class SearchGmailInput(BaseModel):
    query: str = Field(description="Từ khóa tìm kiếm email (người gửi, chủ đề, nội dung)")
    max_results: int = Field(default=5, ge=1, le=20, description="Số lượng email tối đa")


class SearchGmailTool(BaseTool):
    name = "search_gmail"
    description = "Tìm kiếm email trong hộp thư Gmail của nhân viên"
    args_schema = SearchGmailInput
    required_roles = ["employee", "sales_user", "admin"]
    is_write_action = False

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        validated = self.validate_args(**kwargs)
        connector = get_google_connector()

        # Trong ngữ cảnh thực tế, access_token sẽ được lấy từ bảng oauth_tokens dựa vào context.employee_id
        # Nếu chưa liên kết tài khoản Google:
        # Ở đây ta giả định token hoặc mock để đảm bảo tool chạy an toàn
        mock_token = "mock_access_token_for_test"

        try:
            # Nếu chạy test mock hoặc khi có token thực
            messages = await connector.search_gmail_messages(
                access_token=mock_token,
                query=validated.query,
                max_results=validated.max_results,
            )
            return ToolResult(
                success=True,
                data=messages,
                metadata={"source": "google_gmail", "count": len(messages)},
            )
        except Exception as e:
            # Fallback thông báo thân thiện nếu chưa có token hoặc lỗi API
            return ToolResult(
                success=False,
                error=f"Chưa thể truy cập Gmail (Cần liên kết OAuth hoặc lỗi kết nối): {str(e)}",
                metadata={"source": "google_gmail", "requires_auth": True},
            )
