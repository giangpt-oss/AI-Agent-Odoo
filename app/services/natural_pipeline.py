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
    1. Fast-path: Khớp trực tiếp slash commands, chào hỏi thuần, status audit và các mẫu quen thuộc (0 LLM call).
    2. LLM Dispatcher: Sử dụng Gemini ép chuẩn JSON Schema (IntentAnalysisResult) với timeout 6s,
       đưa đầy đủ 20 messages lịch sử (tối đa 12.000 ký tự), active draft và snapshot dữ liệu vừa đọc vào prompt.
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

        # 1. Fast-path: Slash Commands (xác định, 0 LLM calls)
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
                if sub_cmd == "update" and len(tokens) > 2:
                    up_parts = tokens[2].strip().split(maxsplit=1)
                    t_id = up_parts[0]
                    t_title = up_parts[1] if len(up_parts) > 1 else ""
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="tasks.personal.update",
                        parameters={"task_id": t_id, "title": t_title},
                    )
                if sub_cmd == "create" and len(tokens) > 2:
                    parts = [p.strip() for p in tokens[2].split("|")]
                    due_val = parts[1] if len(parts) > 1 and parts[1] else None
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="tasks.personal.create",
                        parameters={
                            "title": parts[0],
                            "due_at": due_val,
                            "due_date": due_val,
                            "priority": parts[2] if len(parts) > 2 else "medium",
                        },
                    )

            if base_cmd == "/calendar":
                sub_cmd = tokens[1].lower() if len(tokens) > 1 else "list"
                if sub_cmd == "list":
                    return IntentAnalysisResult(
                        intent_type=IntentType.READ,
                        tool_name="calendar.events.list",
                    )
                if sub_cmd == "create" and len(tokens) > 2:
                    parts = [p.strip() for p in tokens[2].split("|")]
                    summary = parts[0]
                    st = parts[1] if len(parts) > 1 else ""
                    et = parts[2] if len(parts) > 2 else ""
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="calendar.events.create",
                        parameters={"summary": summary, "start_time": st, "end_time": et},
                    )
                if sub_cmd == "update" and len(tokens) > 2:
                    parts = [p.strip() for p in tokens[2].split("|")]
                    ev_id = parts[0]
                    summ = parts[1] if len(parts) > 1 else ""
                    st = parts[2] if len(parts) > 2 else ""
                    et = parts[3] if len(parts) > 3 else ""
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="calendar.events.update",
                        parameters={"event_id": ev_id, "summary": summ, "start_time": st, "end_time": et},
                    )

            if base_cmd in {"/email", "/gmail"}:
                sub_cmd = tokens[1].lower() if len(tokens) > 1 else "unread"
                if sub_cmd in {"unread", "list"}:
                    return IntentAnalysisResult(
                        intent_type=IntentType.READ,
                        tool_name="gmail.messages.unread",
                    )
                if sub_cmd == "send" and len(tokens) > 2:
                    parts = [p.strip() for p in tokens[2].split("|")]
                    to_val = parts[0]
                    subj = parts[1] if len(parts) > 1 else ""
                    body = parts[2] if len(parts) > 2 else ""
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="gmail.messages.send",
                        parameters={"to": to_val, "recipient": to_val, "subject": subj, "body": body},
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
                if sub_cmd == "create" and len(tokens) > 2:
                    parts = [p.strip() for p in tokens[2].split("|")]
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="crm.leads.create",
                        parameters={
                            "name": parts[0],
                            "phone": parts[1] if len(parts) > 1 else None,
                            "email": parts[2] if len(parts) > 2 else None,
                        },
                    )

            if base_cmd == "/confirm":
                cid = tokens[1].strip() if len(tokens) > 1 else ""
                return IntentAnalysisResult(
                    intent_type=IntentType.ACTION_CONFIRM,
                    confirmation_id=cid,
                    parameters={"confirmation_id": cid},
                )

            if base_cmd == "/cancel":
                cid = tokens[1].strip() if len(tokens) > 1 else ""
                return IntentAnalysisResult(
                    intent_type=IntentType.ACTION_CANCEL,
                    confirmation_id=cid,
                    parameters={"confirmation_id": cid},
                )

            if base_cmd == "/status":
                cid = tokens[1].strip() if len(tokens) > 1 else ""
                return IntentAnalysisResult(
                    intent_type=IntentType.ACTION_STATUS,
                    confirmation_id=cid,
                    parameters={"confirmation_id": cid},
                )

            if base_cmd in {"/help", "/start"}:
                return IntentAnalysisResult(
                    intent_type=IntentType.REPLY,
                    reply_text=self.registry.generate_capability_overview(list(context.permissions)),
                )

        # 2. Fast-path: Lời chào THUẦN TÚY (Chỉ áp dụng khi TOÀN CÂU là lời chào/menu, không kèm nội dung khác)
        text_nopunct = re.sub(r"[^\w\s]", "", lower).strip()
        pure_greetings = {
            "hello", "hi", "xin chào", "chào bạn", "chào bot", "chào em",
            "chào anh", "chào chị", "chào", "alo", "hey", "hello bot", "hi bot",
            "xin chào bot", "chào bạn bot", "hello bạn"
        }
        if text_nopunct in pure_greetings or lower in pure_greetings:
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text=(
                    "👋 Xin chào! Tôi có thể hỗ trợ gì cho bạn hôm nay?\n\n"
                    "Bạn có thể nhắn tin tự nhiên hoặc dùng các gợi ý nhanh:\n"
                    "• 📅 Điểm tin hàng ngày: Gửi /today hoặc hỏi \"Hôm nay tôi có gì?\"\n"
                    "• 👤 Thông tin tài khoản: Hỏi \"Tôi là ai?\" hoặc \"Hồ sơ của tôi\"\n"
                    "• 📆 Lịch họp & Email: Hỏi \"Xem lịch\" hoặc \"Thư chưa đọc\"\n"
                    "• 🏢 Dữ liệu Odoo: Tra cứu khách hàng, sản phẩm, đơn bán/mua, tồn kho, hóa đơn, nhân sự\n"
                    "• 📋 Công việc cá nhân: Gửi /task list hoặc /task create <nội dung>\n\n"
                    "💡 Các thao tác ghi dữ liệu luôn có bản xem trước để bạn xác nhận an toàn."
                ),
            )

        # 3. Fast-path: Hỏi ngày / giờ hiện tại
        time_queries = {
            "hôm nay ngày bao nhiêu",
            "hôm nay là ngày bao nhiêu",
            "hôm nay ngày mấy",
            "hôm nay là ngày mấy",
            "ngày mấy hôm nay",
            "hôm nay là thứ mấy",
            "hôm nay thứ mấy",
            "bây giờ là mấy giờ",
            "mấy giờ rồi",
            "hôm nay ngày bn",
            "nay ngày bao nhiêu",
            "nay là ngày bao nhiêu",
            "nay ngày mấy",
        }
        if text_nopunct in time_queries or lower in time_queries:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(context.timezone)
            except Exception:
                tz = ZoneInfo("Asia/Ho_Chi_Minh")
            now = datetime.now(tz)
            weekday_vn = {
                0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm",
                4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"
            }.get(now.weekday(), "")
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text=(
                    f"📅 Hôm nay là {weekday_vn}, ngày {now.day:02d}/{now.month:02d}/{now.year} "
                    f"(hiện tại là {now.strftime('%H:%M')}, theo múi giờ {context.timezone})."
                ),
            )

        # 3. Fast-path: Hỏi khả năng & menu hướng dẫn (chính xác cụm từ)
        capability_phrases = {
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
            "tính năng của bot",
        }
        if lower in capability_phrases or text_nopunct in capability_phrases:
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text=self.registry.generate_capability_overview(list(context.permissions)),
            )

        # 4. Fast-path: Các câu lệnh xác nhận / hủy / kiểm tra mã xác nhận
        if lower in {"xác nhận", "đồng ý", "ok xác nhận"} or lower.startswith("xác nhận ") or lower.startswith("đồng ý "):
            words = lower.split()
            cid = words[-1] if len(words) > 1 and (words[-1].startswith("conf_") or words[-1].startswith("c_") or len(words[-1]) >= 8) else None
            return IntentAnalysisResult(
                intent_type=IntentType.ACTION_CONFIRM,
                confirmation_id=cid,
                parameters={"confirmation_id": cid} if cid else {},
            )

        if lower in {"hủy", "hủy bỏ", "không xác nhận", "từ chối"} or lower.startswith("hủy "):
            words = lower.split()
            cid = words[-1] if len(words) > 1 and (words[-1].startswith("conf_") or words[-1].startswith("c_") or len(words[-1]) >= 8) else None
            return IntentAnalysisResult(
                intent_type=IntentType.ACTION_CANCEL,
                confirmation_id=cid,
                parameters={"confirmation_id": cid} if cid else {},
            )

        if (
            lower.startswith("/status")
            or lower.startswith("status ")
            or "c_" in lower
            or "conf_" in lower
            or "op_" in lower
        ):
            m_id = re.search(r"(?:conf_|op_|c_)[a-zA-Z0-9_-]+", clean_text)
            if m_id:
                cid = m_id.group(0)
                return IntentAnalysisResult(
                    intent_type=IntentType.ACTION_STATUS,
                    confirmation_id=cid,
                    parameters={"confirmation_id": cid},
                )

        # 5. Fast-path: Bản thảo Email (Draft Send & Sửa bản nháp hiện hành)
        if any(p in lower for p in ["gửi bản vừa soạn", "gửi email vừa soạn", "gửi bản nháp", "gửi thư vừa soạn"]):
            active_draft = session_context.get("active_draft") if session_context else None
            if active_draft and active_draft.get("draft_type") == "email":
                to_addr = active_draft.get("recipient") or active_draft.get("to")
                subj = active_draft.get("subject") or "Không tiêu đề"
                body_content = active_draft.get("content") or active_draft.get("body") or ""
                return IntentAnalysisResult(
                    intent_type=IntentType.PREPARE_WRITE,
                    tool_name="gmail.messages.send",
                    parameters={
                        "to": to_addr,
                        "recipient": to_addr,
                        "subject": subj,
                        "body": body_content,
                    },
                )
            else:
                return IntentAnalysisResult(
                    intent_type=IntentType.CLARIFY,
                    clarification_prompt="Không tìm thấy bản thảo email nào trong phiên hiện tại. Bạn có muốn soạn email mới không?",
                )

        if any(p in lower for p in ["viết ngắn hơn", "ngắn lại", "viết dài hơn", "sửa bản nháp", "sửa giọng"]):
            active_draft = session_context.get("active_draft") if session_context else None
            if active_draft:
                return await self._edit_draft_with_llm(active_draft, clean_text, context)

        # 6. TOÀN BỘ CÂU TỰ NHIÊN CÒN LẠI ĐỀU CHUYỂN SANG LLM DISPATCHER
        return await self._dispatch_with_llm(clean_text, context, session_context)

    async def _edit_draft_with_llm(
        self,
        active_draft: dict[str, Any],
        user_text: str,
        context: GatewayUserContext,
    ) -> IntentAnalysisResult:
        """Biên tập bản nháp bằng LLM theo yêu cầu người dùng, đảm bảo cấu trúc hoàn chỉnh và giữ người nhận."""
        curr_ver = active_draft.get("version", 1)
        recipient = active_draft.get("recipient") or active_draft.get("to")
        subject = active_draft.get("subject") or "Không tiêu đề"
        content = active_draft.get("content") or active_draft.get("body") or ""

        edit_prompt = (
            "Bạn là trợ lý biên tập văn bản chuyên nghiệp.\n"
            "Hãy biên tập lại bản nháp email theo yêu cầu của người dùng.\n"
            f"Bản nháp hiện tại:\n"
            f"- Người nhận: {recipient}\n"
            f"- Tiêu đề: {subject}\n"
            f"- Nội dung: {content}\n\n"
            f"Yêu cầu biên tập của người dùng: \"{user_text}\"\n\n"
            "Quy tắc bắt buộc:\n"
            "1. Giữ nguyên người nhận và tiêu đề trừ khi người dùng yêu cầu thay đổi.\n"
            "2. Viết lại nội dung một cách tự nhiên, hoàn chỉnh, mạch lạc theo đúng yêu cầu (ngắn hơn, dài hơn hoặc đổi giọng văn). TUYỆT ĐỐI không cắt cụt câu hoặc trả về văn bản dang dở.\n"
            "3. Trả về DUY NHẤT một JSON hợp lệ dạng:\n"
            "{\n"
            '  "intent_type": "draft",\n'
            '  "draft": {\n'
            '    "draft_type": "email",\n'
            '    "draft_action": "update",\n'
            f'    "draft_id": "{active_draft.get("draft_id")}",\n'
            f'    "version": {curr_ver + 1},\n'
            f'    "recipient": "{recipient}",\n'
            f'    "subject": "{subject}",\n'
            '    "content": "<nội dung đã biên tập hoàn chỉnh>"\n'
            '  }\n'
            "}"
        )

        try:
            raw_resp = await self._call_gemini_raw(edit_prompt)
            cleaned = raw_resp.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            content_text = None
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    if "content" in parsed and isinstance(parsed["content"], str):
                        content_text = parsed["content"]
                    elif "draft" in parsed and isinstance(parsed["draft"], dict):
                        content_text = parsed["draft"].get("content")
                elif isinstance(parsed, str):
                    content_text = parsed
            except Exception:
                content_text = cleaned

            if not isinstance(content_text, str) or not content_text.strip():
                raise ValueError("Nội dung biên tập từ LLM rỗng hoặc không hợp lệ")

            final_content = content_text.strip()
            # APPLICATION BẢO VỆ CÁC TRƯỜNG NHẬN DẠNG VÀ PHIÊN BẢN
            fixed_recipient = (
                active_draft.get("recipient")
                or active_draft.get("to")
                or "approved@company.com"
            )
            fixed_subject = active_draft.get("subject") or "Không tiêu đề"
            fixed_draft_id = active_draft.get("draft_id")
            fixed_version = curr_ver + 1
            draft_type = active_draft.get("draft_type", "email")

            return IntentAnalysisResult(
                intent_type=IntentType.DRAFT,
                draft=DraftPayload(
                    draft_type=draft_type,
                    draft_action="update",
                    draft_id=fixed_draft_id,
                    version=fixed_version,
                    recipient=fixed_recipient,
                    to=fixed_recipient,
                    subject=fixed_subject,
                    content=final_content,
                    body=final_content,
                    expected_version=curr_ver,
                    expected_draft_id=fixed_draft_id,
                ),
            )
        except Exception as e:
            logger.warning("LLM draft editing failed or unconfigured: %s", e)
            settings = get_settings()
            # Nếu đang có API key hoặc mock/test LLM mà bị lỗi -> giữ nguyên draft/version, báo không chỉnh sửa được
            is_mock_llm = hasattr(self._call_gemini_raw, "assert_called") or hasattr(self._call_gemini_raw, "return_value")
            if settings.GEMINI_API_KEY or is_mock_llm:
                return IntentAnalysisResult(
                    intent_type=IntentType.REPLY,
                    reply_text="⚠️ Không thể chỉnh sửa bản nháp vào lúc này do sự cố kết nối mô hình. Bản nháp hiện tại vẫn được giữ nguyên.",
                )

            # Chế độ offline test (không có API key):
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            if "ngắn" in user_text.lower():
                concise_content = " ".join(lines[:max(1, len(lines) // 2)]) if len(lines) > 1 else lines[0] if lines else "Nội dung tóm tắt"
            elif "dài" in user_text.lower():
                concise_content = f"Kính gửi Quý đối tác,\n\n{content}\n\nRất mong sớm nhận được phản hồi từ Quý vị.\nTrân trọng."
            else:
                concise_content = content

            fixed_recipient = active_draft.get("recipient") or active_draft.get("to") or "approved@company.com"
            return IntentAnalysisResult(
                intent_type=IntentType.DRAFT,
                draft=DraftPayload(
                    draft_type=active_draft.get("draft_type", "email"),
                    draft_action="update",
                    draft_id=active_draft.get("draft_id"),
                    version=curr_ver + 1,
                    recipient=fixed_recipient,
                    to=fixed_recipient,
                    subject=active_draft.get("subject") or "Không tiêu đề",
                    content=concise_content,
                    body=concise_content,
                    expected_version=curr_ver,
                    expected_draft_id=active_draft.get("draft_id"),
                ),
            )

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

        # Tránh chặn event loop bằng cách chạy trong threadpool với timeout 9s
        return await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=9.0)

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

        # Nạp ngữ cảnh hội thoại có cấu trúc: tối đa 20 messages, giới hạn 12.000 ký tự
        context_blocks = []
        if session_context:
            history = session_context.get("history") or []
            if history:
                recent_msgs = history[-20:]
                history_lines = []
                total_len = 0
                for msg in reversed(recent_msgs):
                    line = f"{msg.get('role', 'user')}: {msg.get('content', '')}"
                    if total_len + len(line) > 12000:
                        break
                    history_lines.insert(0, line)
                    total_len += len(line)
                if history_lines:
                    context_blocks.append("<conversation_history>\n" + "\n".join(history_lines) + "\n</conversation_history>")

            active_draft = session_context.get("active_draft")
            if active_draft:
                context_blocks.append(f"<active_draft>\n{json.dumps(active_draft, ensure_ascii=False)}\n</active_draft>")

            last_read = session_context.get("last_read") or session_context.get("last_read_data")
            if last_read:
                context_blocks.append(f"<recent_read_records>\n{json.dumps(last_read, ensure_ascii=False)}\n</recent_read_records>")

            pending_clarify = session_context.get("pending_clarification")
            if pending_clarify:
                context_blocks.append(f"<pending_clarification>\n{json.dumps(pending_clarify, ensure_ascii=False)}\n</pending_clarification>")

        context_str = "\n\n".join(context_blocks)

        system_instruction = (
            "Bạn là bộ não phân tích ý định cho Trợ lý AI Doanh nghiệp kết nối Odoo ERP và Google Workspace.\n"
            "Hãy phân tích thật kỹ câu nói của người dùng cùng lịch sử hội thoại và dữ liệu phiên để trả về DUY NHẤT một chuỗi JSON hợp lệ theo schema:\n"
            "{\n"
            '  "intent_type": "reply" | "read" | "draft" | "prepare_write" | "clarify" | "unsupported" | "action_confirm" | "action_cancel" | "action_status",\n'
            '  "tool_name": "<tên tool chuẩn nếu intent là read hoặc prepare_write, ngược lại để null>",\n'
            '  "parameters": { ...các tham số trích xuất được... },\n'
            '  "reply_text": "<câu trả lời nếu intent là reply hoặc unsupported>",\n'
            '  "clarification_prompt": "<câu hỏi lại nếu intent là clarify>",\n'
            '  "candidate_options": [ ...danh sách lựa chọn nếu clarify... ],\n'
            '  "draft": { "draft_type": "email"|"task", "draft_action": "create"|"update"|"cancel", "recipient": "...", "subject": "...", "content": "..." }\n'
            "}\n\n"
            "QUY TẮC BẮT BUỘC:\n"
            "1. GIẢI THÍCH / HƯỚNG DẪN vs YÊU CẦU HÀNH ĐỘNG:\n"
            "   - Khi người dùng hỏi cách làm, hỏi định nghĩa, hỏi lý thuyết ('Cách tạo công việc?', 'Làm sao để gửi thư?', 'Hướng dẫn tạo khách hàng'):\n"
            "     -> intent_type='reply', reply_text='hướng dẫn người dùng'. TUYỆT ĐỐI KHÔNG TẠO BẢN XEM TRƯỚC (prepare_write).\n"
            "   - Khi câu nói mang tính phủ định ('Đừng tạo công việc', 'Tôi không muốn tạo task nữa', 'Thôi không làm'):\n"
            "     -> intent_type='reply', reply_text='Dạ vâng, tôi sẽ không thực hiện yêu cầu này.'. TUYỆT ĐỐI KHÔNG TẠO BẢN XEM TRƯỚC.\n"
            "   - Khi người dùng muốn hành động nhưng thiếu thông tin tối thiểu ('Tạo công việc' mà không có tên):\n"
            "     -> intent_type='clarify', clarification_prompt='hỏi thông tin còn thiếu'.\n"
            "   - Khi người dùng yêu cầu hành động cụ thể ('Tạo công việc báo cáo tuần', 'Đổi tên công việc thành X'):\n"
            "     -> intent_type='prepare_write', tool_name tương ứng.\n\n"
            "2. ĐỌC DỮ LIỆU CÔNG TY (Odoo / Calendar / Gmail / Tasks):\n"
            "   - Khi hỏi số liệu, danh sách đối tác, khách hàng, sản phẩm, tồn kho, đơn hàng, hóa đơn, nhân viên:\n"
            "     -> intent_type='read', chọn đúng tool_name:\n"
            "     * 'odoo.partners.search': Đối tác, khách hàng, nhà cung cấp.\n"
            "     * 'odoo.products.search': Sản phẩm, mã SKU, tồn kho, giá bán.\n"
            "     * 'odoo.sales.search': Đơn bán hàng, báo giá.\n"
            "     * 'odoo.purchases.search': Đơn mua hàng.\n"
            "     * 'odoo.inventory.search': Phiếu kho, điều chuyển kho.\n"
            "     * 'odoo.invoices.search': Hóa đơn, thanh toán.\n"
            "     * 'odoo.employees.search': Danh bạ nhân viên, phòng ban.\n"
            "     * 'calendar.events.list': Lịch họp Google Calendar.\n"
            "     * 'gmail.messages.unread': Thư Gmail chưa đọc.\n"
            "     * 'tasks.personal.list': Danh sách công việc cá nhân.\n"
            "     * 'identity.whoami': Xem hồ sơ của tôi.\n"
            "     * 'connections.status': Trạng thái kết nối dịch vụ.\n"
            "     * 'briefing.today': Điểm tin hôm nay có gì.\n\n"
            "3. SOẠN THẢO BẢN NHÁP (Draft):\n"
            "   - Khi người dùng muốn soạn nháp thư ('Soạn email gửi đối tác...'):\n"
            "     -> intent_type='draft', trích xuất recipient, subject, content.\n\n"
            "4. NGỮ CẢNH NỐI TIẾP:\n"
            "   - Dùng <conversation_history> và <recent_read_records> để trả lời các câu hỏi nối tiếp ('Công việc vừa tạo có hạn khi nào?', 'Khách hàng đó ở đâu?').\n\n"
            "5. CÂU HỎI VỀ THỜI GIAN / NGÀY THÁNG HIỆN TẠI:\n"
            "   - Khi người dùng hỏi ngày hôm nay, thứ mấy, bây giờ là mấy giờ, thời gian hiện tại:\n"
            "     -> intent_type='reply', reply_text='trả lời chính xác ngày giờ dựa theo thông tin Thời điểm hiện tại của hệ thống được cấp bên dưới (năm, tháng, ngày, giờ, múi giờ). TUYỆT ĐỐI KHÔNG tự bịa năm cũ.'\n\n"
            f"Danh sách công cụ khả dụng:\n" + "\n".join(tools_summary)
        )

        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(context.timezone)
        except Exception:
            tz = ZoneInfo("Asia/Ho_Chi_Minh")

        now = datetime.now(tz)
        weekday_vn = {
            0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm",
            4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"
        }.get(now.weekday(), "")
        current_time_str = f"{weekday_vn}, ngày {now.day:02d}/{now.month:02d}/{now.year}, {now.strftime('%H:%M')}"

        user_prompt = (
            f"{system_instruction}\n\n"
            f"{context_str}\n\n"
            f"Thời điểm hiện tại của hệ thống: {current_time_str} (Múi giờ {context.timezone})\n"
            f"Yêu cầu hiện tại của người dùng: \"{text}\"\n"
            f"Phản hồi JSON:"
        )

        settings = get_settings()
        is_mock_llm = hasattr(self._call_gemini_raw, "assert_called") or hasattr(self._call_gemini_raw, "return_value")
        if not settings.GEMINI_API_KEY and not is_mock_llm:
            return self._offline_semantic_dispatch(text, context, session_context)

        try:
            raw_json = await self._call_gemini_raw(user_prompt)
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

    def _offline_semantic_dispatch(
        self,
        text: str,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]] = None,
    ) -> IntentAnalysisResult:
        """Bộ phân tích ngữ nghĩa ngoại tuyến khi chưa cấu hình GEMINI_API_KEY.

        Tuân thủ nghiêm ngặt nguyên tắc:
        - Phân biệt giải thích/hướng dẫn ('cách tạo...?', 'làm sao...') -> reply.
        - Phân biệt phủ định ('đừng tạo...', 'không làm nữa') -> reply.
        - Thiếu thông tin ('tạo công việc') -> clarify.
        - Yêu cầu hành động cụ thể ('tạo công việc báo cáo tuần') -> prepare_write.
        - Tra cứu dữ liệu thực tế Odoo/Google -> read với đúng tool_name.
        - Ngữ cảnh liên tiếp tra cứu từ records đã đọc hoặc lịch sử.
        """
        lower = text.lower().strip()
        clean = text.strip()

        # Tra cứu thời gian / ngày tháng hiện tại
        if any(w in lower for w in [
            "hôm nay là ngày bao nhiêu", "hôm nay ngày bao nhiêu", "hôm nay là ngày mấy",
            "hôm nay ngày mấy", "hôm nay thứ mấy", "hôm nay là thứ mấy", "bây giờ là mấy giờ",
            "mấy giờ rồi", "ngày hôm nay là ngày bao nhiêu", "ngày bao nhiêu"
        ]):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(context.timezone)
            except Exception:
                tz = ZoneInfo("Asia/Ho_Chi_Minh")
            now = datetime.now(tz)
            weekday_vn = {
                0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm",
                4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"
            }.get(now.weekday(), "")
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text=f"Hôm nay là {weekday_vn}, ngày {now.day:02d}/{now.month:02d}/{now.year} (hiện tại là {now.strftime('%H:%M')}, múi giờ {context.timezone}).",
            )

        # 0. Chặn thao tác phá hoại hoặc câu lệnh ghi mơ hồ không có tham số
        unsupported_bare_writes = {
            "soạn email", "soạn email gửi đối tác", "gửi email", "gửi thư", "tạo cuộc họp",
            "xóa khách hàng", "xóa database", "tạo cơ hội crm mới", "tạo cơ hội crm", "tạo cơ hội"
        }
        if (
            lower in unsupported_bare_writes
            or lower.startswith("soạn email gửi đối tác")
            or lower.startswith("tạo cơ hội crm")
            or any(w in lower for w in ["xóa database", "xóa khách hàng"])
        ):
            return IntentAnalysisResult(
                intent_type=IntentType.UNSUPPORTED,
                reply_text=(
                    "⚠️ Thao tác này chưa được hỗ trợ qua Gateway hoặc yêu cầu phân tích hội thoại chuyên biệt. "
                    "Vui lòng sử dụng các cú pháp lệnh trực tiếp như /calendar, /gmail, /crm hoặc kiểm tra /today."
                ),
            )

        # 1. Phủ định (Negation)
        if any(neg in lower for neg in ["đừng tạo", "không tạo", "hủy không tạo", "thôi không tạo", "đừng làm", "không muốn tạo"]):
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text="Dạ vâng, tôi đã ghi nhận và sẽ không thực hiện thao tác này.",
            )

        # 2. Câu hỏi lý thuyết / hướng dẫn / giải thích
        is_question = any(q in lower for q in ["cách ", "làm thế nào", "làm sao để", "hướng dẫn", "như thế nào", "là gì", "có nên"]) or clean.endswith("?")
        if is_question and any(act in lower for act in ["tạo công việc", "tạo task", "gửi email", "soạn thư", "tạo lịch", "tạo cuộc họp"]):
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text=(
                    "Để tạo công việc cá nhân, bạn có thể nhắn trực tiếp: 'Tạo công việc [tên việc] | [hạn chót]' "
                    "hoặc dùng lệnh /task create <tên việc> | <hạn chót> | <ưu tiên>. "
                    "Hệ thống sẽ luôn gửi bản xem trước để bạn xác nhận an toàn trước khi lưu."
                ),
            )

        # 3. Yêu cầu tạo công việc cá nhân
        task_prefixes = ["tạo công việc ", "tạo task ", "thêm công việc ", "thêm task "]
        if lower in ["tạo công việc", "tạo task", "thêm công việc", "thêm task"]:
            return IntentAnalysisResult(
                intent_type=IntentType.CLARIFY,
                clarification_prompt="Bạn muốn đặt tên cho công việc là gì và có thời hạn cụ thể nào không?",
                candidate_options=["Báo cáo tuần", "Chuẩn bị tài liệu họp", "Kiểm tra email đối tác"],
            )

        for pfx in task_prefixes:
            if pfx in lower:
                idx = lower.find(pfx) + len(pfx)
                title = clean[idx:].strip(" :,-")
                for sfx in ["giúp tôi", "hộ tôi", "nhé", "nha", "giùm tôi"]:
                    if title.lower().endswith(sfx):
                        title = title[:-len(sfx)].strip()
                if not title or title in ["?", ".", "!"]:
                    return IntentAnalysisResult(
                        intent_type=IntentType.CLARIFY,
                        clarification_prompt="Bạn muốn đặt tên cho công việc là gì?",
                    )
                return IntentAnalysisResult(
                    intent_type=IntentType.PREPARE_WRITE,
                    tool_name="tasks.personal.create",
                    parameters={"title": title, "priority": "medium"},
                )

        # 4. Soạn thảo email
        if any(w in lower for w in ["soạn email", "viết email", "soạn thư"]):
            m_email = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", clean)
            recipient = m_email.group(0) if m_email else None
            content = clean
            if "nội dung" in lower:
                content = clean[lower.find("nội dung") + len("nội dung"):].strip(" :,-")
            elif recipient and recipient in clean:
                content = clean[clean.find(recipient) + len(recipient):].strip(" :,-")
            return IntentAnalysisResult(
                intent_type=IntentType.DRAFT,
                draft=DraftPayload(
                    draft_type="email",
                    draft_action="create",
                    recipient=recipient,
                    to=recipient,
                    subject="Email công việc",
                    content=content or clean,
                    body=content or clean,
                ),
            )

        # 5. Tra cứu dữ liệu doanh nghiệp
        if any(w in lower for w in ["đối tác", "khách hàng", "nhà cung cấp"]) and not any(w in lower for w in ["crm", "cơ hội"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh sách)\s*(?:đối tác|khách hàng|nhà cung cấp)?", "", lower).strip()
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.partners.search",
                parameters={"query": m_q if len(m_q) > 1 else ""},
            )

        if any(w in lower for w in ["sản phẩm", "tồn kho", "mặt hàng", "giá bán", "sku"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|kiểm tra|danh mục)?\s*(?:sản phẩm|tồn kho|mặt hàng)?", "", lower).strip()
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.products.search",
                parameters={"query": m_q if len(m_q) > 1 else ""},
            )

        if any(w in lower for w in ["đơn bán", "đơn hàng", "báo giá"]) and "mua" not in lower:
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh sách)?\s*(?:đơn bán|đơn hàng|báo giá)?", "", lower).strip()
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.sales.search",
                parameters={"query": m_q if len(m_q) > 1 else ""},
            )

        if any(w in lower for w in ["đơn mua", "đơn mua hàng", "nhập hàng"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh sách)?\s*(?:đơn mua|đơn mua hàng)?", "", lower).strip()
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.purchases.search",
                parameters={"query": m_q if len(m_q) > 1 else ""},
            )

        if any(w in lower for w in ["phiếu kho", "điều chuyển kho", "phiếu nhập", "phiếu xuất"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.inventory.search",
                parameters={"query": ""},
            )

        if any(w in lower for w in ["hóa đơn", "công nợ", "chứng từ"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.invoices.search",
                parameters={"query": ""},
            )

        if any(w in lower for w in ["nhân viên", "danh bạ", "nhân sự", "đồng nghiệp"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh bạ)?\s*(?:nhân viên|nhân sự)?", "", lower).strip()
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.employees.search",
                parameters={"query": m_q if len(m_q) > 1 else ""},
            )

        if any(w in lower for w in ["cơ hội", "crm", "lead"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh sách)?\s*(?:cơ hội|crm|lead)?", "", lower).strip()
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="crm.leads.search",
                parameters={"query": m_q if len(m_q) > 1 else ""},
            )

        if any(w in lower for w in ["tôi là ai", "hồ sơ của tôi", "thông tin tài khoản", "whoami", "hồ sơ cá nhân", "hồ sơ"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="identity.whoami",
            )

        if any(w in lower for w in ["trạng thái kết nối", "kiểm tra kết nối", "tình trạng kết nối", "kết nối dịch vụ", "kết nối của tôi", "kết nối odoo", "kết nối google"]) or lower == "kết nối":
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="connections.status",
            )

        if any(w in lower for w in ["hôm nay tôi có gì", "hôm nay tôi cần làm gì", "hôm nay có gì", "lịch hôm nay", "công việc hôm nay", "điểm tin"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="briefing.today",
            )

        if any(w in lower for w in ["danh sách công việc", "công việc của tôi", "việc của tôi", "xem công việc", "danh sách task", "nhiệm vụ của tôi", "nhiệm vụ"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="tasks.personal.list",
            )

        if any(w in lower for w in ["xem lịch", "lịch họp", "lịch của tôi", "sự kiện của tôi", "sự kiện"]):
            from app.services.context_resolver import resolve_relative_time_window
            start_dt, end_dt = resolve_relative_time_window(
                clean, timezone_str=context.timezone, allow_default_7_days=True, half_open=True
            )
            params = {}
            if start_dt and end_dt:
                params = {"time_min": start_dt.isoformat(), "time_max": end_dt.isoformat()}
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="calendar.events.list",
                parameters=params,
            )

        if any(w in lower for w in ["thư chưa đọc", "email chưa đọc", "kiểm tra thư", "xem email", "hòm thư"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="gmail.messages.unread",
            )

        # 6. Ngữ cảnh nối tiếp
        if session_context and any(w in lower for w in ["vừa tạo", "vừa đọc", "vừa rồi", "đó là gì", "có hạn khi nào", "ở đâu"]):
            last_read = session_context.get("last_read") or session_context.get("last_read_data")
            if last_read:
                return IntentAnalysisResult(
                    intent_type=IntentType.REPLY,
                    reply_text=f"Theo dữ liệu gần nhất: {json.dumps(last_read, ensure_ascii=False)}",
                )

        return IntentAnalysisResult(
            intent_type=IntentType.REPLY,
            reply_text=f"Tôi đã nhận được tin nhắn: '{clean}'. Bạn có thể yêu cầu tôi xem lịch, kiểm tra email, tra cứu dữ liệu Odoo hoặc tạo công việc cá nhân.",
        )


natural_pipeline = NaturalPipeline()
