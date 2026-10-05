import uuid
from abc import ABC, abstractmethod
from typing import Any, Type
from pydantic import BaseModel, Field


class ToolContext(BaseModel):
    """Ngữ cảnh thực thi của Tool, chứa thông tin nhân viên và request trace."""
    employee_id: str
    telegram_chat_id: int
    roles: list[str] = Field(default_factory=list)
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ToolResult(BaseModel):
    """Kết quả trả về thống nhất từ mọi Tool."""
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseTool(ABC):
    """Lớp cơ sở cho toàn bộ các Tools trong hệ thống.
    Đảm bảo:
    - Input được validate bằng Pydantic args_schema.
    - Định nghĩa rõ các role được phép thực thi.
    - Khai báo cờ is_write_action để áp dụng quy tắc kiểm soát rủi ro.
    """
    name: str
    description: str
    args_schema: Type[BaseModel]
    required_roles: list[str] = []
    is_write_action: bool = False

    def validate_args(self, **kwargs) -> BaseModel:
        """Validate tham số đầu vào bằng Pydantic schema."""
        return self.args_schema(**kwargs)

    @abstractmethod
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Logic thực thi công cụ. Tool sẽ gọi Connector, không gọi trực tiếp API."""
        pass
