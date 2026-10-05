from abc import ABC, abstractmethod
from typing import Any


class BaseERPConnector(ABC):
    """Giao diện trừu tượng cho hệ thống ERP.
    LangGraph Tools chỉ tương tác với Interface này, không phụ thuộc vào chi tiết Odoo.
    """

    @abstractmethod
    async def authenticate(self) -> bool:
        """Xác thực kết nối với ERP."""
        pass

    @abstractmethod
    async def search_read(
        self,
        model: str,
        domain: list[list[Any]] | None = None,
        fields: list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        """Truy vấn danh sách bản ghi theo điều kiện."""
        pass

    @abstractmethod
    async def create_record(self, model: str, values: dict[str, Any]) -> int:
        """Tạo bản ghi mới, trả về ID."""
        pass

    @abstractmethod
    async def update_record(self, model: str, record_id: int, values: dict[str, Any]) -> bool:
        """Cập nhật bản ghi theo ID."""
        pass

    @abstractmethod
    async def call_model_method(
        self,
        model: str,
        method: str,
        record_ids: list[int],
        *args,
        **kwargs,
    ) -> Any:
        """Gọi một hàm nghiệp vụ trên Model (ví dụ: action_confirm, button_validate)."""
        pass
