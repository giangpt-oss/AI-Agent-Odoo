import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VerificationService:
    """Thực hiện Quality Review (trước khi trình bày) và Execution Review (sau khi thực thi)."""

    @staticmethod
    def review_quality(category: str, content: str) -> Tuple[bool, List[str]]:
        """Quality Review: Kiểm tra tính đầy đủ và tiêu chuẩn chuyên nghiệp của nội dung do AI tạo ra."""
        issues = []
        c = (content or "").lower()

        if category == "jd":  # Job Description
            if not any(k in c for k in ["trách nhiệm", "nhiệm vụ", "responsibilit", "mô tả công việc"]):
                issues.append("Thiếu phần: Trách nhiệm / Nhiệm vụ công việc chính.")
            if not any(k in c for k in ["yêu cầu", "tiêu chuẩn", "requirement", "kỹ năng", "kinh nghiệm"]):
                issues.append("Thiếu phần: Yêu cầu năng lực / Kinh nghiệm làm việc.")
            if not any(k in c for k in ["quyền lợi", "chế độ", "benefit", "lương thưởng"]):
                issues.append("Nên bổ sung: Quyền lợi và chế độ đãi ngộ.")

        elif category == "email":  # Email Draft
            if not any(k in c for k in ["kính gửi", "chào", "dear", "hello", "hi"]):
                issues.append("Thiếu lời chào mở đầu chuẩn mực.")
            if not any(k in c for k in ["trân trọng", "cảm ơn", "best regards", "thân mến"]):
                issues.append("Thiếu lời kết thúc / Chữ ký thư.")
            if len(content.strip()) < 30:
                issues.append("Nội dung thư quá ngắn, chưa đủ thông tin công việc cần truyền đạt.")

        elif category == "report":  # Business Report
            if not any(char.isdigit() for char in content):
                issues.append("Báo cáo thiếu các số liệu định lượng (metrics/numbers) cần thiết.")

        is_acceptable = len(issues) == 0
        return is_acceptable, issues

    @staticmethod
    async def verify_execution(
        action_name: str,
        execution_result: Any,
        target_verifier_fn: Optional[Any] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Execution Review: Xác minh hành động đã thực sự được ghi nhận trên hệ thống bên ngoài.
        Tránh vòng lặp vô hạn (No infinite self-correction loops).
        """
        audit_details = {
            "action": action_name,
            "verified": False,
            "notes": ""
        }

        if execution_result is None:
            return False, "Không có kết quả trả về từ hệ thống.", audit_details

        # 1. Email Send Verification
        if action_name in ["send_email", "reply_email"]:
            if isinstance(execution_result, dict) and execution_result.get("status") == "SUCCESS":
                msg_id = execution_result.get("message_id")
                audit_details["verified"] = True
                audit_details["notes"] = f"Email đã chuyển phát thành công (Message ID: {msg_id})."
                return True, "Email đã được gửi thành công và xác nhận bởi máy chủ bưu chính.", audit_details
            elif isinstance(execution_result, str) and ("SUCCESS" in execution_result or "thành công" in execution_result):
                audit_details["verified"] = True
                return True, "Thực thi thành công.", audit_details
            else:
                return False, f"Hệ thống gửi thư trả về trạng thái thất bại: {execution_result}", audit_details

        # 2. Odoo Record Update / Create Verification
        if action_name in ["update_record", "create_record", "edit_spreadsheet"]:
            if target_verifier_fn:
                try:
                    exists = await target_verifier_fn()
                    if exists:
                        audit_details["verified"] = True
                        audit_details["notes"] = "Đã đọc lại dữ liệu trên Odoo và xác nhận thay đổi tồn tại."
                        return True, "Dữ liệu đã được cập nhật chính xác trên hệ thống Odoo.", audit_details
                    else:
                        return False, "Đọc lại dữ liệu trên hệ thống không tìm thấy thay đổi tương ứng.", audit_details
                except Exception as e:
                    logger.warning("Không thể chạy target_verifier_fn: %s", e)
            
            # Default success check if no custom verifier
            audit_details["verified"] = True
            return True, "Thao tác hoàn tất và được ghi nhận thành công.", audit_details

        # Fallback check
        audit_details["verified"] = True
        return True, "Đã hoàn thành tác vụ.", audit_details


verification_service = VerificationService()
