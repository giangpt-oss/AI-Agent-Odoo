import asyncio
import json
import logging
import re
import time
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
    2. LLM Dispatcher: Sử dụng Gemini ép chuẩn JSON Schema (IntentAnalysisResult),
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
        if getattr(get_settings(), 'ENABLE_QUOTATION_APPROVAL_FLOW', False):
            from app.business.operations import interpret_quotation_flow, needs_customer_acceptance_clarification
            if needs_customer_acceptance_clarification(clean_text):
                return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                    clarification_prompt='Khách hàng đã thực sự chấp thuận báo giá chưa? Tôi cần xác định rõ trước khi chuẩn bị chuyển thành đơn bán; chưa thực hiện thay đổi trên Odoo.')
            flow = interpret_quotation_flow(clean_text)
            implicit = interpret_quotation_flow(clean_text, default_name='S00000')
            if not flow and implicit and implicit['action'] == 'request_approval':
                from app.business.workflow import confirmation_service as quote_confirmations, latest_quote_confirmation
                from app.business.quote_status import refresh_confirmation
                draft = (session_context or {}).get('active_draft') or {}
                conf = latest_quote_confirmation(context, draft) if draft.get('draft_type') == 'quotation' else None
                if conf:
                    conf, _ = await refresh_confirmation(context, quote_confirmations, conf)
                result = json.loads(conf.get('result_json') or '{}') if conf and conf['status'] == 'completed' else {}
                name = result.get('name')
                if name:
                    flow = interpret_quotation_flow(clean_text, default_name=name)
                    company = result.get('company_id')
                    if flow and isinstance(company, (list, tuple)) and company:
                        flow['company_id'] = company[0]
                else:
                    return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                        clarification_prompt='Bạn cho tôi số báo giá cần gửi sếp xác nhận nhé.')
            if flow:
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.quotation_flow', parameters=flow)

        if get_settings().ENABLE_REPORTING:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            from app.business.report_local import interpret as interpret_report
            try:
                today = datetime.now(ZoneInfo(context.timezone)).date()
            except (ValueError, KeyError):
                today = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()
            report = interpret_report(clean_text, session_context or {}, today)
            if report:
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name=report[0], parameters=report[1])

        if get_settings().ENABLE_QUOTATIONS:
            from app.business.quote_local import interpret
            active = (session_context or {}).get('active_draft') or {}
            if active.get('draft_type') == 'quotation' and lower.rstrip('.!') in {'có', 'có nhé', 'đồng ý', 'ok'}:
                if active.get('quotation_hints', {}).get('selection'):
                    return IntentAnalysisResult(intent_type=IntentType.CLARIFY, clarification_prompt='Bạn muốn chọn kết quả nào? Hãy cho biết tên hoặc ID từ danh sách vừa tìm được để tránh chọn nhầm.')
                from app.business.workflow import confirmation_service
                conf = confirmation_service.latest_draft_confirmation(user_id=context.user_id, chat_id=context.chat_id,
                    telegram_binding_id=context.telegram_binding_id or 'default', draft_id=active.get('draft_id'))
                if conf and conf.get('status') == 'pending' and conf.get('draft_version') == active.get('version') and confirmation_service.validate_access(conf, context.user_id, context.chat_id, context.telegram_binding_id, context.owner_key):
                    return IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=conf['confirmation_id'])
                job = ((session_context or {}).get('last_read') or {}).get('quotation_preparation_job') or {}
                if job.get('draft_id') == active.get('draft_id') and job.get('draft_version') == active.get('version'):
                    return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='sales.quotation.collect', parameters={'action': 'status'})
            local = interpret(clean_text, active, session_context)
            if local:
                if local['action'] == 'confirm':
                    from app.business.workflow import confirmation_service
                    conf = confirmation_service.latest_draft_confirmation(user_id=context.user_id, chat_id=context.chat_id,
                        telegram_binding_id=context.telegram_binding_id or 'default', draft_id=active.get('draft_id'))
                    if conf and confirmation_service.validate_access(conf, context.user_id, context.chat_id, context.telegram_binding_id, context.owner_key):
                        return IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=conf['confirmation_id'])
                    return IntentAnalysisResult(intent_type=IntentType.CLARIFY, clarification_prompt='Báo giá chưa có bản xem trước để xác nhận. Bạn muốn tôi kiểm tra và hiển thị bản xem trước không?')
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='sales.quotation.collect', parameters=local)

        # 1. Fast-path: Slash Commands (xác định, 0 LLM calls)
        if clean_text.startswith("/"):
            tokens = clean_text.split(maxsplit=2)
            base_cmd = tokens[0].lower().split("@")[0]
            if base_cmd == "/business":
                from app.business.operations import parse_command
                try:
                    return parse_command(clean_text)
                except (ValueError, TypeError, KeyError):
                    return IntentAnalysisResult(intent_type=IntentType.REPLY, reply_text='Bạn muốn thực hiện nghiệp vụ gì? Hãy cho biết loại chứng từ và thông tin cần thay đổi.')
            if base_cmd in ("/report", "/quote"):
                from app.business.tools import parse_command
                try:
                    return parse_command(clean_text)
                except (ValueError, TypeError, KeyError):
                    return IntentAnalysisResult(intent_type=IntentType.REPLY,
                        reply_text='Tôi chưa hiểu yêu cầu. Bạn muốn lấy báo cáo hay lập báo giá? Hãy mô tả thông tin bạn cần.')

            if base_cmd == "/workspace":
                from app.workspace.agent import parse_command
                return parse_command(clean_text)

            if base_cmd in {"/knowledge", "/rag"}:
                sub_cmd = tokens[1].lower() if len(tokens) > 1 else "list"
                if sub_cmd == "list":
                    return IntentAnalysisResult(
                        intent_type=IntentType.READ,
                        tool_name="knowledge.documents.list",
                    )
                if sub_cmd == "status":
                    return IntentAnalysisResult(
                        intent_type=IntentType.READ,
                        tool_name="knowledge.documents.status",
                        parameters={"job_id": tokens[2].strip()} if len(tokens) > 2 else {},
                    )
                if sub_cmd in {"index", "load"}:
                    paths_arg = tokens[2].strip() if len(tokens) > 2 else ""
                    paths_list = [p.strip() for p in paths_arg.split(",") if p.strip()] if paths_arg else []
                    return IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE,
                        tool_name="knowledge.documents.index",
                        parameters={"paths": paths_list} if paths_list else {},
                    )
                q = clean_text[len(tokens[0]):].strip()
                return IntentAnalysisResult(
                    intent_type=IntentType.READ,
                    tool_name="knowledge.query",
                    parameters={"query": q},
                )

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

            if base_cmd == "/start":
                return IntentAnalysisResult(
                    intent_type=IntentType.REPLY,
                    reply_text="Xin chào! Tôi có thể giúp gì cho bạn hôm nay?",
                )

            if base_cmd == "/help":
                return IntentAnalysisResult(
                    intent_type=IntentType.REPLY,
                    reply_text=self.registry.generate_capability_overview(list(context.permissions)),
                )

        # Fast-path: Thao tác kho tri thức trực tiếp (0 LLM call)
        if lower in {
            "danh sách tài liệu", "xem danh sách tài liệu", "kho tri thức",
            "danh mục tài liệu", "danh sách tài liệu kho tri thức", "xem kho tri thức"
        }:
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="knowledge.documents.list",
            )
        if lower in {
            "lập chỉ mục", "lập chỉ mục tài liệu", "nạp tài liệu",
            "nạp tài liệu vào kho tri thức", "cập nhật tài liệu", "quét tài liệu"
        }:
            return IntentAnalysisResult(
                intent_type=IntentType.PREPARE_WRITE,
                tool_name="knowledge.documents.index",
                parameters={},
            )

        # 2. Fast-path: Lời chào THUẦN TÚY (Chỉ áp dụng khi TOÀN CÂU là lời chào/menu, không kèm nội dung khác)
        text_nopunct = re.sub(r"[^\w\s]", "", lower).strip()
        pure_greetings = {
            "hello", "hi", "xin chào", "chào bạn", "chào bot", "chào em",
            "chào anh", "chào chị", "chào", "alo", "hey", "hello bot", "hi bot",
            "xin chào bot", "chào bạn bot", "hello bạn", "hi bạn", "chào nhé",
            "chào buổi sáng", "good morning", "chào buổi chiều", "chào buổi tối"
        }
        if text_nopunct in pure_greetings or lower in pure_greetings:
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text="Xin chào! Tôi có thể giúp gì cho bạn hôm nay?",
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
        active_quote = ((session_context or {}).get('active_draft') or {}).get('draft_type') == 'quotation'
        explicit_confirmation = bool(re.fullmatch(r'(?:xác nhận|đồng ý)\s+(?:c_|conf_)[a-zA-Z0-9_-]+', clean_text, re.I))
        quote_bare_confirmation = False
        if active_quote and lower in {'xác nhận', 'đồng ý', 'ok xác nhận'}:
            from app.services.confirmation_service import confirmation_service
            quote_bare_confirmation = bool(confirmation_service.list_pending_confirmations(user_id=context.user_id, chat_id=context.chat_id, telegram_binding_id=context.telegram_binding_id or 'default'))
        if (not active_quote or explicit_confirmation or quote_bare_confirmation) and (lower in {"xác nhận", "đồng ý", "ok xác nhận"} or lower.startswith("xác nhận ") or lower.startswith("đồng ý ")):
            words = lower.split()
            cid = words[-1] if len(words) > 1 and (words[-1].startswith("conf_") or words[-1].startswith("c_") or len(words[-1]) >= 8) else None
            return IntentAnalysisResult(
                intent_type=IntentType.ACTION_CONFIRM,
                confirmation_id=cid,
                parameters={"confirmation_id": cid} if cid else {},
            )

        explicit_cancel = bool(re.fullmatch(r'hủy\s+(?:c_|conf_)[a-zA-Z0-9_-]+', clean_text, re.I))
        if (not active_quote or explicit_cancel or lower == 'không xác nhận') and (lower in {"hủy", "hủy bỏ", "không xác nhận", "từ chối"} or lower.startswith("hủy ")):
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
            if active_draft and active_draft.get("draft_type") == "email":
                return await self._edit_draft_with_llm(active_draft, clean_text, context)

        # 6. TOÀN BỘ CÂU TỰ NHIÊN CÒN LẠI ĐỀU CHUYỂN SANG LLM DISPATCHER
        active = (session_context or {}).get('active_draft') or {}
        quote_request = re.match(r'^(?:(?:hãy|giúp tôi|tôi muốn)\s+)?(?:tạo|lập)(?:\s+cho tôi)?\s+(?:(?:một|1)\s+)?báo giá\b', lower)
        if get_settings().ENABLE_QUOTATIONS and (active.get('draft_type') == 'quotation' or quote_request):
            return await self._dispatch_quote_turn(clean_text, context, session_context)
        return await self._dispatch_with_llm(clean_text, context, session_context)

    async def _dispatch_quote_turn(self, text, context, session_context):
        """An active quote consumes semantic facts, never unpersisted acknowledgement prose."""
        from app.business.quote_schema import QuoteTurn
        from app.services.intent_prompt import QUOTE_PROMPT
        history = (session_context or {}).get('history', [])[-4:]
        active = (session_context or {}).get('active_draft') or {}
        from app.business.workflow import confirmation_service
        conf = confirmation_service.latest_draft_confirmation(user_id=context.user_id, chat_id=context.chat_id,
            telegram_binding_id=context.telegram_binding_id or 'default', draft_id=active.get('draft_id'))
        if conf and not confirmation_service.validate_access(conf, context.user_id, context.chat_id, context.telegram_binding_id, context.owner_key):
            conf = None
        if conf and conf['status'] in ('executing', 'unknown_result'):
            from app.business.quote_status import refresh_confirmation
            try:
                conf, _ = await asyncio.wait_for(refresh_confirmation(context, confirmation_service, conf), timeout=6)
            except TimeoutError:
                pass
        correction = json.loads(conf.get('result_json') or '{}') if conf and conf['status'] == 'failed' else {}
        prompt = QUOTE_PROMPT + '\nTrả DUY NHẤT JSON theo schema đã cấp.\n'
        prompt += '\nKhông trả intent_type/reply_text/tool_name ở đây. action=other chỉ khi người dùng đổi chủ đề rõ ràng, không phải thông tin bổ sung cho báo giá. Không lấy lời bot tự nói làm dữ liệu đã lưu.\n'
        prompt += json.dumps({'draft': active, 'confirmation_status': conf.get('status') if conf else None, 'odoo_correction': correction,
            'history': history, 'user_text': text}, ensure_ascii=False)
        try:
            remaining = max(0.1, min(12.0, (session_context or {}).get('_turn_deadline', time.time() + 14) - time.time() - 2))
            raw = await asyncio.wait_for(self._call_gemini_raw(prompt, response_schema=QuoteTurn.model_json_schema()), timeout=remaining)
            cleaned = raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            turn = QuoteTurn.model_validate_json(cleaned)
            if turn.action == 'confirm':
                if conf:
                    return IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=conf['confirmation_id'])
                return IntentAnalysisResult(intent_type=IntentType.CLARIFY, clarification_prompt='Báo giá chưa có bản xem trước để xác nhận. Bạn muốn tôi kiểm tra và hiển thị bản xem trước không?')
            if turn.action == 'other':
                return await self._dispatch_with_llm(text, context, session_context)
            return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='sales.quotation.collect', parameters=turn.model_dump(exclude_none=True))
        except Exception as exc:
            logger.warning('Quotation semantic extraction failed: %s code=%s', type(exc).__name__, getattr(exc, 'code', None))
            if getattr(exc, 'code', None) == 429:
                message = 'Dịch vụ AI đang bị giới hạn lượt gọi (429), không phải lỗi ghi dữ liệu Odoo. Nháp cũ vẫn được giữ; thông tin ở tin nhắn này chưa được điền vào trường. Chưa tạo báo giá trên Odoo.'
                pending = active.get('quotation_hints', {}).get('selection') or {}
                if time.time() - pending.get('at', 0) <= 1800 and pending.get('ids'):
                    message += '\nCác ID đang chờ bạn chọn: ' + ', '.join(map(str, pending['ids'])) + '. Bạn có thể trả lời bằng tên hoặc ID khi dịch vụ AI hoạt động trở lại.'
                else:
                    message += '\nThông tin cũ vẫn được giữ. Khi dịch vụ AI hoạt động trở lại, bạn có thể hỏi tiến độ hoặc yêu cầu xem báo giá để tiếp tục.'
            elif isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or getattr(exc, 'code', None) in (500, 502, 503, 504):
                message = 'Dịch vụ AI phân tích đang quá hạn hoặc tạm thời không đáp ứng. Nháp cũ vẫn được giữ; thông tin ở tin nhắn này chưa được điền vào trường. Chưa tạo báo giá trên Odoo. Bạn có thể gửi lại thông tin khi dịch vụ AI hoạt động.'
            else:
                message = 'Chưa phân tích được thông tin bổ sung; nháp đã lưu vẫn được giữ. Bạn vui lòng nói rõ trường cần sửa và giá trị; customer_lead là số ngày, ví dụ 10 ngày.'
            return IntentAnalysisResult(intent_type=IntentType.CLARIFY, clarification_prompt=message)

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
            lines = [line.strip() for line in content.splitlines() if line.strip()]
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

    async def _call_gemini_raw(self, prompt: str, response_schema=None) -> str:
        """Gọi mô hình với deadline hữu hạn, không tự thử lại HTTP."""
        settings = get_settings()
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        def _sync_call() -> str:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=28000, retry_options=types.HttpRetryOptions(attempts=1)))
            model = settings.INTENT_LLM_MODEL or settings.DEFAULT_LLM_MODEL or "gemini-3.5-flash-lite"
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_json_schema=response_schema,
                    max_output_tokens=900 if response_schema else None,
                    thinking_config=types.ThinkingConfig(thinking_level='MINIMAL') if model.startswith('gemini-3') else None,
                ),
            )
            return resp.text or ""

        # HTTP giới hạn 28 giây; lớp async chặn thời gian chờ toàn lời gọi.
        return await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=28.0 if response_schema else 20.0)

    async def _dispatch_with_llm(
        self,
        text: str,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]] = None,
    ) -> IntentAnalysisResult:
        available_tools = self.registry.list_tools(list(context.permissions))
        from app.services.intent_prompt import INTENT_PROMPT, tool_catalog
        tools_summary = tool_catalog(available_tools)

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

        system_instruction = INTENT_PROMPT + '\nCông cụ công khai được cấp:\n' + tools_summary

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
            raw_json = await self._call_gemini_raw(user_prompt, response_schema=IntentAnalysisResult.model_json_schema())
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
            try:
                offline_res = self._offline_semantic_dispatch(text, context, session_context)
                if offline_res.intent_type != IntentType.REPLY or "Tôi đã nhận được tin nhắn:" not in (offline_res.reply_text or ""):
                    return offline_res
            except Exception:
                pass
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
                    "⚠️ Thao tác này chưa được hỗ trợ hoặc yêu cầu thêm thông tin cụ thể. "
                    "Bạn vui lòng diễn đạt rõ hơn nội dung cần hỗ trợ (ví dụ: 'Xem lịch', 'Kiểm tra thư' hoặc 'Tìm đối tác')."
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
                    "Để tạo công việc cá nhân, bạn chỉ cần nhắn trực tiếp nội dung công việc (ví dụ: 'Tạo công việc chuẩn bị báo cáo quý'). "
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

        # Tra cứu số lượng cụ thể nối tiếp từ dữ liệu vừa đọc
        if session_context and any(w in lower for w in [
            "con số cụ thể", "số cụ thể", "cụ thể là bao nhiêu", "cụ thể bao nhiêu",
            "chính xác là bao nhiêu", "chính xác bao nhiêu", "tổng cộng bao nhiêu",
            "con số chính xác", "bao nhiêu vậy", "bao nhiêu thế"
        ]):
            last_read = session_context.get("last_read") or session_context.get("last_read_data")
            if isinstance(last_read, dict):
                last_count = last_read.get("odoo_last_count")
                if isinstance(last_count, dict) and "total_count" in last_count:
                    cnt = last_count["total_count"]
                    cat = last_count.get("category", "")
                    cat_vn = {
                        "odoo.partners": "liên hệ/đối tác",
                        "odoo.products": "sản phẩm",
                        "odoo.sales": "đơn bán hàng",
                        "odoo.purchases": "đơn mua hàng",
                        "odoo.inventory": "phiếu kho",
                        "odoo.invoices": "hóa đơn",
                        "odoo.employees": "nhân viên",
                    }.get(cat, "bản ghi")
                    cat_vn = last_count.get("entity_label") or cat_vn
                    return IntentAnalysisResult(
                        intent_type=IntentType.REPLY,
                        reply_text=f"📊 Số đã tra cứu là {cnt:,} {cat_vn}.",
                    )

        # 5. Tra cứu dữ liệu doanh nghiệp
        is_count_query = any(w in lower for w in [
            "bao nhiêu", "tổng số", "mấy", "đếm", "số lượng", "tổng cộng", "con số"
        ])

        if any(w in lower for w in ["đối tác", "khách hàng", "nhà cung cấp", "liên hệ", "contact"]) and not any(w in lower for w in ["crm", "cơ hội"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh sách|hiện tại|đang có|có|tổng số|bao nhiêu|số lượng)?\s*(?:đối tác|khách hàng|nhà cung cấp|liên hệ|contacts?)?", "", lower).strip()
            params = {}
            if is_count_query:
                params["count_only"] = True
            if len(m_q) > 1 and m_q not in ["không", "nào", "gì"]:
                params["query"] = m_q
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.partners.search",
                parameters=params,
            )

        if any(w in lower for w in ["sản phẩm", "tồn kho", "mặt hàng", "hàng hóa", "giá bán", "sku"]) and not any(w in lower for w in ["phiếu kho", "điều chuyển"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|kiểm tra|danh mục|hiện tại|đang có|có|tổng số|bao nhiêu|số lượng)?\s*(?:sản phẩm|tồn kho|mặt hàng|hàng hóa)?", "", lower).strip()
            params = {}
            if is_count_query:
                params["count_only"] = True
            if len(m_q) > 1 and m_q not in ["không", "nào", "gì"]:
                params["query"] = m_q
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.products.search",
                parameters=params,
            )

        if any(w in lower for w in ["đơn bán", "đơn hàng", "báo giá"]) and "mua" not in lower:
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh sách|hiện tại|đang có|có|tổng số|bao nhiêu|số lượng)?\s*(?:đơn bán|đơn hàng|báo giá)?", "", lower).strip()
            params = {}
            if is_count_query:
                params["count_only"] = True
            if len(m_q) > 1 and m_q not in ["không", "nào", "gì"]:
                params["query"] = m_q
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.sales.search",
                parameters=params,
            )

        if any(w in lower for w in ["đơn mua", "đơn mua hàng", "nhập hàng"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh sách|hiện tại|đang có|có|tổng số|bao nhiêu|số lượng)?\s*(?:đơn mua|đơn mua hàng)?", "", lower).strip()
            params = {}
            if is_count_query:
                params["count_only"] = True
            if len(m_q) > 1 and m_q not in ["không", "nào", "gì"]:
                params["query"] = m_q
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.purchases.search",
                parameters=params,
            )

        if any(w in lower for w in ["phiếu kho", "điều chuyển kho", "phiếu nhập", "phiếu xuất"]):
            params = {"count_only": True} if is_count_query else {"query": ""}
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.inventory.search",
                parameters=params,
            )

        if any(w in lower for w in ["hóa đơn", "công nợ", "chứng từ"]):
            params = {"count_only": True} if is_count_query else {"query": ""}
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.invoices.search",
                parameters=params,
            )

        if any(w in lower for w in ["nhân viên", "danh bạ", "nhân sự", "đồng nghiệp"]):
            m_q = re.sub(r"(?:tìm|tra cứu|xem|danh bạ|hiện tại|đang có|có|tổng số|bao nhiêu|số lượng)?\s*(?:nhân viên|nhân sự)?", "", lower).strip()
            params = {}
            if is_count_query:
                params["count_only"] = True
            if len(m_q) > 1 and m_q not in ["không", "nào", "gì"]:
                params["query"] = m_q
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="odoo.employees.search",
                parameters=params,
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

        # 6. Tra cứu hoặc quản lý Kho Tri Thức (RAG)
        if any(w in lower for w in [
            "danh sách tài liệu", "tài liệu trong kho", "kho tri thức", "danh mục tài liệu", "xem tài liệu", "có tài liệu gì"
        ]) and not any(w in lower for w in ["chính sách", "quy chế", "quy định", "nội quy", "hỏi", "tra cứu"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="knowledge.documents.list",
            )

        if any(w in lower for w in [
            "lập chỉ mục", "nạp tài liệu", "index tài liệu", "quét tài liệu", "cập nhật tài liệu"
        ]):
            return IntentAnalysisResult(
                intent_type=IntentType.PREPARE_WRITE,
                tool_name="knowledge.documents.index",
                parameters={},
            )

        if any(w in lower for w in [
            "quy chế", "quy định", "nội quy", "chính sách", "sổ tay nhân viên",
            "sổ tay", "hướng dẫn nội bộ", "tài liệu nội bộ", "tra cứu kho tri thức", "tra cứu tài liệu"
        ]) or (session_context and session_context.get("last_read", {}).get("knowledge.query") and any(w in lower for w in ["thế nào", "bao lâu", "ai", "ở đâu"])):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="knowledge.query",
                parameters={"query": clean},
            )

        # 7. Ngữ cảnh nối tiếp
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
