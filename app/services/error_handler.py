from enum import Enum
import asyncio
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    CONFIG_MISSING = "CONFIG_MISSING"
    INVALID_MODEL = "INVALID_MODEL"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_ERROR = "LLM_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    DATA_SOURCE_ERROR = "DATA_SOURCE_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    TURN_TIMEOUT = "TURN_TIMEOUT"


ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.CONFIG_MISSING: (
        "⚙️ Tính năng xử lý ngôn ngữ nâng cao chưa hoàn tất cấu hình (Thiếu API Key). "
        "Vui lòng liên hệ người quản lý hệ thống để hoàn tất cấu hình."
    ),
    ErrorCode.INVALID_MODEL: (
        "⚙️ Mô hình AI được cấu hình hiện không khả dụng. "
        "Vui lòng liên hệ quản trị viên để cập nhật phiên bản mô hình phù hợp."
    ),
    ErrorCode.LLM_TIMEOUT: (
        "Yêu cầu phân tích đang phản hồi quá thời gian quy định. "
        "Thông tin đã lưu được giữ nguyên. Bạn có thể thử lại khi dịch vụ hoạt động."
    ),
    ErrorCode.LLM_ERROR: (
        "Chưa phân tích được yêu cầu do lỗi phản hồi từ dịch vụ AI. "
        "Thông tin đã lưu được giữ nguyên; tôi chưa tự thực hiện yêu cầu này."
    ),
    ErrorCode.RATE_LIMIT: (
        "🚦 Hệ thống đang nhận được quá nhiều yêu cầu cùng lúc (Rate limit). "
        "Vui lòng đợi vài giây và thử gửi lại yêu cầu."
    ),
    ErrorCode.DATA_SOURCE_ERROR: (
        "🔌 Không thể kết nối hoặc truy vấn dữ liệu từ Odoo / Google Workspace. "
        "Vui lòng kiểm tra lại trạng thái dịch vụ bằng lệnh 'Kiểm tra kết nối'."
    ),
    ErrorCode.PERMISSION_DENIED: (
        "🔒 Tài khoản của bạn chưa được cấp quyền thực hiện tác vụ này. "
        "Vui lòng liên hệ quản trị viên hoặc kiểm tra quyền trên Cổng Gateway."
    ),
    ErrorCode.OUT_OF_SCOPE: (
        "🧭 Không thể xử lý yêu cầu hoặc yêu cầu nằm ngoài phạm vi hỗ trợ của Trợ lý Doanh nghiệp. "
        "Bạn có thể mô tả rõ kết quả muốn nhận và dữ liệu cần sử dụng."
    ),
    ErrorCode.TURN_TIMEOUT: (
        "⏱️ Tổng thời gian xử lý yêu cầu vượt quá giới hạn an toàn (18 giây). "
        "Thông tin đã lưu được giữ nguyên. Nếu đã xác nhận thao tác ghi, hãy hỏi trạng thái để đối soát trước khi yêu cầu tạo lại."
    ),
}


def classify_exception(exc: Exception) -> Tuple[ErrorCode, str]:
    """Phân loại lỗi kỹ thuật thành mã lỗi chuẩn và thông báo thân thiện."""
    err_str = str(exc).lower()

    if ("odoo" in err_str or "google" in err_str or "gateway" in err_str) and ("timeout" in err_str or "connection" in err_str or "503" in err_str or "refused" in err_str):
        return ErrorCode.DATA_SOURCE_ERROR, ERROR_MESSAGES[ErrorCode.DATA_SOURCE_ERROR]

    if isinstance(exc, asyncio.TimeoutError) or any(term in err_str for term in ('timeout', 'deadline_exceeded', '503', '504', 'unavailable')):
        return ErrorCode.LLM_TIMEOUT, ERROR_MESSAGES[ErrorCode.LLM_TIMEOUT]

    if "gemini_api_key" in err_str or "api_key" in err_str or "api key" in err_str or "not configured" in err_str:
        return ErrorCode.CONFIG_MISSING, ERROR_MESSAGES[ErrorCode.CONFIG_MISSING]

    if "model" in err_str and ("not found" in err_str or "invalid" in err_str):
        return ErrorCode.INVALID_MODEL, ERROR_MESSAGES[ErrorCode.INVALID_MODEL]

    if "429" in err_str or "quota" in err_str or "rate limit" in err_str or "resourceexhausted" in err_str:
        if any(term in err_str for term in ('perday', 'per day', 'daily', 'requests_per_day')):
            return ErrorCode.RATE_LIMIT, 'Dịch vụ AI đã hết hạn mức trong ngày. Cần chờ hạn mức được làm mới hoặc bổ sung hạn mức cho khóa AI. Thông tin đã lưu vẫn được giữ; các bước báo giá và báo cáo rõ nghĩa vẫn có thể xử lý trực tiếp.'
        return ErrorCode.RATE_LIMIT, ERROR_MESSAGES[ErrorCode.RATE_LIMIT]

    if "permission" in err_str or "unauthorized" in err_str or "forbidden" in err_str or "403" in err_str:
        return ErrorCode.PERMISSION_DENIED, ERROR_MESSAGES[ErrorCode.PERMISSION_DENIED]

    if "odoo" in err_str or "google" in err_str or "connection" in err_str or "gateway" in err_str:
        return ErrorCode.DATA_SOURCE_ERROR, ERROR_MESSAGES[ErrorCode.DATA_SOURCE_ERROR]

    return ErrorCode.LLM_ERROR, ERROR_MESSAGES[ErrorCode.LLM_ERROR]


def format_error_message(code: ErrorCode, custom_message: str | None = None) -> str:
    """Trả về thông báo lỗi chuẩn hóa có chỉ dẫn hành động rõ ràng."""
    return custom_message or ERROR_MESSAGES.get(code, "⚠️ Đã có lỗi xảy ra. Vui lòng thử lại sau.")


get_user_friendly_error = format_error_message
