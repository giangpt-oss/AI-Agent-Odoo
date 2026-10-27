import asyncio
import json
import logging
import re
from typing import Any, Optional

from app.core.config import get_settings
from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import (
    DraftPayload,
    IntentAnalysisResult,
    IntentType,
)
from app.domain.tool_registry import ToolRegistry, global_tool_registry

logger = logging.getLogger(__name__)


class NaturalPipeline:
    """Pipeline phân tích ý định và định tuyến hội thoại thống nhất.
    
    Quy trình:
    1. Fast-path: Khớp trực tiếp slash commands, chào hỏi và hỏi tính năng trong < 50ms.
    2. LLM Dispatcher: Sử dụng Gemini ép chuẩn JSON Schema (IntentAnalysisResult) với timeout 6s.
    """

    def __init__(self, tool_registry: Optional[ToolRegistry] = None) -> None:
        self.registry = tool_registry or global_tool_registry

    async def analyze_input(
        self,
        text: str,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]] = None,
    ) -> IntentAnalysisResult:
        clean_text = (text or "").strip()
        lower = clean_text.lower()

        # 1. Fast-path: Slash Commands
        if clean_text.startswith("/"):
            tokens = clean_text.split(maxsplit=2)
            base_cmd = tokens[0].lower().split("@")[0]

            if base_cmd == "/today":
                return IntentAnalysisResult(
                    intent_type=IntentType.READ,
                    tool_name="briefing.today",
                )
            if base_cmd == "/task":
                sub_cmd = tokens[1].lower() if len(tokens) > 1 else "list"
                if sub_cmd == "list":
                    return IntentAnalysisResult(
                        intent_type=IntentType.READ,
                        tool_name="tasks.personal.list",
                    )
                if sub_cmd == "done" and len(tokens) > 2:
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="tasks.personal.done",
                        parameters={"task_id": tokens[2].strip()},
                    )
                if sub_cmd == "create" and len(tokens) > 2:
                    parts = [p.strip() for p in tokens[2].split("|")]
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="tasks.personal.create",
                        parameters={
                            "title": parts[0],
                            "due_date": parts[1] if len(parts) > 1 else None,
                            "priority": parts[2] if len(parts) > 2 else "medium",
                        },
                    )

            if base_cmd == "/crm":
                sub_cmd = tokens[1].lower() if len(tokens) > 1 else "search"
                if sub_cmd == "search":
                    query = tokens[2].strip() if len(tokens) > 2 else ""
                    return IntentAnalysisResult(
                        intent_type=IntentType.READ,
                        tool_name="crm.leads.search",
                        parameters={"query": query},
                    )

            if base_cmd in {"/help", "/start"}:
                return IntentAnalysisResult(
                    intent_type=IntentType.REPLY,
                    reply_text=self.registry.generate_capability_overview(list(context.permissions)),
                )

        # 2. Fast-path: Chào hỏi phổ biến
        greetings = ["hello", "hi", "xin chào", "chào bạn", "chào bot", "chào em", "chào", "alo", "hey", "hello bot", "hi bot"]
        if lower in greetings or any(lower.startswith(g + " ") for g in greetings) or any(g in lower for g in ["xin chào", "chào bot", "chào bạn"]):
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text=(
                    "👋 Xin chào! Tôi có thể hỗ trợ gì cho bạn hôm nay?\n\n"
                    "Bạn có thể nhắn tin tự nhiên hoặc dùng các gợi ý nhanh:\n"
                    "• 📅 Điểm tin hàng ngày: Gửi /today hoặc hỏi \"Hôm nay tôi có gì?\"\n"
                    "• 👤 Thông tin tài khoản: Hỏi \"Tôi là ai?\" hoặc \"Hồ sơ của tôi\"\n"
                    "• 📆 Lịch họp & Email: Hỏi \"Xem lịch\" hoặc \"Thư chưa đọc\"\n"
                    "• 💼 Odoo CRM: Gửi /crm search <từ khóa> hoặc \"Tìm khách hàng\"\n"
                    "• 📋 Công việc cá nhân: Gửi /task list hoặc /task create <nội dung>\n\n"
                    "💡 Các thao tác ghi dữ liệu luôn có bản xem trước để bạn xác nhận an toàn."
                ),
            )

        # 3. Fast-path: Chặn thao tác ghi trực tiếp/chưa được hỗ trợ qua Gateway
        unsupported_writes = [
            "soạn email", "gửi email", "gửi thư", "tạo cuộc họp",
            "xóa khách hàng", "xóa database", "tạo cơ hội", "tạo crm", "tạo lead"
        ]
        if any(w in lower for w in unsupported_writes):
            return IntentAnalysisResult(
                intent_type=IntentType.UNSUPPORTED,
                reply_text=(
                    "⚠️ Yêu cầu thao tác ghi tự do chưa được hỗ trợ qua Gateway.\n"
                    "Để đảm bảo an toàn dữ liệu, các thao tác ghi (tạo lịch, tạo task) "
                    "cần tuân thủ cú pháp có xác nhận 2 bước hoặc qua giao diện chuyên dụng."
                ),
            )

        # 4. Fast-path: Hỏi khả năng & menu hướng dẫn
        capability_phrases = [
            "bạn có thể làm gì",
            "bạn giúp tôi được việc gì",
            "bạn giúp được gì",
            "hướng dẫn tôi dùng bot",
            "hướng dẫn sử dụng",
            "chức năng của bot",
            "chức năng",
            "menu tính năng",
            "tính năng",
            "khả năng của bạn",
            "bot làm được gì",
        ]
        if any(phrase in lower for phrase in capability_phrases):
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text=self.registry.generate_capability_overview(list(context.permissions)),
            )

        # 5. Fast-path: Danh tính
        whoami_phrases = ["tôi là ai", "hồ sơ của tôi", "thông tin tài khoản", "hồ sơ cá nhân", "whoami", "thông tin của tôi", "tài khoản của tôi"]
        if any(p in lower for p in whoami_phrases):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="identity.whoami",
            )

        # 6. Fast-path: Trạng thái kết nối
        conn_phrases = ["trạng thái kết nối", "kiểm tra kết nối", "kết nối dịch vụ", "tình trạng kết nối", "kết nối odoo", "kết nối google", "connection status"]
        if any(p in lower for p in conn_phrases):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="connections.status",
            )

        # 7. Fast-path: Điểm tin ngày mới
        briefing_phrases = ["hôm nay tôi có gì", "hôm nay tôi cần làm gì", "hôm nay có gì", "lịch hôm nay", "công việc hôm nay", "điểm tin"]
        if any(p in lower for p in briefing_phrases):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="briefing.today",
            )

        # 8. Fast-path: Tra cứu danh sách công việc cá nhân
        task_list_phrases = ["danh sách công việc", "công việc của tôi", "việc của tôi", "xem công việc", "nhiệm vụ của tôi", "danh sách task"]
        if any(p in lower for p in task_list_phrases):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="tasks.personal.list",
            )

        # 9. Fast-path: Tra cứu Lịch Google
        cal_list_phrases = ["xem lịch", "lịch họp", "lịch của tôi", "sự kiện của tôi"]
        if any(p in lower for p in cal_list_phrases):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="calendar.events.list",
            )

        # 10. Fast-path: Tra cứu Thư Gmail chưa đọc
        mail_list_phrases = ["thư chưa đọc", "email chưa đọc", "kiểm tra thư", "xem email", "hòm thư"]
        if any(p in lower for p in mail_list_phrases):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="gmail.messages.unread",
            )

        # 11. LLM Dispatcher cho các câu hội thoại tự nhiên phức tạp
        return await self._dispatch_with_llm(clean_text, context, session_context)

    async def _call_gemini_raw(self, prompt: str) -> str:
        """Thực hiện gọi Google Gemini với timeout tối đa 6 giây."""
        settings = get_settings()
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        def _sync_call() -> str:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=settings.DEFAULT_LLM_MODEL or "gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            return resp.text or ""

        # Tránh chặn event loop bằng cách chạy trong threadpool với timeout 6s
        return await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=6.0)

    async def _dispatch_with_llm(
        self,
        text: str,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]] = None,
    ) -> IntentAnalysisResult:
        available_tools = self.registry.list_tools(list(context.permissions))
        tools_summary = [
            f"- {t.name} ({t.action_type}): {t.description}. Schema: {json.dumps(t.parameter_schema)}"
            for t in available_tools
        ]

        system_instruction = (
            "Bạn là bộ phân tích ý định cho Trợ lý Doanh nghiệp kết nối Odoo & Google Workspace.\n"
            "Hãy phân tích yêu cầu của người dùng và trả về DUY NHẤT một chuỗi JSON hợp lệ theo cấu trúc:\n"
            "{\n"
            '  "intent_type": "reply" | "read" | "draft" | "prepare_write" | "clarify" | "unsupported",\n'
            '  "tool_name": "<tên tool nếu intent là read hoặc prepare_write, ngược lại để null>",\n'
            '  "parameters": { ...các tham số trích xuất được... },\n'
            '  "reply_text": "<câu trả lời nếu intent là reply hoặc unsupported>",\n'
            '  "clarification_prompt": "<câu hỏi lại nếu intent là clarify>",\n'
            '  "candidate_options": [ ...danh sách lựa chọn nếu clarify... ]\n'
            "}\n\n"
            f"Danh sách công cụ khả dụng:\n" + "\n".join(tools_summary)
        )

        user_prompt = f"{system_instruction}\n\nYêu cầu của người dùng: \"{text}\"\nMúi giờ: {context.timezone}\nPhản hồi JSON:"

        try:
            raw_json = await self._call_gemini_raw(user_prompt)
            # Làm sạch markdown json nếu có
            cleaned = raw_json.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            data = json.loads(cleaned)
            return IntentAnalysisResult.model_validate(data)

        except Exception as e:
            logger.error("LLM dispatch error: %s", e)
            from app.services.error_handler import classify_exception
            _, err_msg = classify_exception(e)
            return IntentAnalysisResult(
                intent_type=IntentType.UNSUPPORTED,
                reply_text=err_msg,
            )


natural_pipeline = NaturalPipeline()
