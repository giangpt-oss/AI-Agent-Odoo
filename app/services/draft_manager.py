import logging
from typing import Any, Dict, List, Optional
from threading import Lock
from app.domain.task_models import DraftItem, DraftSession

logger = logging.getLogger(__name__)


class DraftManager:
    """Quản lý các bản nháp trung gian (Email, JD, Báo cáo) hỗ trợ xem trước và tinh chỉnh đa lượt."""

    def __init__(self):
        self._sessions: Dict[str, DraftSession] = {}
        self._lock = Lock()

    def get_session(self, user_id: str) -> Optional[DraftSession]:
        with self._lock:
            return self._sessions.get(str(user_id))

    def create_or_replace_session(self, user_id: str, items: List[DraftItem]) -> DraftSession:
        with self._lock:
            session = DraftSession(user_id=str(user_id), items=items)
            self._sessions[str(user_id)] = session
            return session

    def update_item_content(self, user_id: str, item_index: int, new_content: str, note: str = "") -> bool:
        with self._lock:
            session = self._sessions.get(str(user_id))
            if not session:
                return False
            return session.update_item_content(item_index, new_content, note)

    def refine_draft_with_instructions(
        self,
        user_id: str,
        item_index: int,
        instruction: str,
        ai_client: Any = None
    ) -> Optional[DraftItem]:
        """Chỉnh sửa một bản nháp cụ thể theo chỉ dẫn người dùng (ví dụ: 'thân thiện hơn', 'ngắn gọn hơn')."""
        session = self.get_session(user_id)
        if not session or not (0 <= item_index < len(session.items)):
            return None

        target_item = session.items[item_index]
        original_body = target_item.content

        # Nếu có AI client, sử dụng prompt để viết lại
        if ai_client and hasattr(ai_client, "models"):
            try:
                prompt = (
                    f"Bạn là trợ lý doanh nghiệp. Hãy viết lại nội dung sau đây theo yêu cầu: '{instruction}'.\n"
                    f"Chỉ trả về nội dung mới đã chỉnh sửa, không kèm lời giải thích thừa.\n\n"
                    f"--- NỘI DUNG GỐC ---\n{original_body}"
                )
                from google.genai import types
                resp = ai_client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=prompt
                )
                if resp and resp.text:
                    new_body = resp.text.strip()
                    self.update_item_content(user_id, item_index, new_body, note=instruction)
                    return target_item
            except Exception as e:
                logger.warning("Không thể gọi LLM để refine draft, sử dụng fallback: %s", e)

        # Fallback tinh chỉnh nội dung nếu không gọi được API
        friendliness_prefix = "Dạ chào anh/chị, em rất vui được liên hệ lại với anh/chị ạ!\n\n"
        if "thân thiện" in instruction.lower() or "friendly" in instruction.lower():
            if not original_body.startswith("Dạ chào"):
                new_body = f"{friendliness_prefix}{original_body}\n\nChúc anh/chị một ngày làm việc thật nhiều năng lượng!"
            else:
                new_body = f"{original_body}\n\n(Rất mong sớm có dịp hợp tác cùng anh/chị ạ!)"
        elif "ngắn gọn" in instruction.lower() or "short" in instruction.lower():
            new_body = original_body[:200] + "\n\nTrân trọng cảm ơn!"
        else:
            new_body = f"{original_body}\n\n[Ghi chú chỉnh sửa: {instruction}]"

        self.update_item_content(user_id, item_index, new_body, note=instruction)
        return target_item

    def format_drafts_for_review(self, session: DraftSession) -> str:
        """Hiển thị toàn bộ danh sách bản nháp để người dùng đánh giá và chỉ định sửa đổi."""
        if not session.items:
            return "Không có bản nháp nào trong phiên hiện tại."

        lines = [
            f"📋 **DANH SÁCH BẢN NHÁP ({len(session.items)} mục đang chờ duyệt)**",
            "────────────────────────────"
        ]
        for idx, item in enumerate(session.items, start=1):
            recipient_info = f"gửi `{item.recipient}`" if item.recipient else ""
            subject_info = f"Tiêu đề: *{item.subject}*" if item.subject else ""
            version_tag = f"(v{item.version})" if item.version > 1 else ""
            lines.append(f"**[{idx}] {item.title or f'Bản nháp số {idx}'}** {recipient_info} {version_tag}")
            if subject_info:
                lines.append(f"• {subject_info}")
            # Hiển thị nội dung
            lines.append("```text")
            lines.append(item.content.strip())
            lines.append("```")
            lines.append("")

        lines.append("💡 **Bạn có thể:**")
        lines.append("• Ra lệnh chỉnh sửa cụ thể: *'Sửa email số 2 thân thiện hơn'* hoặc *'Làm email số 1 ngắn gọn lại'*")
        lines.append("• Duyệt thực hiện: *'Gửi đi'* hoặc *'Xác nhận gửi toàn bộ'*")
        return "\n".join(lines)


draft_manager = DraftManager()
