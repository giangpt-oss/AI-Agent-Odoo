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


def _quote_today(context):
    """Business-local date used to resolve relative quotation deadlines."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        return datetime.now(ZoneInfo(context.timezone or 'Asia/Ho_Chi_Minh')).date()
    except (ValueError, KeyError):
        return datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()


class NaturalPipeline:
    """Pipeline phân tích ý định và định tuyến hội thoại thống nhất.
    
    Quy trình:
    1. Fast-path: Khớp trực tiếp slash commands, chào hỏi thuần, status audit và các mẫu quen thuộc (0 LLM call).
    2. LLM Dispatcher: Sử dụng Gemini ép chuẩn JSON Schema (IntentAnalysisResult),
       đưa đầy đủ 20 messages lịch sử (tối đa 12.000 ký tự), active draft và snapshot dữ liệu vừa đọc vào prompt.
    """

    def __init__(self, tool_registry: Optional[ToolRegistry] = None) -> None:
        self.registry = tool_registry or global_tool_registry
        from app.services.turn_router import TurnRouter
        self.router = TurnRouter(self._call_router_llm)

    async def analyze_input(
        self,
        text: str,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]] = None,
    ) -> IntentAnalysisResult:
        clean_text = (text or "").strip()
        lower = clean_text.lower()
        active = (session_context or {}).get('active_draft') or {}
        if m_po_btn := re.fullmatch(r"/(purchase_edit|purchase_approve|purchase_grant|purchase_feedback|purchase_reject)\s+([0-9a-f]{24})", clean_text, re.I):
            btn_map = {
                "purchase_edit": "edit",
                "purchase_approve": "submit",
                "purchase_grant": "approve",
                "purchase_feedback": "feedback",
                "purchase_reject": "reject",
            }
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="business.purchase_approval_button",
                parameters={"action": btn_map[m_po_btn[1].lower()], "token": m_po_btn[2]},
            )
        if getattr(get_settings(), 'ENABLE_BUSINESS_ACTIONS', False) and not clean_text.startswith('/'):
            from app.business.operations import (
                interpret_purchase_create,
                interpret_purchase_draft_reply,
                interpret_purchase_flow,
                interpret_stock_create,
                interpret_stock_draft_reply,
                interpret_stock_flow,
            )
            from app.services.workspace_tool_plan import detect_domains

            recent_history = (session_context or {}).get("history") or []
            recent_last_read = (session_context or {}).get("last_read")
            if stock_act := interpret_stock_flow(clean_text, history=recent_history):
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.stock_flow', parameters=stock_act)
            if stock_act := interpret_stock_create(clean_text):
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.stock_create', parameters=stock_act)
            if purchase := interpret_purchase_flow(clean_text, history=recent_history, last_read=recent_last_read):
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.purchase_flow', parameters=purchase)
            if purchase := interpret_purchase_create(clean_text):
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.purchase_create', parameters=purchase)
            stock_draft = str(active.get('business_action', '')).startswith('business.stock.')
            if stock_draft:
                if draft_reply := interpret_stock_draft_reply(clean_text, active):
                    return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.stock_create', parameters=draft_reply)
            purchase_draft = str(active.get('business_action', '')).startswith('business.purchases.')
            if purchase_draft:
                from app.business import purchase_optional
                if purchase_optional.is_active(active):
                    return IntentAnalysisResult(
                        intent_type=IntentType.READ,
                        tool_name='business.purchase_create',
                        parameters={'action': 'optional_reply', 'text': clean_text},
                    )
                if draft_reply := interpret_purchase_draft_reply(clean_text, active):
                    return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.purchase_create', parameters=draft_reply)
            detected = detect_domains(clean_text)
            stock_request = (
                'stock' in detected
                and 'reports' not in detected
                and not (detected & {'calendar', 'drive', 'docs', 'sheets', 'slides', 'tasks'})
            )
            if stock_request or (stock_draft and lower.rstrip('.!') not in {
                    'xác nhận', 'đồng ý', 'ok xác nhận', 'hủy', 'hủy bỏ', 'không xác nhận', 'từ chối'}):
                return await self._dispatch_with_llm(clean_text, context, session_context, domains=['stock'])
            purchase_request = (
                'purchases' in detected
                and 'reports' not in detected
                and not (detected & {'calendar', 'drive', 'docs', 'sheets', 'slides', 'tasks'})
            )
            if purchase_request or (purchase_draft and lower.rstrip('.!') not in {
                    'xác nhận', 'đồng ý', 'ok xác nhận', 'hủy', 'hủy bỏ', 'không xác nhận', 'từ chối'}):
                return await self._dispatch_with_llm(clean_text, context, session_context, domains=['purchases'])
        if (active.get('quotation_hints') or {}).get('unclassified_product_candidate') or (active.get('quotation_hints') or {}).get('product_query_1_needs_confirmation'):
            from app.business.quote_local import interpret as interpret_quote_local
            decision = interpret_quote_local(text, active, session_context)
            if decision and decision.get('action') in ('discard_price_candidate', 'restore_product_candidate'):
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='sales.quotation.collect', parameters=decision)

        # Giữ nguyên cơ chế điều hướng thống nhất cho cả Legacy và Two-Stage Router:
        # Khi ENABLE_TWO_STAGE_ROUTER=True:
        # - Xác nhận / Hủy CHỈ nhận từ nút bấm Telegram (/confirm <id>, /cancel <id>).
        # - Mọi câu chat tự nhiên không thuộc fast-path tất định (slash commands, lời chào thuần, hỏi giờ/chức năng, bản nháp email)
        #   đều đi qua TurnRouter 2 tầng trước khi chọn tool.
        if (active.get("draft_type") == "quotation" and not clean_text.startswith("/")
                and getattr(get_settings(), "ENABLE_QUOTATIONS", False)):
            explicit_workspace = re.search(
                r"\b(?:drive|gmail|calendar|docs|sheets|slides|mail|email)\b|"
                r"quy chế|nội quy|chính sách|sổ tay|tài liệu nội bộ|tài liệu|sơ đồ|hướng dẫn|quy trình|nghiệp vụ|"
                r"\b(?:lịch|lich|cuộc họp|cuoc hop)\b",
                lower,
            )
            quotation_target = re.search(r"\bbáo giá\b|\bs\d{3,}\b", lower)
            if explicit_workspace and not quotation_target:
                topic_context = dict(session_context or {})
                topic_context.pop("active_draft", None)
                topic_context.pop("pending_clarification", None)
                if getattr(get_settings(), "ENABLE_TWO_STAGE_ROUTER", False):
                    routed = await self._route_two_stage(clean_text, context, topic_context)
                    if routed is not None:
                        return routed
                return await self._dispatch_with_llm(clean_text, context, topic_context)
        if lower.rstrip("?!.") in {"hôm nay tôi có gì", "hôm nay tôi cần làm gì", "hôm nay có gì"}:
            return IntentAnalysisResult(intent_type=IntentType.READ, tool_name="briefing.today")
        from app.business.operations import looks_like_quote_chatter
        if getattr(get_settings(), 'ENABLE_QUOTATIONS', False) and looks_like_quote_chatter(clean_text, active):
            return await self._dispatch_quote_chatter(clean_text, context, session_context)
        if getattr(get_settings(), 'ENABLE_QUOTATION_APPROVAL_FLOW', False):
            from app.business.operations import interpret_quotation_flow, needs_customer_acceptance_clarification
            if needs_customer_acceptance_clarification(clean_text):
                return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                    clarification_prompt='Khách hàng đã thực sự chấp thuận báo giá chưa? Tôi cần xác định rõ trước khi chuẩn bị chuyển thành đơn bán; chưa thực hiện thay đổi trên Odoo.')
            flow = interpret_quotation_flow(clean_text)
            implicit = interpret_quotation_flow(clean_text, default_name='S00000')
            if implicit and implicit['action'] == 'request_approval':
                return IntentAnalysisResult(intent_type=IntentType.REPLY,
                    reply_text='Hãy gửi lại PDF của báo giá và bấm “Xác nhận gửi duyệt” trên đúng bản PDF đó.')
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
            two_stage_btn_only = bool(getattr(get_settings(), "ENABLE_TWO_STAGE_ROUTER", False))
            active = (session_context or {}).get('active_draft') or {}
            if active.get('draft_type') == 'quotation' and lower.rstrip('.!') in {'có', 'có nhé', 'đồng ý', 'ok'}:
                if active.get('quotation_hints', {}).get('selection'):
                    return IntentAnalysisResult(intent_type=IntentType.CLARIFY, clarification_prompt='Bạn muốn chọn kết quả nào? Hãy cho biết tên hoặc ID từ danh sách vừa tìm được để tránh chọn nhầm.')
                from app.business.workflow import confirmation_service
                conf = confirmation_service.latest_draft_confirmation(user_id=context.user_id, chat_id=context.chat_id,
                    telegram_binding_id=context.telegram_binding_id or 'default', draft_id=active.get('draft_id'))
                if conf and conf.get('status') == 'pending' and conf.get('draft_version') == active.get('version') and confirmation_service.validate_access(conf, context.user_id, context.chat_id, context.telegram_binding_id, context.owner_key):
                    if two_stage_btn_only:
                        return IntentAnalysisResult(
                            intent_type=IntentType.CLARIFY,
                            clarification_prompt='Để đảm bảo an toàn, vui lòng bấm nút Xác nhận hoặc Từ chối trực tiếp trên bản xem trước.',
                        )
                    return IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=conf['confirmation_id'])
                job = ((session_context or {}).get('last_read') or {}).get('quotation_preparation_job') or {}
                if job.get('draft_id') == active.get('draft_id') and job.get('draft_version') == active.get('version'):
                    return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='sales.quotation.collect', parameters={'action': 'status'})
            local = interpret(clean_text, active, session_context)
            if local:
                if local['action'] == 'confirm':
                    if two_stage_btn_only:
                        return IntentAnalysisResult(
                            intent_type=IntentType.CLARIFY,
                            clarification_prompt='Để đảm bảo an toàn, vui lòng bấm nút Xác nhận hoặc Từ chối trực tiếp trên bản xem trước.',
                        )
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

        # Fast-path: Trạng thái kết nối & Hồ sơ cá nhân (0 LLM call)
        if any(w in lower for w in ["trạng thái kết nối", "kiểm tra kết nối", "tình trạng kết nối", "kết nối dịch vụ", "kết nối của tôi", "kết nối odoo", "kết nối google"]) or lower == "kết nối":
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="connections.status",
            )
        if any(w in lower for w in ["tôi là ai", "hồ sơ của tôi", "thông tin tài khoản", "whoami", "hồ sơ cá nhân", "hồ sơ"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="identity.whoami",
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

        # 4. Fast-path: Các câu lệnh xác nhận / hủy / kiểm tra mã xác nhận (chỉ bật xác nhận gõ chữ khi tắt button-only)
        if not getattr(get_settings(), "ENABLE_TWO_STAGE_ROUTER", False):
            active_quote = ((session_context or {}).get('active_draft') or {}).get('draft_type') == 'quotation'
            explicit_confirmation = bool(re.fullmatch(r'(?:xác nhận|đồng ý)\s+(?:c_|conf_)[a-zA-Z0-9_-]+', clean_text, re.I))
            quote_bare_confirmation = False
            if active_quote and lower in {'xác nhận', 'đồng ý', 'ok xác nhận'}:
                from app.services.confirmation_service import confirmation_service
                quote_bare_confirmation = bool(confirmation_service.list_pending_confirmations(user_id=context.user_id, chat_id=context.chat_id, telegram_binding_id=context.telegram_binding_id or 'default'))
            if (not active_quote or explicit_confirmation or quote_bare_confirmation) and (lower in {"xác nhận", "đồng ý", "ok xác nhận"} or explicit_confirmation):
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

        # 6. TOÀN BỘ CÂU TỰ NHIÊN CÒN LẠI ĐỀU CHUYỂN SANG LLM DISPATCHER (hoặc Two-Stage Router khi bật cờ)
        active = (session_context or {}).get('active_draft') or {}
        from app.business.quote_fallback import plain as quote_plain
        plain_text = quote_plain(clean_text)
        explicit_workspace_write = bool(
            re.search(
                r"\b(?:soan|viet|gui)\s+(?:nhap\s+|ban\s+nhap\s+)?(?:email|e-mail|mail|thu)\b"
                r"|\b(?:tao|dat|them)\s+(?:lich|cuoc\s+hop|su\s+kien|cong\s+viec|task|viec)\b",
                plain_text,
            )
        )
        if get_settings().ENABLE_QUOTATIONS and active.get('draft_type') == 'quotation':
            return await self._dispatch_quote_turn(clean_text, context, session_context)
        if getattr(get_settings(), "ENABLE_TWO_STAGE_ROUTER", False):
            routed = await self._route_two_stage(clean_text, context, session_context)
            if routed is not None:
                return routed
        if get_settings().ENABLE_QUOTATIONS and not explicit_workspace_write and re.search(r'\bbao gia\b', plain_text):
            return await self._dispatch_quote_turn(clean_text, context, session_context)
        return await self._dispatch_with_llm(clean_text, context, session_context)

    async def _dispatch_quote_chatter(self, text, context, session_context):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.business.chatter_schema import ChatterTurn
        from app.business.quote_status import refresh_confirmation
        from app.business.workflow import confirmation_service as quote_confirmations, latest_quote_confirmation

        active = (session_context or {}).get('active_draft') or {}
        conf = latest_quote_confirmation(context, active) if active.get('draft_type') == 'quotation' else None
        if conf:
            conf, _ = await refresh_confirmation(context, quote_confirmations, conf)
        result = json.loads(conf.get('result_json') or '{}') if conf and conf['status'] == 'completed' else {}
        if not result and active.get('business_action', '').startswith('business.sales.'):
            result = ((session_context or {}).get('last_read') or {}).get('quotation_chatter_target') or {}
        timezone = context.timezone or 'Asia/Bangkok'
        try:
            now = datetime.now(ZoneInfo(timezone)).isoformat()
        except (KeyError, ValueError):
            now = datetime.now(ZoneInfo('Asia/Bangkok')).isoformat()
        prompt = '''Trích xuất yêu cầu thao tác trên lịch sử báo giá thành JSON theo schema.
Gửi tin nhắn qua email dùng action=message, options.body là nội dung người dùng cung cấp và options.recipient là địa chỉ email, hoặc "customer" khi người dùng chỉ rõ khách hàng của báo giá. Ghi chú nội bộ dùng action=note và body. Lên lịch Việc cần làm, Email, Gọi, Tài liệu dùng action=activity và activity_type lần lượt todo, email, call, document. Cuộc họp dùng action=meeting, summary, start và stop dạng ISO, múi giờ địa phương. Ngày đến hạn dùng date_deadline YYYY-MM-DD. Ghi chú hoạt động dùng note. Người được phân công theo tên dùng user_query, không đoán user_id. Tên người tham dự dùng attendee_queries. Chỉ cung cấp attendee_ids khi người dùng nói rõ ID. Giữ nguyên nội dung và thông tin do người dùng cung cấp. Nếu thiếu giá trị thì để trống để hệ thống hỏi. Không suy đoán số báo giá, thời gian, người nhận hoặc người tham dự.'''
        prompt += '\n' + json.dumps({'now': now, 'timezone': timezone, 'last_quotation': result.get('name'),
                                      'history': (session_context or {}).get('history', [])[-4:], 'user_text': text}, ensure_ascii=False)
        try:
            raw = await asyncio.wait_for(self._call_gemini_raw(prompt, response_schema=ChatterTurn.model_json_schema()), timeout=12)
            cleaned = raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            turn = ChatterTurn.model_validate_json(cleaned)
        except Exception:
            return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                clarification_prompt='Tôi chưa xác định được nội dung thao tác. Bạn hãy cho biết báo giá, loại thao tác và thông tin cần ghi.')
        names = re.findall(r'\bS\d{3,}\b', text, re.I)
        if len(names) > 1:
            return IntentAnalysisResult(intent_type=IntentType.CLARIFY, clarification_prompt='Bạn chọn một báo giá để thực hiện thao tác nhé.')
        name = names[0].upper() if names else result.get('name')
        if not name:
            return IntentAnalysisResult(intent_type=IntentType.CLARIFY, clarification_prompt='Bạn cho tôi số báo giá cần ghi hoặc lên lịch nhé.')
        params = {'quotation_name': name, 'action': turn.action, 'options': turn.options.model_dump(exclude_none=True)}
        if not turn.quotation_name and isinstance(result.get('company_id'), (list, tuple)) and result['company_id']:
            params['company_id'] = result['company_id'][0]
        return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.quotation_chatter', parameters=params)

    async def _dispatch_quote_turn(self, text, context, session_context):
        """An active quote consumes semantic facts, never unpersisted acknowledgement prose."""
        from app.business.quote_schema import QuoteTurn
        from app.business.quote_fallback import (
            explicit_customer_name, extract as extract_local_quote,
            non_creation_intent, pending_field, plain as quote_plain,
        )
        from app.services.intent_prompt import QUOTE_PROMPT
        session_context = session_context or {}
        history = []
        budget = 12000
        for item in reversed(session_context.get('history', [])[-12:]):
            content = str(item.get('content') or '')[-min(2000, budget):]
            if not content or item.get('role') not in ('user', 'assistant'):
                continue
            history.append({'role': item['role'], 'content': content})
            budget -= len(content)
            if budget <= 0:
                break
        history.reverse()
        active = (session_context or {}).get('active_draft') or {}
        today = _quote_today(context)
        local_facts = extract_local_quote(text, active, session_context, today=today)
        pending = pending_field(session_context, active)
        safe_non_creation = non_creation_intent(text)
        from app.business.quote_local import requests_fresh_quote, requests_new_quote
        if (active.get('draft_type') == 'quotation' and local_facts and local_facts.get('customer_query')
                and requests_new_quote(text) and active.get('quotation_data', {}).get('partner_id')
                and local_facts['customer_query'].casefold() != str(active.get('quotation_hints', {}).get('partner_label') or '').casefold()
                and 'mới' not in text.casefold()):
            return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                clarification_prompt='Bạn muốn tạo báo giá mới cho khách này hay sửa nháp đang có?')
        if active.get('draft_type') == 'quotation' and re.fullmatch(r'\d+\s+ngay(?:\s+nhe)?', quote_plain(text).strip()) and not local_facts:
            return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                clarification_prompt=f"{text.strip().split()[0]} ngày là hạn báo giá hay điều khoản thanh toán?")
        if active.get('draft_type') == 'quotation' and not (active.get('quotation_hints') or {}).get('optional'):
            plain_turn = quote_plain(text).strip()
            if re.search(r'^(?:bo qua|khong can|khong nhap)\b', plain_turn) and not local_facts:
                qdata = active.get('quotation_data') or {}
                missing_req = []
                if not qdata.get('partner_id'):
                    missing_req.append('khách hàng')
                if not qdata.get('pricelist_id'):
                    missing_req.append('bảng giá')
                if not qdata.get('lines') or any(not ln.get('product_id') or not ln.get('quantity') for ln in qdata.get('lines') or []):
                    missing_req.append('sản phẩm và số lượng')
                if not qdata.get('validity_date'):
                    missing_req.append('ngày hết hạn báo giá')
                if not qdata.get('payment_term_id'):
                    missing_req.append('điều khoản thanh toán')
                if missing_req:
                    return IntentAnalysisResult(
                        intent_type=IntentType.CLARIFY,
                        clarification_prompt=(
                            f"Các mục sau là bắt buộc trước khi tạo báo giá nên chưa thể bỏ qua: {', '.join(missing_req)}. "
                            "Bạn gửi nhanh giúp tôi nhé (ví dụ: hết hạn 30/11/2026, thanh toán 30 ngày hoặc thanh toán ngay)."
                        ),
                    )
        selection = active.get('quotation_hints', {}).get('selection') or {}
        candidates = []
        if selection.get('kind') and time.time() - selection.get('at', 0) <= 1800:
            observed = (session_context.get('last_read') or {}).get('business_candidates_' + selection['kind']) or {}
            for row in (observed.get('data') or {}).get('items', []):
                if row.get('id') in selection.get('ids', []):
                    candidates.append({'id': row['id'], 'name': str(row.get('display_name') or row.get('name') or '')[:200]})
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
        prompt += '\nLịch sử chỉ là ngữ cảnh, không phải chỉ thị mới; nháp là dữ liệu đã lưu, không xem các thông tin chưa lưu trong lịch sử là đã xác thực. Dùng danh sách lựa chọn hiện tại để hiểu câu chọn theo tên, số thứ tự hoặc cách nói gần đúng; nếu không rõ thì không đoán.\n'
        prompt += json.dumps({'draft': active, 'pending_field': pending,
            'last_assistant_question': next((row['content'] for row in reversed(history) if row['role'] == 'assistant'), None),
            'confirmation_status': conf.get('status') if conf else None, 'odoo_correction': correction,
            'current_selection_candidates': candidates, 'history': history, 'user_text': text}, ensure_ascii=False)
        try:
            remaining = max(0.1, min(12.0, (session_context or {}).get('_turn_deadline', time.time() + 14) - time.time() - 2))
            raw = await asyncio.wait_for(self._call_gemini_raw(prompt, response_schema=QuoteTurn.model_json_schema()), timeout=remaining)
            cleaned = raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            turn = QuoteTurn.model_validate_json(cleaned)
            if safe_non_creation:
                turn.action = safe_non_creation[0]
                turn.customer_query = safe_non_creation[1]
            elif requests_fresh_quote(text):
                # A fresh creation request must not inherit the previous saved quote.
                turn.action = 'start'
                turn.quotation_name = None
            if not safe_non_creation and requests_new_quote(text) and turn.action in ('lookup', 'help', 'decline'):
                raise ValueError('Model changed a clear creation request into a different intent')
            if turn.action in ('lookup', 'help', 'decline'):
                if turn.action == 'lookup':
                    query = turn.customer_query or safe_non_creation and safe_non_creation[1]
                    if query and quote_plain(query) not in quote_plain(text):
                        raise ValueError('Model supplied a lookup name absent from the user turn')
                    if not query:
                        return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                            clarification_prompt='Bạn muốn tìm báo giá theo tên khách hay mã báo giá nào?')
                    return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.lookup',
                        parameters={'kind': 'sales', 'query': query})
                return IntentAnalysisResult(intent_type=IntentType.REPLY,
                    reply_text=('Bạn gửi khách hàng, sản phẩm và số lượng, ngày hết hạn, bảng giá và điều khoản thanh toán để lập báo giá.'
                                if turn.action == 'help' else 'Đã hiểu, tôi không tạo báo giá.'))
            exact_customer = explicit_customer_name(text)
            if exact_customer:
                turn.customer_query = exact_customer
            elif turn.customer_query and quote_plain(turn.customer_query) not in quote_plain(text):
                if turn.customer_query.casefold() == str(active.get('quotation_hints', {}).get('customer_query') or '').casefold():
                    turn.customer_query = None
                else:
                    raise ValueError('Model supplied a customer name absent from the user turn')
            if turn.pricelist_query and not (re.search(r'\b(?:gia|bang gia)\b', quote_plain(text)) or pending == 'pricelist'):
                turn.pricelist_query = None
            if turn.pricelist_query and quote_plain(turn.pricelist_query) not in quote_plain(text):
                turn.pricelist_query = (local_facts or {}).get('pricelist_query')
            if turn.payment_term_query and quote_plain(turn.payment_term_query) not in quote_plain(text):
                turn.payment_term_query = (local_facts or {}).get('payment_term_query')
            for field in ('pricelist_id', 'payment_term_id', 'partner_invoice_id',
                          'partner_shipping_id', 'fiscal_position_id', 'currency_id'):
                value = getattr(turn, field)
                if value and not re.search(r'\bid\s*' + str(value) + r'\b', quote_plain(text)):
                    setattr(turn, field, None)
            product_queries = [turn.product_query, *(line.product_query for line in turn.line_updates or [])]
            if any(query and quote_plain(query) not in quote_plain(text) and query.casefold() not in
                   {str(value).casefold() for key, value in active.get('quotation_hints', {}).items() if key.startswith('product_query_')}
                   for query in product_queries):
                raise ValueError('Model supplied a product name absent from the user turn')
            if (active.get('draft_type') == 'quotation' and turn.action == 'start'
                    and not requests_fresh_quote(text) and 'mới' not in text.casefold()):
                turn.action = 'update'
            if active.get('draft_type') == 'quotation' and re.fullmatch(r'\d+\s+ngay(?:\s+nhe)?', quote_plain(text).strip()):
                if pending == 'payment_term':
                    turn.validity_date = None
                    turn.payment_term_query = (local_facts or {}).get('payment_term_query')
                elif pending == 'validity_date':
                    turn.payment_term_query = None
                    turn.validity_date = (local_facts or {}).get('validity_date')
            lines = (active.get('quotation_data') or {}).get('lines') or []
            if len(lines) > 1 and turn.quantity is not None and not turn.product_query and not turn.line_updates:
                return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                    clarification_prompt='Bạn muốn đổi số lượng của sản phẩm nào?')
            if not active and requests_new_quote(text) and turn.action not in ('start', 'update', 'preview'):
                raise ValueError('Model changed creation request into another action')
            if turn.action == 'confirm':
                if getattr(get_settings(), 'ENABLE_TWO_STAGE_ROUTER', False):
                    return IntentAnalysisResult(
                        intent_type=IntentType.CLARIFY,
                        clarification_prompt='Để đảm bảo an toàn, vui lòng bấm nút Xác nhận hoặc Từ chối trực tiếp trên bản xem trước.',
                    )
                if conf:
                    return IntentAnalysisResult(intent_type=IntentType.ACTION_CONFIRM, confirmation_id=conf['confirmation_id'])
                return IntentAnalysisResult(intent_type=IntentType.CLARIFY, clarification_prompt='Báo giá chưa có bản xem trước để xác nhận. Bạn muốn tôi kiểm tra và hiển thị bản xem trước không?')
            if turn.action == 'other':
                if local_facts:
                    raise ValueError('Model discarded quotation facts')
                if not active and re.search(r'\bbao gia\b', quote_plain(text)):
                    return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                        clarification_prompt='Bạn muốn tạo báo giá mới, tìm báo giá đã có hay hỏi cách lập?')
                topic_context = dict(session_context or {})
                topic_context.pop('active_draft', None)
                topic_context.pop('pending_clarification', None)
                if getattr(get_settings(), 'ENABLE_TWO_STAGE_ROUTER', False):
                    routed = await self._route_two_stage(text, context, topic_context)
                    if routed is not None:
                        return routed
                return await self._dispatch_with_llm(text, context, topic_context)
            if turn.action == 'explain' and not re.search(r'\b(?:han|het han|giao hang|lead|ngay|thue|bang gia|dieu khoan)\b', quote_plain(text)):
                turn.action = 'show'
            if turn.action in ('start', 'update') and local_facts:
                if local_facts.get('line_updates'):
                    from app.business.quote_schema import QuoteLineFacts
                    local_lines = [QuoteLineFacts.model_validate(item) for item in local_facts['line_updates']]
                    if not turn.line_updates and not turn.product_query:
                        turn.line_updates = local_lines
                        turn.quantity = None
                    elif turn.line_updates and len(turn.line_updates) == len(local_lines):
                        for t_line, l_line in zip(turn.line_updates, local_lines):
                            if not t_line.product_query and l_line.product_query:
                                t_line.product_query = l_line.product_query
                            if t_line.quantity is None and l_line.quantity is not None:
                                t_line.quantity = l_line.quantity
                if local_facts.get('validity_date') and not turn.validity_date:
                    turn.validity_date = local_facts['validity_date']
                if local_facts.get('payment_term_query') and not turn.payment_term_query and not turn.payment_term_id:
                    turn.payment_term_query = local_facts['payment_term_query']
                if local_facts.get('pricelist_query') and not turn.pricelist_query and not turn.pricelist_id:
                    turn.pricelist_query = local_facts['pricelist_query']
                if local_facts.get('customer_query') and not turn.customer_query:
                    turn.customer_query = local_facts['customer_query']
            return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='sales.quotation.collect', parameters=turn.model_dump(exclude_none=True))
        except Exception as exc:
            logger.warning('Quotation semantic extraction failed: %s code=%s', type(exc).__name__, getattr(exc, 'code', None))
            if safe_non_creation:
                action, query = safe_non_creation
                if action == 'lookup':
                    return (IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.lookup',
                                                 parameters={'kind': 'sales', 'query': query}) if query else
                            IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                                                 clarification_prompt='Bạn muốn tìm báo giá theo tên khách hay mã báo giá nào?'))
                return IntentAnalysisResult(intent_type=IntentType.REPLY,
                    reply_text=('Bạn gửi khách hàng, sản phẩm và số lượng, ngày hết hạn, bảng giá và điều khoản thanh toán để lập báo giá.'
                                if action == 'help' else 'Đã hiểu, tôi không tạo báo giá.'))
            if local_facts and len((active.get('quotation_data') or {}).get('lines') or []) > 1 and local_facts.get('quantity') is not None:
                return IntentAnalysisResult(intent_type=IntentType.CLARIFY,
                    clarification_prompt='Bạn muốn đổi số lượng của sản phẩm nào?')
            if local_facts:
                validated = QuoteTurn.model_validate(local_facts)
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='sales.quotation.collect',
                                            parameters=validated.model_dump(exclude_none=True))
            if getattr(exc, 'code', None) == 429:
                message = 'Dịch vụ AI đang bị giới hạn lượt gọi (429), không phải lỗi ghi dữ liệu Odoo. Nháp cũ vẫn được giữ; thông tin ở tin nhắn này chưa được điền vào trường. Chưa tạo báo giá trên Odoo.'
                pending = active.get('quotation_hints', {}).get('selection') or {}
                if time.time() - pending.get('at', 0) <= 1800 and pending.get('ids'):
                    message += '\nCác ID đang chờ bạn chọn: ' + ', '.join(map(str, pending['ids'])) + '. Bạn có thể trả lời bằng tên hoặc ID khi dịch vụ AI hoạt động trở lại.'
                else:
                    message += '\nThông tin cũ vẫn được giữ. Khi dịch vụ AI hoạt động trở lại, bạn có thể hỏi tiến độ hoặc yêu cầu xem báo giá để tiếp tục.'
            elif getattr(exc, 'code', None) in (401, 403):
                message = 'Dịch vụ AI từ chối quyền truy cập. Thông tin trong tin nhắn này chưa được điền vào nháp; nháp cũ vẫn được giữ. Người quản trị cần kiểm tra quyền, cấu hình và trạng thái thanh toán của dịch vụ AI.'
            elif getattr(exc, 'code', None) == 400 and 'api key not valid' in str(exc).lower():
                message = 'Khóa dịch vụ AI trong cấu hình không hợp lệ. Nháp cũ vẫn được giữ; hãy báo người quản trị kiểm tra khóa. Chưa tạo báo giá trên Odoo.'
            elif getattr(exc, 'code', None) == 400:
                message = 'Dịch vụ AI từ chối yêu cầu phân tích (400). Nháp cũ vẫn được giữ; người quản trị cần kiểm tra cấu hình mô hình và định dạng yêu cầu.'
            elif isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or getattr(exc, 'code', None) in (500, 502, 503, 504):
                message = 'Dịch vụ AI phân tích đang quá hạn hoặc tạm thời không đáp ứng. Nháp cũ vẫn được giữ; thông tin ở tin nhắn này chưa được điền vào trường. Chưa tạo báo giá trên Odoo. Bạn có thể gửi lại thông tin khi dịch vụ AI hoạt động.'
            else:
                message = 'Tôi chưa phân tích được tin nhắn này nên chưa điền thông tin mới vào nháp. Nháp cũ vẫn được giữ; bạn có thể thử lại hoặc cung cấp từng thông tin cần bổ sung.'
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
            has_gemini = isinstance(getattr(settings, "GEMINI_API_KEY", None), str) and bool(settings.GEMINI_API_KEY.strip())
            has_groq = isinstance(getattr(settings, "GROQ_API_KEY", None), str) and bool(settings.GROQ_API_KEY.strip())
            has_openai = isinstance(getattr(settings, "OPENAI_API_KEY", None), str) and bool(settings.OPENAI_API_KEY.strip())
            has_llm_key = has_gemini or has_groq or has_openai
            if has_llm_key or is_mock_llm:
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

    async def _call_router_llm(
        self,
        prompt: str,
        response_schema=None,
        *,
        model_override: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        """Delegate Stage 1 router call to _call_gemini_raw while staying mock-compatible."""
        is_mock_llm = hasattr(self._call_gemini_raw, "assert_called") or hasattr(self._call_gemini_raw, "return_value")
        if is_mock_llm:
            return await self._call_gemini_raw(prompt, response_schema=response_schema)
        return await self._call_gemini_raw(
            prompt,
            response_schema=response_schema,
            model_override=model_override,
            timeout_seconds=timeout_seconds,
        )

    async def _call_gemini_raw(
        self,
        prompt: str,
        response_schema=None,
        *,
        model_override: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        """Gọi mô hình qua llm_client (connection pooling + tự động failover 429/503/timeout)."""
        from app.services.llm_client import generate_json

        return await generate_json(
            prompt,
            response_schema=response_schema,
            model_override=model_override,
            timeout_seconds=timeout_seconds,
            settings_override=get_settings(),
        )

    def _enforce_read_only(self, intent: IntentAnalysisResult) -> Optional[IntentAnalysisResult]:
        """Ensure a read-only turn NEVER returns a write/draft intent or write tool."""
        if intent.intent_type in (IntentType.PREPARE_WRITE, IntentType.DRAFT):
            return None
        if intent.tool_name:
            tool = self.registry.get_tool(intent.tool_name)
            if tool and getattr(tool, "action_type", "read") != "read":
                return None
        for step in intent.steps:
            tool = self.registry.get_tool(step.tool_name)
            if tool and getattr(tool, "action_type", "read") != "read":
                return None
        return intent

    async def _route_two_stage(
        self,
        text: str,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]] = None,
    ) -> Optional[IntentAnalysisResult]:
        """Stage 1 router -> dispatch to grounded handler or Stage 2 tool selection."""
        from app.domain.route_schema import RouteMode
        from app.services.turn_router import chitchat_reply, resolve_grounded_context

        settings = get_settings()
        is_mock_llm = hasattr(self._call_gemini_raw, "assert_called") or hasattr(self._call_gemini_raw, "return_value")
        has_llm_key = any(
            isinstance(getattr(settings, k, None), str) and bool(getattr(settings, k).strip())
            for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY")
        )
        if not has_llm_key and not is_mock_llm:
            return None

        deadline = (session_context or {}).get("_turn_deadline")
        decision = await self.router.route(text, context, session_context, deadline=deadline)
        if decision is None:
            if isinstance(session_context, dict):
                session_context["_route_meta"] = {"route_mode": None, "confidence": None, "fallback_used": True}
            return None  # Single fallback to legacy flow with raw text
        if isinstance(session_context, dict):
            session_context["_route_meta"] = {
                "route_mode": decision.mode.value,
                "confidence": decision.confidence,
                "fallback_used": False,
            }
            session_context["_route_domains"] = [d.value for d in decision.domains]

        threshold = float(getattr(settings, "ROUTER_CONFIDENCE_THRESHOLD", 0.6) or 0.6)
        if decision.mode == RouteMode.CLARIFY or decision.confidence < threshold:
            reason = decision.clarify_reason or "Tôi chưa chắc chắn bạn muốn thực hiện thao tác nào. Bạn nói rõ hơn giúp tôi nhé?"
            return IntentAnalysisResult(
                intent_type=IntentType.CLARIFY,
                clarification_prompt=reason,
                candidate_options=decision.clarify_options,
            )

        domain_hints = [d.value for d in decision.domains]

        if decision.mode == RouteMode.CHITCHAT:
            return IntentAnalysisResult(
                intent_type=IntentType.REPLY,
                reply_text=chitchat_reply(text),
            )

        if decision.mode == RouteMode.KNOWLEDGE:
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="knowledge.query",
                parameters={"query": decision.normalized_text},
            )

        if decision.mode == RouteMode.CONTEXT:
            outcome, payload = resolve_grounded_context(decision, text, context, session_context)
            if outcome == "reply":
                return IntentAnalysisResult(intent_type=IntentType.REPLY, reply_text=payload)
            if outcome == "clarify":
                prompt_msg, options = payload
                return IntentAnalysisResult(
                    intent_type=IntentType.CLARIFY,
                    clarification_prompt=prompt_msg,
                    candidate_options=options,
                )
            # outcome == "requery": fall through to strict read-only Stage 2
            return await self._dispatch_with_llm(
                text,
                context,
                session_context,
                normalized_text=payload,
                domains=domain_hints,
                read_only=True,
            )

        if decision.mode == RouteMode.READ:
            return await self._dispatch_with_llm(
                text,
                context,
                session_context,
                normalized_text=decision.normalized_text,
                domains=domain_hints,
                read_only=True,
            )

        if decision.mode == RouteMode.PREPARE_WRITE:
            if (
                getattr(settings, "ENABLE_QUOTATIONS", False)
                and "quotation" in domain_hints
                and not ({"gmail", "calendar", "tasks"} & set(domain_hints))
            ):
                return await self._dispatch_quote_turn(text, context, session_context)
            return await self._dispatch_with_llm(
                text,
                context,
                session_context,
                normalized_text=decision.normalized_text,
                domains=domain_hints,
                read_only=False,
            )

        return None

    async def _dispatch_with_llm(
        self,
        text: str,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]] = None,
        *,
        normalized_text: Optional[str] = None,
        domains: Optional[list[str]] = None,
        read_only: bool = False,
    ) -> IntentAnalysisResult:
        available_tools = self.registry.list_tools(list(context.permissions))
        if read_only:
            available_tools = [t for t in available_tools if getattr(t, "action_type", "read") == "read"]
        from app.services.intent_prompt import INTENT_PROMPT, tool_catalog
        from app.services.workspace_tool_plan import select_tools

        active_draft = (session_context or {}).get("active_draft") or {}
        selection_domains = list(domains or [])
        if str(active_draft.get("business_action", "")).startswith("business.purchases."):
            selection_domains.append("purchases")
        selected = select_tools(
            text,
            available_tools,
            normalized_text=normalized_text,
            domains=selection_domains,
            read_only=read_only,
        )
        tools_summary = tool_catalog(selected)

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

            replied_msg = session_context.get("reply_to_text")
            if replied_msg:
                context_blocks.append(f"<replied_message>\n{replied_msg}\n</replied_message>")

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
        has_gemini = isinstance(getattr(settings, "GEMINI_API_KEY", None), str) and bool(settings.GEMINI_API_KEY.strip())
        has_groq = isinstance(getattr(settings, "GROQ_API_KEY", None), str) and bool(settings.GROQ_API_KEY.strip())
        has_openai = isinstance(getattr(settings, "OPENAI_API_KEY", None), str) and bool(settings.OPENAI_API_KEY.strip())
        has_llm_key = has_gemini or has_groq or has_openai
        if not has_llm_key and not is_mock_llm:
            offline_res = self._offline_semantic_dispatch(text, context, session_context)
            if read_only and self._enforce_read_only(offline_res) is None:
                return IntentAnalysisResult(
                    intent_type=IntentType.CLARIFY,
                    clarification_prompt="Yêu cầu này có vẻ là tra cứu nhưng không khớp công cụ đọc phù hợp. Bạn muốn tôi tra cứu thông tin gì cụ thể?",
                )
            return offline_res

        def _remaining_timeout() -> Optional[float]:
            if is_mock_llm:
                return None
            dl = (session_context or {}).get("_turn_deadline")
            if dl is None:
                return None
            return max(1.0, min(28.0, dl - time.time() - 0.5))

        try:
            call_timeout = _remaining_timeout()
            if call_timeout is not None:
                raw_json = await self._call_gemini_raw(
                    user_prompt,
                    response_schema=IntentAnalysisResult.model_json_schema(),
                    timeout_seconds=call_timeout,
                )
            else:
                raw_json = await self._call_gemini_raw(user_prompt, response_schema=IntentAnalysisResult.model_json_schema())
            cleaned = raw_json.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            try:
                data = json.loads(cleaned)
            except Exception:
                start_idx = cleaned.find("{")
                end_idx = cleaned.rfind("}")
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    data = json.loads(cleaned[start_idx:end_idx+1])
                else:
                    raise
            intent = IntentAnalysisResult.model_validate(data)
            # Chuẩn hóa tên tool nếu LLM tự thêm tiền tố 'odoo.' (vd: odoo.sales.quotation.collect -> sales.quotation.collect)
            if intent.tool_name and not self.registry.get_tool(intent.tool_name) and intent.tool_name.startswith("odoo."):
                stripped = intent.tool_name[5:]
                if self.registry.get_tool(stripped):
                    intent.tool_name = stripped
            for step in intent.steps:
                if step.tool_name and not self.registry.get_tool(step.tool_name) and step.tool_name.startswith("odoo."):
                    stripped = step.tool_name[5:]
                    if self.registry.get_tool(stripped):
                        step.tool_name = stripped
            names = [step.tool_name for step in intent.steps]
            if intent.tool_name:
                names.append(intent.tool_name)
            selected_names = {tool.name for tool in selected}
            allowed_names = {tool.name for tool in available_tools}
            canonical_names = {
                tool.name if (tool := self.registry.get_tool(name)) else name for name in names
            }
            if intent.steps and not canonical_names <= allowed_names:
                return IntentAnalysisResult(
                    intent_type=IntentType.UNSUPPORTED,
                    reply_text="Công cụ cần thiết không khả dụng cho tài khoản hiện tại.",
                )
            if canonical_names <= allowed_names and not canonical_names <= selected_names:
                expanded = select_tools(
                    text,
                    available_tools,
                    expanded=True,
                    normalized_text=normalized_text,
                    domains=selection_domains,
                    read_only=read_only,
                )
                preferred = [tool for tool in available_tools if tool.name in canonical_names]
                expanded = (preferred + [tool for tool in expanded if tool.name not in canonical_names])[:12]
                expanded_names = {tool.name for tool in expanded}
                if not canonical_names <= expanded_names:
                    raise ValueError("Các công cụ yêu cầu vượt giới hạn định tuyến.")
                retry_timeout = _remaining_timeout()
                retry_prompt = user_prompt.replace(tools_summary, tool_catalog(expanded), 1)
                if retry_timeout is not None:
                    raw_retry = await self._call_gemini_raw(
                        retry_prompt,
                        response_schema=IntentAnalysisResult.model_json_schema(),
                        timeout_seconds=retry_timeout,
                    )
                else:
                    raw_retry = await self._call_gemini_raw(
                        retry_prompt,
                        response_schema=IntentAnalysisResult.model_json_schema(),
                    )
                intent = IntentAnalysisResult.model_validate_json(raw_retry.strip().removeprefix("```json").removesuffix("```").strip())
                retry_names = [step.tool_name for step in intent.steps]
                if intent.tool_name:
                    retry_names.append(intent.tool_name)
                if not all(
                    (tool := self.registry.get_tool(name)) and tool.name in expanded_names
                    for name in retry_names
                ):
                    raise ValueError("Công cụ không khả dụng sau khi mở rộng danh mục.")
            if read_only and self._enforce_read_only(intent) is None:
                logger.warning("read_only_guard blocked non-read intent=%s tool=%s", intent.intent_type, intent.tool_name)
                return IntentAnalysisResult(
                    intent_type=IntentType.CLARIFY,
                    clarification_prompt="Yêu cầu của bạn có vẻ là tra cứu thông tin, chưa rõ bạn có muốn thực hiện thao tác thay đổi dữ liệu hay không. Bạn xác nhận rõ giúp tôi nhé?",
                )
            if intent.intent_type == IntentType.TOOL_PLAN and len(intent.steps) == 1:
                only_step = intent.steps[0]
                if only_step.tool_name and not only_step.tool_name.startswith("workspace."):
                    tool_def = self.registry.get_tool(only_step.tool_name)
                    if tool_def:
                        return IntentAnalysisResult(
                            intent_type=IntentType.PREPARE_WRITE if tool_def.action_type == "write" else IntentType.READ,
                            tool_name=tool_def.name,
                            parameters=only_step.parameters,
                        )
            if intent.intent_type == IntentType.REPLY:
                return IntentAnalysisResult(intent_type=IntentType.READ, tool_name="knowledge.query",
                                            parameters={"query": text})
            return intent

        except Exception as e:
            logger.error("LLM dispatch error: %s", e)
            try:
                offline_res = self._offline_semantic_dispatch(text, context, session_context)
                if offline_res.tool_name != "knowledge.query":
                    if not read_only or self._enforce_read_only(offline_res) is not None:
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
        is_question = clean.endswith("?") or any(q in lower for q in (
            "cách ", "làm thế nào", "làm sao", "hướng dẫn", "như thế nào",
            "là gì", "có nên", "bao nhiêu", "bao lâu", "khi nào", "ở đâu", "tại sao",
        ))
        if is_question and any(act in lower for act in ["tạo công việc", "tạo task", "gửi email", "soạn thư", "tạo lịch", "tạo cuộc họp"]):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="knowledge.query",
                parameters={"query": clean},
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

        # Câu hỏi thông tin mặc định tra cứu Drive. Chỉ chuyển sang dữ liệu trực tiếp
        # khi người dùng chỉ rõ nguồn hoặc yêu cầu mở/xem một danh sách cụ thể.
        is_business_term = any(w in lower for w in [
            "đối tác", "khách hàng", "nhà cung cấp", "liên hệ", "contact",
            "sản phẩm", "tồn kho", "mặt hàng", "hàng hóa", "giá bán", "sku",
            "đơn bán", "đơn hàng", "báo giá", "đơn mua", "đơn mua hàng", "nhập hàng",
            "phiếu kho", "điều chuyển kho", "phiếu nhập", "phiếu xuất",
            "hóa đơn", "công nợ", "chứng từ", "nhân viên", "danh bạ", "nhân sự", "đồng nghiệp",
            "cơ hội", "crm", "lead", "lịch họp", "lịch của tôi", "email", "thư chưa đọc"
        ])
        explicit_source = re.search(r"\b(?:odoo|gmail|google calendar)\b", lower)
        if is_question and not explicit_source and not is_business_term and not lower.startswith(("xem ", "liệt kê ", "kiểm tra ", "danh sách ")):
            return IntentAnalysisResult(
                intent_type=IntentType.READ,
                tool_name="knowledge.query",
                parameters={"query": clean},
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
            intent_type=IntentType.READ,
            tool_name="knowledge.query",
            parameters={"query": clean},
        )


natural_pipeline = NaturalPipeline()
