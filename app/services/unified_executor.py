# -*- coding: utf-8 -*-
"""Unified Executor for AI Agent.

Contract:
- Single execution entry point receiving IntentAnalysisResult.
- Maps canonical tool names from ToolRegistry directly to handlers.
- Parameter normalization (recipient -> to, due_date -> due_at).
- Strict schema & permission validation before any execution.
- Safe two-step write execution with cancellation recovery and unknown_result ledger.
- Status reconciliation audit with Gateway.
"""

import asyncio
import json
import logging
import secrets
import time
from datetime import datetime, UTC, timedelta
from typing import Any, Optional

import sqlite3
from app.domain.gateway_context import GatewayPermission, GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.domain.tool_registry import ToolRegistry, global_tool_registry
from app.providers.gateway_calendar import GatewayCalendarProvider
from app.providers.gateway_crm import GatewayCRMProvider
from app.providers.gateway_gmail import GatewayGmailProvider
from app.providers.tasks.local import LocalTaskProvider, execute_task_confirmation_atomically
from app.services.confirmation_service import confirmation_service
from app.services.context_resolver import (
    resolve_relative_time_window,
    resolve_target_from_context,
    validate_and_adjust_event_times,
)
from app.services.conversation_history_store import conversation_history_store
from app.services.gateway_client import gateway_client
from app.services.morning_briefing_service import morning_briefing_service

logger = logging.getLogger(__name__)


class UnifiedExecutor:
    """Bộ thực thi ý định thống nhất không phụ thuộc vào parser phụ."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        local_tasks: Optional[LocalTaskProvider] = None,
    ) -> None:
        self.registry = registry or global_tool_registry
        self.calendar_provider = GatewayCalendarProvider()
        self.gmail_provider = GatewayGmailProvider()
        self.crm_provider = GatewayCRMProvider()
        self.local_tasks = local_tasks or LocalTaskProvider()

    async def execute(
        self,
        intent: IntentAnalysisResult,
        context: GatewayUserContext,
        chat_id: int,
        telegram_user_id: int,
        session_context: dict[str, Any],
        deadline: float,
        user_info: dict[str, Any],
    ) -> list[str]:
        """Thực thi IntentAnalysisResult với deadline kiểm soát toàn turn."""
        now_ts = time.time()
        remaining_time = max(0.5, deadline - now_ts)

        # 1. REPLY Intent
        if intent.intent_type == IntentType.REPLY:
            return [intent.reply_text or ""]

        # 2. CLARIFY Intent
        if intent.intent_type == IntentType.CLARIFY:
            clarify_text = intent.clarification_prompt or "Vui lòng làm rõ yêu cầu."
            if intent.candidate_options:
                options_str = "\n".join(f"• {opt}" for opt in intent.candidate_options)
                clarify_text += f"\n\nDanh sách lựa chọn:\n{options_str}"
            conversation_history_store.set_pending_clarification(
                tenant=context.tenant,
                user_id=context.user_id,
                chat_id=chat_id,
                binding_id=context.telegram_binding_id,
                clarification_data={
                    "prompt": clarify_text,
                    "missing_fields": intent.missing_fields,
                    "candidate_options": intent.candidate_options,
                    "tool_name": intent.tool_name,
                    "parameters": intent.parameters,
                },
                database=context.database,
            )
            return [clarify_text]

        # 3. UNSUPPORTED Intent
        if intent.intent_type == IntentType.UNSUPPORTED:
            return [intent.reply_text or "⚠️ Yêu cầu ngoài phạm vi hỗ trợ."]

        # 4. DRAFT Intent
        if intent.intent_type == IntentType.DRAFT:
            return self._handle_draft(intent, context, chat_id)

        # 5. ACTION_CONFIRM Intent
        if intent.intent_type == IntentType.ACTION_CONFIRM:
            return await self._handle_confirm(intent, context, chat_id, telegram_user_id, remaining_time)

        # 6. ACTION_CANCEL Intent
        if intent.intent_type == IntentType.ACTION_CANCEL:
            return self._handle_cancel(intent, context, chat_id)

        # 7. ACTION_STATUS Intent (Đối soát trạng thái với Gateway)
        if intent.intent_type == IntentType.ACTION_STATUS:
            return await self._handle_status(intent, context, chat_id, telegram_user_id, remaining_time)

        # 8. READ Intent
        if intent.intent_type == IntentType.READ:
            return await self._handle_read(intent, context, chat_id, user_info, remaining_time)

        # 9. PREPARE_WRITE Intent
        if intent.intent_type == IntentType.PREPARE_WRITE:
            return await self._handle_prepare_write(intent, context, chat_id)

        return ["⚠️ Không xác định được loại ý định xử lý."]

    # =========================================================================
    # DRAFT HANDLER
    # =========================================================================
    def _handle_draft(
        self, intent: IntentAnalysisResult, context: GatewayUserContext, chat_id: int
    ) -> list[str]:
        if not intent.draft:
            return ["⚠️ Yêu cầu bản nháp không hợp lệ (thiếu payload)."]

        draft_dict = intent.draft.model_dump()
        saved = conversation_history_store.set_active_draft(
            context.tenant,
            context.user_id,
            chat_id,
            context.telegram_binding_id,
            draft_dict,
            database=context.database,
            expected_version=intent.draft.expected_version,
            expected_draft_id=intent.draft.expected_draft_id,
        )
        if not saved:
            err = getattr(conversation_history_store, "last_draft_error", None)
            if err == "LOCK_TIMEOUT":
                return ["⚠️ Hệ thống đang bận xử lý một thao tác khác trên bản nháp. Vui lòng thử lại sau giây lát."]
            return ["⚠️ Bản nháp đã được chỉnh sửa ở một tác vụ khác. Không thể ghi đè bản nháp mới hơn."]

        draft_id = saved.get("draft_id")
        executing_warning = ""
        if draft_id:
            # Hủy toàn bộ pending preview của phiên bản cũ
            confirmation_service.cancel_pending_draft_confirmations(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=context.telegram_binding_id or "default",
                draft_id=draft_id,
            )
            # Kiểm tra xem có preview nào của draft_id đang executing không
            with sqlite3.connect(confirmation_service.db_path) as conn:
                r = conn.execute(
                    "SELECT confirmation_id FROM pending_confirmations WHERE draft_id = ? AND status = 'executing'",
                    (draft_id,),
                ).fetchone()
                if r:
                    executing_warning = "\n\n⚠️ **Cảnh báo:** Một yêu cầu gửi thư trước đó đang được thực thi trên Gateway. Việc chỉnh sửa bản nháp lúc này không thể hủy bỏ yêu cầu đã gửi."

        ver = saved.get("version", intent.draft.version)
        rec = saved.get("recipient") or saved.get("to") or "Chưa chỉ định"
        subj = saved.get("subject") or "Không tiêu đề"
        body = saved.get("content") or saved.get("body") or ""

        return [
            f"📝 **Bản nháp email (v{ver}) đã được lưu:**\n"
            f"• Người nhận: `{rec}`\n"
            f"• Tiêu đề: {subj}\n"
            f"• Nội dung: {body}\n\n"
            f"💡 Bạn có thể nhắn *'viết ngắn hơn'*, *'sửa tiêu đề thành...'* hoặc *'gửi bản vừa soạn'* để tạo bản xem trước xác nhận.{executing_warning}"
        ]

    def _handle_provider_read_error(self, resp: Any, domain_name: str) -> Optional[list[str]]:
        """Chuẩn hóa và phân biệt các loại lỗi provider (quyền, kết nối, timeout, nguồn)."""
        if not isinstance(resp, dict):
            return None
        raw_status = str(resp.get("status", "")).upper()
        if not raw_status or raw_status in ["OK", "SUCCESS", "COMPLETED"]:
            return None
        if raw_status == "PERMISSION_DENIED":
            return [f"❌ Bạn chưa được cấp hoặc đã bị thu hồi quyền truy cập {domain_name}."]
        if raw_status in ["NOT_CONNECTED", "RECONNECT_REQUIRED"]:
            return [f"🔗 Dịch vụ {domain_name} chưa được kết nối hoặc cần liên kết lại tài khoản để sử dụng."]
        if raw_status in ["TIMEOUT"]:
            return [f"⏳ Quá thời gian chờ phản hồi từ {domain_name}. Vui lòng thử lại sau giây lát."]
        if raw_status in ["ERROR", "SERVICE_ERROR"]:
            msg = resp.get("message") or "Lỗi từ dịch vụ ngoài"
            return [f"⚠️ Lỗi từ dịch vụ {domain_name}: {msg}"]
        return None

    # =========================================================================
    # READ HANDLERS
    # =========================================================================
    async def _handle_read(
        self,
        intent: IntentAnalysisResult,
        context: GatewayUserContext,
        chat_id: int,
        user_info: dict[str, Any],
        remaining_time: float,
    ) -> list[str]:
        tool_name = intent.tool_name
        tool_def = self.registry.get_tool(tool_name) if tool_name else None
        if not tool_def:
            return [f"⚠️ Công cụ đọc `{tool_name}` không tồn tại trong hệ thống."]

        if tool_def.required_permission and not context.has_permission(tool_def.required_permission):
            return [f"⚠️ Bạn chưa được cấp quyền `{tool_def.required_permission}` để tra cứu thông tin này."]

        params = dict(intent.parameters)
        timeout = max(1.0, min(10.0, remaining_time - 1.0))

        if tool_name == "identity.whoami":
            from app.api.v1.internal import format_whoami
            return [format_whoami(context)]

        if tool_name == "connections.status":
            from app.api.v1.internal import format_connection_status
            conns = await gateway_client.get_user_connections(context.user_id)
            return [format_connection_status(user_info, conns)]

        if tool_name == "briefing.today":
            text = await morning_briefing_service.generate_real_briefing(user_info)
            return [text]

        if tool_name == "tasks.personal.list":
            if not context.has_permission(GatewayPermission.TASKS_READ):
                return [
                    "⚠️ Tài khoản của bạn chưa được cấp quyền tra cứu công việc (`tasks.read`). "
                    "Vui lòng liên hệ quản trị viên hoặc kiểm tra quyền trên Cổng Gateway."
                ]
            if not context.tenant or not context.database or context.odoo_uid is None:
                return [
                    "⚠️ Không thể tải danh sách công việc: Thông tin định danh không đầy đủ "
                    "để xác định chủ sở hữu (yêu cầu tenant, database và Odoo UID)."
                ]
            tasks = await self.local_tasks.list_tasks(filters=params, owner_key=context.owner_key)
            if not isinstance(tasks, list):
                tasks = []
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "tasks", tasks, database=context.database
            )
            from app.api.v1.internal import format_task_list
            return [format_task_list(tasks)]

        if tool_name == "calendar.events.list":
            if not context.has_permission(GatewayPermission.CALENDAR_READ):
                return ["⚠️ Tài khoản của bạn chưa được cấp quyền tra cứu Google Calendar (`calendar.read`)."]
            time_min = params.get("time_min")
            time_max = params.get("time_max")
            if not time_min or not time_max:
                st, et = resolve_relative_time_window(None, timezone_str=context.timezone, allow_default_7_days=True, half_open=True)
                time_min = st.isoformat() if st else None
                time_max = et.isoformat() if et else None

            events_resp = await self.calendar_provider.get_events(
                user_id=context.user_id, time_min=time_min or "", time_max=time_max or ""
            )
            err = self._handle_provider_read_error(events_resp, "Google Calendar")
            if err:
                return err

            events = events_resp if isinstance(events_resp, list) else events_resp.get("events", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "calendar", events, database=context.database
            )
            return [self._format_calendar_events(events, time_min, time_max)]

        if tool_name == "gmail.messages.unread":
            if not context.has_permission(GatewayPermission.GMAIL_READ):
                return ["⚠️ Tài khoản của bạn chưa được cấp quyền đọc thư Gmail (`gmail.read`)."]
            msgs_resp = await self.gmail_provider.get_unread(user_id=context.user_id)
            err = self._handle_provider_read_error(msgs_resp, "Gmail")
            if err:
                return err

            msgs = msgs_resp if isinstance(msgs_resp, list) else msgs_resp.get("messages", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "gmail", msgs, database=context.database
            )
            return [self._format_unread_emails(msgs)]

        if tool_name == "crm.leads.search":
            if not context.has_permission(GatewayPermission.CRM_READ):
                return ["⚠️ Tài khoản của bạn chưa được cấp quyền tra cứu cơ hội CRM (`crm.read`)."]
            query = params.get("query") or ""
            leads_resp = await self.crm_provider.search_leads(user_id=context.user_id, search_term=query)
            err = self._handle_provider_read_error(leads_resp, "Odoo CRM")
            if err:
                return err

            leads = leads_resp if isinstance(leads_resp, list) else leads_resp.get("leads", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "crm", leads, database=context.database
            )
            return [self._format_crm_leads(leads, query)]

        if tool_name == "odoo.partners.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 10)
            domain = []
            if query:
                domain = ["|", ("name", "ilike", query), ("email", "ilike", query)]
            res = await gateway_client.query_odoo(
                user_id=context.user_id,
                model="res.partner",
                domain=domain,
                limit=limit,
            )
            err = self._handle_odoo_query_error(res, "đối tác/khách hàng")
            if err:
                return err
            items = res.get("items", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.partners", items, database=context.database
            )
            return [self._format_odoo_partners(items, query)]

        if tool_name == "odoo.products.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 10)
            domain = []
            if query:
                domain = ["|", ("name", "ilike", query), ("default_code", "ilike", query)]
            res = await gateway_client.query_odoo(
                user_id=context.user_id,
                model="product.product",
                domain=domain,
                limit=limit,
            )
            err = self._handle_odoo_query_error(res, "sản phẩm")
            if err:
                return err
            items = res.get("items", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.products", items, database=context.database
            )
            return [self._format_odoo_products(items, query)]

        if tool_name == "odoo.sales.search":
            query = (params.get("query") or "").strip()
            state = (params.get("state") or "").strip()
            limit = int(params.get("limit") or 10)
            domain = []
            if query:
                domain.append(("name", "ilike", query))
            if state:
                domain.append(("state", "=", state))
            res = await gateway_client.query_odoo(
                user_id=context.user_id,
                model="sale.order",
                domain=domain,
                limit=limit,
            )
            err = self._handle_odoo_query_error(res, "đơn bán hàng")
            if err:
                return err
            items = res.get("items", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.sales", items, database=context.database
            )
            return [self._format_odoo_sales(items, query)]

        if tool_name == "odoo.purchases.search":
            query = (params.get("query") or "").strip()
            state = (params.get("state") or "").strip()
            limit = int(params.get("limit") or 10)
            domain = []
            if query:
                domain.append(("name", "ilike", query))
            if state:
                domain.append(("state", "=", state))
            res = await gateway_client.query_odoo(
                user_id=context.user_id,
                model="purchase.order",
                domain=domain,
                limit=limit,
            )
            err = self._handle_odoo_query_error(res, "đơn mua hàng")
            if err:
                return err
            items = res.get("items", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.purchases", items, database=context.database
            )
            return [self._format_odoo_purchases(items, query)]

        if tool_name == "odoo.inventory.search":
            query = (params.get("query") or "").strip()
            state = (params.get("state") or "").strip()
            limit = int(params.get("limit") or 10)
            domain = []
            if query:
                domain = ["|", ("name", "ilike", query), ("origin", "ilike", query)]
            if state:
                domain.append(("state", "=", state))
            res = await gateway_client.query_odoo(
                user_id=context.user_id,
                model="stock.picking",
                domain=domain,
                limit=limit,
            )
            err = self._handle_odoo_query_error(res, "kho bãi")
            if err:
                return err
            items = res.get("items", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.inventory", items, database=context.database
            )
            return [self._format_odoo_inventory(items, query)]

        if tool_name == "odoo.invoices.search":
            query = (params.get("query") or "").strip()
            pay_state = (params.get("payment_state") or "").strip()
            limit = int(params.get("limit") or 10)
            domain = []
            if query:
                domain.append(("name", "ilike", query))
            if pay_state:
                domain.append(("payment_state", "=", pay_state))
            res = await gateway_client.query_odoo(
                user_id=context.user_id,
                model="account.move",
                domain=domain,
                limit=limit,
            )
            err = self._handle_odoo_query_error(res, "hóa đơn")
            if err:
                return err
            items = res.get("items", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.invoices", items, database=context.database
            )
            return [self._format_odoo_invoices(items, query)]

        if tool_name == "odoo.employees.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 10)
            domain = []
            if query:
                domain = ["|", ("name", "ilike", query), ("work_email", "ilike", query)]
            res = await gateway_client.query_odoo(
                user_id=context.user_id,
                model="hr.employee",
                domain=domain,
                limit=limit,
            )
            err = self._handle_odoo_query_error(res, "danh bạ nhân sự")
            if err:
                return err
            items = res.get("items", [])
            conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.employees", items, database=context.database
            )
            return [self._format_odoo_employees(items, query)]

        return [f"⚠️ Chưa có handler triển khai cho công cụ đọc `{tool_name}`."]

    # =========================================================================
    # PREPARE WRITE HANDLERS (Preview generation)
    # =========================================================================
    async def _handle_prepare_write(
        self, intent: IntentAnalysisResult, context: GatewayUserContext, chat_id: int
    ) -> list[str]:
        tool_name = intent.tool_name
        tool_def = self.registry.get_tool(tool_name) if tool_name else None
        if not tool_def:
            return [f"⚠️ Công cụ ghi `{tool_name}` không tồn tại trong hệ thống."]

        if tool_def.required_permission and not context.has_permission(tool_def.required_permission):
            return [f"⚠️ Bạn chưa được cấp quyền `{tool_def.required_permission}` để thực hiện thao tác này."]

        # Chuẩn hóa tham số tại adapter
        params = self._normalize_write_parameters(intent.parameters)
        binding_id = context.telegram_binding_id or "default"

        # Tasks create
        if tool_name in ["tasks.personal.create", "tasks.create"]:
            title = params.get("title")
            if not title:
                return ["⚠️ Thiếu tiêu đề công việc để tạo. Vui lòng cung cấp tiêu đề."]
            due_at = params.get("due_at") or params.get("due_date")
            priority = params.get("priority") or "medium"
            conf_id, preview = confirmation_service.create_confirmation(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action="tasks.personal.create",
                arguments={
                    "title": title,
                    "description": params.get("description", ""),
                    "due_at": due_at,
                    "priority": priority,
                    "timezone": params.get("timezone", context.timezone),
                    "project": params.get("project"),
                    "tags": params.get("tags", []),
                },
                preview_text=(
                    f"📋 **Bản xem trước tạo công việc cá nhân:**\n"
                    f"• Tiêu đề: {title}\n"
                    f"• Hạn chót: {due_at or 'Không có'}\n"
                    f"• Ưu tiên: {priority}"
                ),
                owner_key=context.owner_key,
                telegram_user_id=context.telegram_user_id,
            )
            return [preview]

        # Tasks update
        if tool_name in ["tasks.personal.update", "tasks.update"]:
            task_id = params.get("task_id") or params.get("id")
            if not task_id:
                return ["⚠️ Thiếu mã công việc (task_id) để cập nhật."]

            # Lấy snapshot công việc hiện tại để đối soát
            snapshot = None
            try:
                task_obj = await self.local_tasks.get_task(task_id, owner_key=context.owner_key)
                snapshot = dict(task_obj)
            except Exception:
                pass

            if not snapshot:
                return [f"⚠️ Không tìm thấy công việc `{task_id}` hoặc bạn không có quyền sở hữu."]

            old_title = snapshot.get("title") if snapshot else "Công việc"
            new_title = params.get("title") or old_title

            diff_lines = [f"📋 **Bản xem trước cập nhật công việc `{task_id}`**:"]
            if new_title != old_title:
                diff_lines.append(f"• Tiêu đề: `{old_title}` ➔ **{new_title}**")
            else:
                diff_lines.append(f"• Tiêu đề: **{old_title}**")

            if params.get("due_at") or params.get("due_date"):
                new_due = params.get("due_at") or params.get("due_date")
                old_due = snapshot.get("due_at") if snapshot else "Không có"
                diff_lines.append(f"• Hạn chót: `{old_due}` ➔ **{new_due}**")

            conf_id, preview = confirmation_service.create_confirmation(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action="tasks.personal.update",
                arguments=params,
                preview_text="\n".join(diff_lines),
                owner_key=context.owner_key,
                target_snapshot=snapshot,
                telegram_user_id=context.telegram_user_id,
            )
            return [preview]

        # Tasks done
        if tool_name in ["tasks.personal.done", "tasks.done", "tasks.complete", "tasks.personal.complete"]:
            task_id = params.get("task_id") or params.get("id")
            if not task_id:
                return ["⚠️ Thiếu mã công việc (task_id) để hoàn thành."]

            snapshot = None
            try:
                task_obj = await self.local_tasks.get_task(task_id, owner_key=context.owner_key)
                snapshot = dict(task_obj)
            except Exception:
                pass

            if not snapshot:
                return [f"⚠️ Không tìm thấy công việc `{task_id}` hoặc bạn không có quyền sở hữu."]

            task_title = snapshot.get("title") if snapshot else f"Công việc {task_id}"

            conf_id, preview = confirmation_service.create_confirmation(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action="tasks.personal.done",
                arguments={"task_id": task_id},
                preview_text=f"📋 **Bản xem trước hoàn thành công việc `{task_id}`**:\n• Tiêu đề: **{task_title}**",
                owner_key=context.owner_key,
                target_snapshot=snapshot,
                telegram_user_id=context.telegram_user_id,
            )
            return [preview]

        # Calendar create
        if tool_name in ["calendar.events.create", "calendar.create"]:
            summary = params.get("summary")
            st_raw = params.get("start_time")
            et_raw = params.get("end_time")
            if not summary or not st_raw:
                return ["⚠️ Thiếu tiêu đề hoặc thời gian bắt đầu cho sự kiện lịch."]
            try:
                start_dt, end_dt = validate_and_adjust_event_times(
                    start_time=st_raw, end_time=et_raw, timezone_str=context.timezone
                )
            except ValueError as ve:
                return [f"⚠️ Thời gian không hợp lệ: {ve}"]

            conf_id, preview = confirmation_service.create_confirmation(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action="calendar.create",
                arguments={
                    "summary": summary,
                    "start_time": start_dt.isoformat(),
                    "end_time": end_dt.isoformat(),
                    "description": params.get("description", ""),
                },
                preview_text=(
                    f"📅 **Bản xem trước tạo sự kiện Lịch Google:**\n"
                    f"• Tiêu đề: {summary}\n"
                    f"• Bắt đầu: {start_dt.strftime('%H:%M %d/%m/%Y')}\n"
                    f"• Kết thúc: {end_dt.strftime('%H:%M %d/%m/%Y')}"
                ),
                owner_key=context.owner_key,
                telegram_user_id=context.telegram_user_id,
            )
            return [preview]

        # Calendar update (có snapshot, diff hiển thị, giữ duration khi chỉ dời start_time)
        if tool_name in ["calendar.events.update", "calendar.update"]:
            event_id = params.get("event_id")
            if not event_id:
                session = conversation_history_store.get_session(
                    tenant=context.tenant, user_id=context.user_id, chat_id=chat_id, binding_id=binding_id, database=context.database
                )
                last_cal = session.get("last_read", {}).get("calendar", [])
                if len(last_cal) == 1:
                    event_id = last_cal[0].get("id")
                    params["event_id"] = event_id
                elif len(last_cal) > 1:
                    opts = "\n".join(f"• `{ev.get('id')}`: {ev.get('summary')}" for ev in last_cal)
                    return [f"⚠️ Bạn có nhiều cuộc họp gần đây, vui lòng chỉ rõ mã cuộc họp:\n{opts}"]
                else:
                    return ["⚠️ Thiếu mã cuộc họp (event_id) để cập nhật."]

            snapshot = None
            try:
                raw_snap = await self.calendar_provider.client.get_calendar_event(user_id=context.user_id, event_id=event_id)
                if raw_snap and raw_snap.get("status") != "ERROR":
                    snapshot = raw_snap
            except Exception:
                pass

            if not snapshot:
                session = conversation_history_store.get_session(
                    tenant=context.tenant, user_id=context.user_id, chat_id=chat_id, binding_id=binding_id, database=context.database
                )
                last_cal = session.get("last_read", {}).get("calendar", [])
                for ev in last_cal:
                    if ev.get("id") == event_id:
                        snapshot = ev
                        break

            if not snapshot:
                snapshot = {"id": event_id, "summary": params.get("summary") or "Sự kiện lịch"}

            old_summary = snapshot.get("summary") or "(Không có tiêu đề)"
            old_start = self._safe_time(snapshot.get("start") or snapshot.get("start_time"))
            old_end = self._safe_time(snapshot.get("end") or snapshot.get("end_time"))
            etag = snapshot.get("etag")

            # Xử lý dời lịch: nếu chỉ có start_time mới, giữ thời lượng từ snapshot
            new_st_str = params.get("start_time")
            new_et_str = params.get("end_time")

            if new_st_str:
                try:
                    new_start_dt = datetime.fromisoformat(new_st_str.replace("Z", "+00:00"))
                except Exception:
                    new_start_dt = datetime.now(UTC)

                if new_et_str:
                    try:
                        new_end_dt = datetime.fromisoformat(new_et_str.replace("Z", "+00:00"))
                    except Exception:
                        new_end_dt = new_start_dt + timedelta(hours=1)
                else:
                    orig_duration = timedelta(hours=1)
                    if old_start and old_end:
                        try:
                            o_st = datetime.fromisoformat(old_start.replace("Z", "+00:00"))
                            o_et = datetime.fromisoformat(old_end.replace("Z", "+00:00"))
                            if o_et > o_st:
                                orig_duration = o_et - o_st
                        except Exception:
                            pass
                    new_end_dt = new_start_dt + orig_duration

                if new_end_dt <= new_start_dt:
                    return ["⚠️ Thời gian kết thúc phải diễn ra sau thời gian bắt đầu."]

                params["start_time"] = new_start_dt.isoformat()
                params["end_time"] = new_end_dt.isoformat()

            new_summary = params.get("summary") or old_summary

            diff_lines = [f"📅 **Bản xem trước cập nhật sự kiện Lịch Google (`{event_id}`):**"]
            if new_summary != old_summary:
                diff_lines.append(f"• Tiêu đề: `{old_summary}` ➔ **{new_summary}**")
            else:
                diff_lines.append(f"• Tiêu đề: **{old_summary}**")

            if new_st_str:
                diff_lines.append(f"• Bắt đầu: `{old_start}` ➔ **{params['start_time']}**")
                diff_lines.append(f"• Kết thúc: `{old_end}` ➔ **{params['end_time']}**")
            else:
                diff_lines.append(f"• Thời gian: {old_start} - {old_end}")

            target_snap_data = {
                "event_id": event_id,
                "summary": old_summary,
                "start": old_start,
                "end": old_end,
                "etag": etag,
            }

            conf_id, preview = confirmation_service.create_confirmation(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action="calendar.update",
                arguments=params,
                preview_text="\n".join(diff_lines),
                owner_key=context.owner_key,
                target_snapshot=target_snap_data,
                telegram_user_id=context.telegram_user_id,
            )
            return [preview]

        # Gmail send (gắn draft_id và draft_version từ session active_draft)
        if tool_name in ["gmail.messages.send", "gmail.send"]:
            to_addr = params.get("to")
            subj = params.get("subject") or "Không tiêu đề"
            body = params.get("body")
            if not to_addr or not body:
                return ["⚠️ Thiếu địa chỉ người nhận (to) hoặc nội dung thư (body)."]

            draft_id = params.get("draft_id")
            draft_ver = params.get("draft_version")
            if not draft_id:
                sess = conversation_history_store.get_session(
                    tenant=context.tenant, user_id=context.user_id, chat_id=chat_id, binding_id=binding_id, database=context.database
                )
                act_dr = sess.get("active_draft")
                if act_dr:
                    draft_id = act_dr.get("draft_id")
                    draft_ver = act_dr.get("version", 1)

            conf_id, preview = confirmation_service.create_confirmation(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action="gmail.send",
                arguments={"to": to_addr, "subject": subj, "body": body},
                preview_text=(
                    f"✉️ **Bản xem trước gửi thư Gmail:**\n"
                    f"• Người nhận: `{to_addr}`\n"
                    f"• Tiêu đề: {subj}\n"
                    f"• Nội dung: {body}"
                ),
                owner_key=context.owner_key,
                draft_id=draft_id,
                draft_version=draft_ver,
                telegram_user_id=context.telegram_user_id,
            )
            return [preview]

        # CRM create
        if tool_name in ["crm.leads.create", "crm.create"]:
            name = params.get("name")
            if not name:
                return ["⚠️ Thiếu tên cơ hội/khách hàng để tạo CRM Lead."]
            conf_id, preview = confirmation_service.create_confirmation(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action="crm.create",
                arguments=params,
                preview_text=(
                    f"💼 **Bản xem trước tạo cơ hội Odoo CRM:**\n"
                    f"• Tên: {name}\n"
                    f"• SĐT: {params.get('phone', 'Chưa có')}\n"
                    f"• Email: {params.get('email', 'Chưa có')}"
                ),
                owner_key=context.owner_key,
                telegram_user_id=context.telegram_user_id,
            )
            return [preview]

        # CRM update (có snapshot, diff hiển thị, lưu write_date)
        if tool_name in ["crm.leads.update", "crm.update"]:
            lead_id = params.get("lead_id") or params.get("id")
            if not lead_id:
                return ["⚠️ Thiếu ID cơ hội (lead_id) để cập nhật."]

            snapshot = None
            try:
                raw_snap = await self.crm_provider.client.get_crm_lead(user_id=context.user_id, lead_id=int(lead_id))
                if raw_snap and raw_snap.get("status") != "ERROR":
                    snapshot = raw_snap
            except Exception:
                pass

            old_name = snapshot.get("name") if snapshot else f"Cơ hội #{lead_id}"
            old_rev = snapshot.get("expected_revenue") if snapshot else None
            write_date = snapshot.get("write_date") if snapshot else None

            diff_lines = [f"💼 **Bản xem trước cập nhật cơ hội CRM (`{lead_id}`):**"]
            new_name = params.get("name")
            new_rev = params.get("expected_revenue")
            if new_name and new_name != old_name:
                diff_lines.append(f"• Tên: `{old_name}` ➔ **{new_name}**")
            else:
                diff_lines.append(f"• Tên: **{old_name}**")

            if new_rev is not None:
                old_rev_str = f"{old_rev:,.0f} VND" if old_rev is not None else "Chưa có"
                diff_lines.append(f"• Doanh thu dự kiến: `{old_rev_str}` ➔ **{float(new_rev):,.0f} VND**")

            target_snap_data = {
                "lead_id": lead_id,
                "name": old_name,
                "expected_revenue": old_rev,
                "expected_write_date": write_date,
            }

            conf_id, preview = confirmation_service.create_confirmation(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action="crm.write",
                arguments=params,
                preview_text="\n".join(diff_lines),
                owner_key=context.owner_key,
                target_snapshot=target_snap_data,
                telegram_user_id=context.telegram_user_id,
            )
            return [preview]

        return [f"⚠️ Chưa có handler chuẩn bị cho công cụ ghi `{tool_name}`."]

    # =========================================================================
    # CONFIRM EXECUTION HANDLER (Safe dispatch & Cancellation recovery)
    # =========================================================================
    async def _handle_confirm(
        self,
        intent: IntentAnalysisResult,
        context: GatewayUserContext,
        chat_id: int,
        telegram_user_id: int,
        remaining_time: float,
    ) -> list[str]:
        conf_id = intent.confirmation_id or intent.parameters.get("confirmation_id")
        binding_id = context.telegram_binding_id or "default"

        if not conf_id:
            pending_list = confirmation_service.list_pending_confirmations(
                user_id=context.user_id, chat_id=chat_id, telegram_binding_id=binding_id
            )
            if len(pending_list) == 1:
                conf_id = pending_list[0]["confirmation_id"]
            elif len(pending_list) > 1:
                options = "\n".join(f"• `/confirm {c['confirmation_id']}` ({c['action']})" for c in pending_list)
                return [f"⚠️ Bạn đang có nhiều thao tác chờ xác nhận. Vui lòng chọn mã cụ thể:\n{options}"]
            else:
                return ["⚠️ Không tìm thấy thao tác nào đang chờ xác nhận."]

        # 1. Claim nguyên tử và lấy dữ liệu đóng băng
        ok, claim_msg, conf_data = confirmation_service.claim_and_validate(
            confirmation_id=conf_id,
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=binding_id,
        )
        if not ok or not conf_data:
            return [f"⚠️ {claim_msg}"]

        action = conf_data["action"]
        arguments = conf_data.get("arguments") or {}
        exec_token = conf_data.get("execution_token")
        op_id = conf_data.get("operation_id")
        norm_action = action.lower()

        # 2. Kiểm tra lại quyền nghiệp vụ và chủ sở hữu TRƯỚC KHI THỰC THI (không để kẹt executing)
        is_task_action = norm_action.startswith("tasks.")
        if is_task_action:
            if not context.has_permission(GatewayPermission.TASKS_WRITE):
                confirmation_service.mark_failed(conf_id, "Quyền tasks.write bị từ chối hoặc đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Bạn không có quyền thực hiện thao tác công việc (yêu cầu quyền tasks.write)."]

            if not context.owner_key:
                confirmation_service.mark_failed(conf_id, "Thiếu thông tin owner_key của người dùng hiện tại.", execution_token=exec_token)
                return ["❌ Không thể xác định tài khoản người dùng để thực hiện thao tác công việc."]

            if conf_data.get("owner_key") and conf_data["owner_key"] != context.owner_key:
                confirmation_service.mark_failed(conf_id, "Owner key không khớp với bản xem trước ban đầu.", execution_token=exec_token)
                return ["❌ Bạn không có quyền thực hiện công việc của tài khoản khác."]

            if not conf_data.get("owner_key"):
                confirmation_service.mark_failed(conf_id, "Bản ghi cũ thiếu thông tin owner cần thiết, yêu cầu tạo preview mới.", execution_token=exec_token)
                return ["⚠️ Bản ghi xác nhận cũ thiếu thông tin người sở hữu hợp lệ. Vui lòng tạo yêu cầu mới."]

            # Chặn preview update/done cũ thiếu revision
            if norm_action in ["tasks.update", "tasks.personal.update", "tasks.done", "tasks.complete", "tasks.personal.done", "tasks.personal.complete"]:
                target_snap = conf_data.get("target_snapshot")
                if not target_snap or not isinstance(target_snap, dict) or target_snap.get("revision") is None:
                    confirmation_service.mark_failed(
                        conf_id,
                        "Bản xem trước cũ thiếu thông tin phiên bản (revision). Vui lòng tạo bản xem trước mới.",
                        execution_token=exec_token,
                    )
                    return ["⚠️ Công việc đã thay đổi sau bản xem trước, cần tạo yêu cầu mới."]

            # Thực thi Task mutation và mark completed trong CÙNG TRANSACTION SQLite
            try:
                res = execute_task_confirmation_atomically(
                    db_path=confirmation_service.db_path,
                    confirmation_id=conf_id,
                    execution_token=exec_token,
                    action=action,
                    arguments=arguments,
                    owner_key=context.owner_key,
                    target_snapshot=conf_data.get("target_snapshot"),
                )
                return [f"✅ **Thực hiện thành công:** {conf_data.get('preview_text', action)}"]
            except Exception as e:
                confirmation_service.mark_failed(conf_id, error_message=str(e), execution_token=exec_token)
                if "thay đổi sau bản xem trước" in str(e).lower() or "revision" in str(e).lower():
                    return ["⚠️ Công việc đã thay đổi sau bản xem trước, cần tạo yêu cầu mới."]
                return [f"❌ Không thể thực hiện công việc: {e}"]

        # Kiểm tra tính hợp lệ của Draft Version nếu confirmation tạo từ Draft
        if conf_data.get("draft_id"):
            expected_ver = conf_data.get("draft_version")
            if expected_ver is None:
                confirmation_service.mark_failed(
                    conf_id,
                    "Bản xem trước thư thiếu thông tin phiên bản (draft_version). Vui lòng tạo bản xem trước mới.",
                    execution_token=exec_token,
                )
                return ["⚠️ Bản xem trước không còn hợp lệ do thiếu phiên bản bản thảo. Vui lòng tạo bản xem trước mới."]

            sess = conversation_history_store.get_session(
                tenant=context.tenant,
                user_id=context.user_id,
                chat_id=chat_id,
                binding_id=binding_id,
                database=context.database,
            )
            act_dr = sess.get("active_draft") if sess else None

            # 1. Draft bị xóa, hết TTL hoặc không tồn tại
            if not act_dr:
                confirmation_service.mark_failed(
                    conf_id,
                    "Bản nháp liên kết với bản xem trước này đã bị xóa hoặc không còn tồn tại.",
                    execution_token=exec_token,
                )
                return ["⚠️ Bản nháp liên kết đã bị xóa hoặc không còn tồn tại. Thao tác gửi thư bị hủy bỏ."]

            # 2. Draft khác ID
            if act_dr.get("draft_id") != conf_data["draft_id"]:
                confirmation_service.mark_failed(
                    conf_id,
                    f"Bản nháp hiện tại ({act_dr.get('draft_id')}) không khớp với bản xem trước ({conf_data['draft_id']}).",
                    execution_token=exec_token,
                )
                return ["⚠️ Bản nháp hiện tại đã thay đổi sang một bản thảo khác. Vui lòng tạo bản xem trước mới."]

            # 3. Draft khác version (dù cao hơn hay thấp hơn)
            curr_ver = act_dr.get("version")
            if curr_ver != expected_ver:
                confirmation_service.mark_failed(
                    conf_id,
                    f"Phiên bản bản nháp hiện tại (v{curr_ver}) không khớp chính xác với phiên bản xem trước (v{expected_ver}).",
                    execution_token=exec_token,
                )
                return [f"⚠️ Bản nháp đã được chỉnh sửa (phiên bản hiện tại: v{curr_ver}, bản xem trước: v{expected_ver}). Mã xác nhận cũ không còn hiệu lực, vui lòng tạo bản xem trước mới."]

        # Kiểm tra quyền cho các dịch vụ ngoài qua Gateway
        if norm_action.startswith("calendar."):
            if not context.has_permission(GatewayPermission.CALENDAR_WRITE):
                confirmation_service.mark_failed(conf_id, "Quyền calendar.write bị từ chối hoặc đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Bạn không có quyền thao tác trên Google Calendar (thiếu quyền calendar.write)."]
        elif norm_action.startswith("gmail."):
            if not context.has_permission(GatewayPermission.GMAIL_SEND):
                confirmation_service.mark_failed(conf_id, "Quyền gmail.send bị từ chối hoặc đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Bạn không có quyền gửi email qua Gmail (thiếu quyền gmail.send)."]
        elif norm_action.startswith("crm."):
            if not (context.has_permission(GatewayPermission.CRM_WRITE) or context.has_permission(GatewayPermission.CRM_CREATE)):
                confirmation_service.mark_failed(conf_id, "Quyền CRM bị từ chối hoặc đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Bạn không có quyền tạo hoặc sửa đổi CRM trên Odoo."]

        # 3. Xử lý Gateway Action qua Cổng Gateway với dispatch bền vững
        if not op_id:
            confirmation_service.mark_unknown(conf_id, "Bản ghi thiếu operation_id, chuyển sang đối soát thủ công.", execution_token=exec_token)
            return ["⚠️ Thao tác thiếu mã đối soát (operation_id) hợp lệ, chuyển sang đối soát thủ công."]

        # Ghi nhận dispatch_started_at trước khi gọi mạng; chỉ dispatch nếu token và lease còn hiệu lực
        if not confirmation_service.mark_dispatched(conf_id, execution_token=exec_token):
            return ["⚠️ Phiên xử lý thao tác đã hết hạn hoặc không còn hiệu lực. Vui lòng thử lại."]

        call_timeout = max(1.0, min(14.0, remaining_time - 1.0))

        try:
            gw_res = await gateway_client.execute_action(
                user_id=context.user_id,
                operation_id=op_id,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                action=action,
                arguments=arguments,
                timeout=call_timeout,
            )

            status = gw_res.get("status")
            if status == "completed":
                result = gw_res.get("result") or {}
                confirmation_service.mark_completed(conf_id, result=result, execution_token=exec_token)
                if action == "calendar.create":
                    summary = result.get("summary") or arguments.get("summary") or "Sự kiện lịch"
                    st = self._safe_time(result.get("start") or arguments.get("start_time"))
                    et = self._safe_time(result.get("end") or arguments.get("end_time"))
                    link = result.get("htmlLink") or result.get("link") or "N/A"
                    return [
                        f"✅ **Đã tạo thành công sự kiện Lịch Google:**\n"
                        f"- Tiêu đề: **{summary}**\n"
                        f"- Bắt đầu: {st}\n"
                        f"- Kết thúc: {et}\n"
                        f"- Liên kết: {link}"
                    ]
                elif action == "calendar.update":
                    summary = result.get("summary") or arguments.get("summary") or "Đã cập nhật"
                    st = self._safe_time(result.get("start") or arguments.get("start_time"))
                    et = self._safe_time(result.get("end") or arguments.get("end_time"))
                    link = result.get("htmlLink") or result.get("link") or "N/A"
                    return [
                        f"✅ **Đã cập nhật sự kiện Lịch Google thành công:**\n"
                        f"- Tiêu đề: **{summary}**\n"
                        f"- Bắt đầu: {st}\n"
                        f"- Kết thúc: {et}\n"
                        f"- Liên kết: {link}"
                    ]
                elif action == "gmail.send":
                    return [
                        f"✅ **Đã gửi thư Gmail thành công:**\n"
                        f"- Mã thư: `{result.get('id', 'N/A')}`\n"
                        f"- Trạng thái: Đã gửi qua máy chủ thư của Google"
                    ]
                elif action in ["crm.create", "crm.leads.create"]:
                    lead_id = result.get("id") or result.get("lead", {}).get("id") or "N/A"
                    lead_name = result.get("name") or result.get("lead", {}).get("name") or arguments.get("name")
                    return [
                        f"✅ **Đã tạo cơ hội CRM mới trên Odoo:**\n"
                        f"- ID: `{lead_id}`\n"
                        f"- Tên cơ hội: **{lead_name}**"
                    ]
                elif action in ["crm.write", "crm.update", "crm.leads.update"]:
                    lead_id = result.get("id") or arguments.get("lead_id") or "N/A"
                    return [
                        f"✅ **Đã cập nhật cơ hội CRM thành công trên Odoo:**\n"
                        f"- ID: `{lead_id}`"
                    ]
                return [f"✅ **Thao tác hoàn tất thành công!**\n• Hành động: `{action}`\n• Mã theo dõi: `{op_id}`"]

            elif status == "in_progress":
                return [
                    f"⏳ **Thao tác đang được xử lý trên Gateway:**\n"
                    f"• Mã xác nhận: `{conf_id}`\n"
                    f"• Mã thao tác: `{op_id}`\n\n"
                    f"Thao tác đang trong quá trình thực hiện. Bạn có thể kiểm tra kết quả sau bằng lệnh: `/status {conf_id}`"
                ]

            elif status == "unknown_result":
                confirmation_service.mark_unknown(conf_id, reason=gw_res.get("message", "Chưa rõ kết quả từ Gateway"), execution_token=exec_token)
                return [
                    f"⚠️ **Chưa rõ kết quả thực hiện:** Yêu cầu đã được gửi tới Gateway nhưng phản hồi bị gián đoạn hoặc quá thời gian.\n"
                    f"• Mã xác nhận: `{conf_id}`\n"
                    f"• Mã thao tác: `{op_id}`\n\n"
                    f"🔍 Để kiểm tra trạng thái thực tế mà không tạo lại thao tác, hãy gửi: `/status {conf_id}`"
                ]

            elif status == "CONFLICT":
                confirmation_service.mark_failed(conf_id, error_message=gw_res.get("message", "Xung đột phiên bản"), execution_token=exec_token)
                return [
                    f"⚠️ **Tài nguyên đã bị thay đổi kể từ lúc xem trước:**\n"
                    f"Để tránh ghi đè dữ liệu mới hơn của người khác, thao tác đã bị dừng. Vui lòng tạo yêu cầu xem trước mới."
                ]

            else:
                err_msg = gw_res.get("message") or "Lỗi dịch vụ ngoài"
                confirmation_service.mark_failed(conf_id, error_message=err_msg, execution_token=exec_token)
                return [f"❌ **Thao tác thất bại:** {err_msg}"]

        except (asyncio.CancelledError, asyncio.TimeoutError) as ce:
            logger.warning("Turn cancelled/timed out during gateway action dispatch: %s", ce)
            confirmation_service.mark_unknown(conf_id, reason=f"Hết thời gian turn hoặc hủy sau dispatch: {ce}", execution_token=exec_token)
            return [
                f"⚠️ **Yêu cầu đã gửi nhưng hết thời gian chờ phản hồi:**\n"
                f"• Mã xác nhận: `{conf_id}`\n"
                f"• Mã thao tác: `{op_id}`\n\n"
                f"Hệ thống không tự động gửi lại để tránh trùng lặp. Vui lòng kiểm tra trạng thái bằng lệnh: `/status {conf_id}`"
            ]

        except Exception as e:
            logger.error("Unexpected error executing gateway action: %s", e, exc_info=True)
            confirmation_service.mark_unknown(conf_id, reason=f"Lỗi kết nối sau dispatch: {e}", execution_token=exec_token)
            return [
                f"⚠️ **Sự cố kết nối sau khi gửi yêu cầu:**\n"
                f"• Mã xác nhận: `{conf_id}`\n"
                f"• Mã thao tác: `{op_id}`\n\n"
                f"Vui lòng kiểm tra lại trạng thái sau giây lát bằng lệnh: `/status {conf_id}`"
            ]

    # =========================================================================
    # CANCEL HANDLER
    # =========================================================================
    def _handle_cancel(
        self, intent: IntentAnalysisResult, context: GatewayUserContext, chat_id: int
    ) -> list[str]:
        conf_id = intent.confirmation_id or intent.parameters.get("confirmation_id")
        binding_id = context.telegram_binding_id or "default"

        if not conf_id:
            pending_list = confirmation_service.list_pending_confirmations(
                user_id=context.user_id, chat_id=chat_id, telegram_binding_id=binding_id
            )
            if len(pending_list) == 1:
                conf_id = pending_list[0]["confirmation_id"]
            elif len(pending_list) > 1:
                options = "\n".join(f"• `/cancel {c['confirmation_id']}` ({c['action']})" for c in pending_list)
                return [f"⚠️ Bạn đang có nhiều thao tác chờ. Vui lòng chọn mã để hủy:\n{options}"]
            else:
                return ["⚠️ Không tìm thấy thao tác nào đang chờ xác nhận để hủy."]

        success, msg = confirmation_service.cancel_confirmation(
            confirmation_id=conf_id,
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=binding_id,
        )
        return [msg if success else f"⚠️ {msg}"]

    # =========================================================================
    # STATUS AUDIT HANDLER (Reconciliation with Gateway)
    # =========================================================================
    async def _handle_status(
        self,
        intent: IntentAnalysisResult,
        context: GatewayUserContext,
        chat_id: int,
        telegram_user_id: int,
        remaining_time: float,
    ) -> list[str]:
        target_id = intent.confirmation_id or intent.parameters.get("confirmation_id")
        binding_id = context.telegram_binding_id or "default"

        if not target_id:
            return ["⚠️ Vui lòng cung cấp mã xác nhận hoặc mã thao tác để kiểm tra. Ví dụ: `/status c_123`"]

        target_id = target_id.strip()

        # 1. Tìm trong SQLite Confirmation Service
        conf = confirmation_service.get_confirmation(target_id)
        if conf:
            # KIỂM TRA QUYỀN TRUY CẬP: user_id, chat_id, telegram_binding_id, owner_key
            if not confirmation_service.validate_access(
                conf=conf,
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                owner_key=context.owner_key,
            ):
                return ["⚠️ Không tìm thấy hoặc bạn không có quyền truy cập thông tin thao tác này."]

            cid = conf["confirmation_id"]
            # Bổ sung mapping telegram_user_id cho confirmation cũ nếu chưa có
            if conf.get("telegram_user_id") is None and telegram_user_id:
                confirmation_service.update_telegram_user_id(cid, telegram_user_id)
                conf["telegram_user_id"] = telegram_user_id

            c_status = conf.get("status")

            if c_status == "executing":
                now_ts = datetime.now(UTC).timestamp()
                lease = conf.get("lease_expires_at")
                if lease and now_ts < lease:
                    return [f"⏳ Thao tác `{target_id}` đang được thực thi trên hệ thống..."]
                # Lease thiếu hoặc đã hết hạn: thu hồi claim và chuyển sang unknown_result trước khi đối soát
                confirmation_service.recover_expired_leases()
                conf = confirmation_service.get_confirmation(cid) or conf
                c_status = conf.get("status")

            if c_status == "completed":
                return [f"✅ Thao tác `{target_id}` đã được thực thi thành công."]
            if c_status == "cancelled":
                return [f"ℹ️ Thao tác `{target_id}` đã được hủy bỏ an toàn."]
            if c_status == "failed":
                return [f"❌ Thao tác `{target_id}` đã thất bại: {conf.get('error_message')}"]

            if c_status == "unknown_result":
                op_id = conf.get("operation_id")
                tele_uid = conf.get("telegram_user_id") or telegram_user_id
                if not op_id or not tele_uid:
                    missing_info = []
                    if not op_id:
                        missing_info.append("mã operation ID")
                    if not tele_uid:
                        missing_info.append("định danh Telegram đã xác minh")
                    return [
                        f"⚠️ Thao tác `{target_id}` đang ở trạng thái chưa rõ kết quả và thiếu {', '.join(missing_info)} để tự động đối soát.\n"
                        f"Vui lòng liên hệ quản trị viên hoặc kiểm tra trực tiếp trên Cổng Gateway với mã xác nhận `{cid}`."
                    ]

                gw_check = await gateway_client.get_action_status(
                    user_id=context.user_id,
                    operation_id=op_id,
                    telegram_user_id=tele_uid,
                    chat_id=chat_id,
                    telegram_binding_id=binding_id,
                    timeout=max(1.0, min(5.0, remaining_time - 1.0)),
                )
                gw_st = gw_check.get("status")
                if gw_st == "completed":
                    confirmation_service.reconcile_confirmation_result(
                        cid, status="completed", result=gw_check.get("result"), operation_id=op_id
                    )
                    return [f"✅ Thao tác `{target_id}` đã hoàn tất thành công trên Cổng Gateway!"]
                elif gw_st == "failed":
                    confirmation_service.reconcile_confirmation_result(
                        cid, status="failed", error_message=gw_check.get("message", "Thất bại trên Gateway"), operation_id=op_id
                    )
                    return [f"❌ Thao tác `{target_id}` đã thất bại trên Cổng Gateway: {gw_check.get('message')}"]
                elif gw_st == "not_found":
                    return [
                        f"⚠️ Thao tác `{target_id}` chưa được ghi nhận trên ledger Gateway. "
                        f"Để an toàn, hệ thống không tự động thử lại."
                    ]
                elif gw_st == "in_progress":
                    return [f"⏳ Thao tác `{target_id}` hiện đang được xử lý trên Gateway."]
                else:
                    return [
                        f"⏳ Thao tác `{target_id}` hiện có trạng thái: `{gw_st}` ({gw_check.get('message', 'Đang xử lý')})."
                    ]

        # 2. Tra cứu trực tiếp với Gateway ledger nếu là operation_id
        if target_id.startswith("op_"):
            gw_check = await gateway_client.get_action_status(
                user_id=context.user_id,
                operation_id=target_id,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                timeout=max(1.0, min(5.0, remaining_time - 1.0)),
            )
            gw_st = gw_check.get("status")
            if gw_st == "completed":
                return [f"✅ Thao tác `{target_id}` đã hoàn tất thành công trên Cổng Gateway."]
            elif gw_st == "failed":
                return [f"❌ Thao tác `{target_id}` đã thất bại trên Cổng Gateway: {gw_check.get('message')}"]
            elif gw_st == "not_found":
                return [f"⚠️ Không tìm thấy bản ghi cho mã `{target_id}` trên Cổng Gateway."]
            elif gw_st == "permission_denied":
                return ["⚠️ Không tìm thấy hoặc bạn không có quyền truy cập thông tin thao tác này."]
            else:
                return [f"ℹ️ Trạng thái thao tác `{target_id}` trên Gateway: `{gw_st}`."]

        return ["⚠️ Không tìm thấy hoặc bạn không có quyền truy cập thông tin thao tác này."]

    # =========================================================================
    # PARAMETER NORMALIZATION AT ADAPTER
    # =========================================================================
    def _normalize_write_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
        """Chuẩn hóa tham số: recipient -> to, due_date -> due_at."""
        res = dict(params)
        if "recipient" in res and "to" not in res:
            res["to"] = res["recipient"]
        if "to" in res and "recipient" not in res:
            res["recipient"] = res["to"]

        if "due_date" in res and "due_at" not in res:
            res["due_at"] = res["due_date"]
        if "due_at" in res and "due_date" not in res:
            res["due_date"] = res["due_at"]

        if "content" in res and "body" not in res:
            res["body"] = res["content"]
        if "body" in res and "content" not in res:
            res["content"] = res["body"]

        return res

    # =========================================================================
    # FORMATTERS
    # =========================================================================
    def _format_whoami(self, ctx: GatewayUserContext) -> str:
        lines = [
            "👤 **Thông tin định danh người dùng:**",
            f"• Họ và tên: {ctx.name or 'Chưa rõ'}",
            f"• Email đăng nhập: {ctx.login or 'Chưa rõ'}",
            f"• Chức danh: {ctx.job_title or 'Chưa rõ'}",
            f"• Phòng ban: {ctx.department or 'Chưa rõ'}",
            f"• Múi giờ: {ctx.timezone}",
            f"• Odoo UID: {ctx.odoo_uid}",
            f"• Quyền hạn: {', '.join(sorted(ctx.permissions)) if ctx.permissions else 'Không có'}",
        ]
        return "\n".join(lines)

    def _format_connection_status(self, user_info: dict[str, Any], conns: dict[str, Any]) -> str:
        lines = [
            "🔗 **Trạng thái kết nối dịch vụ:**",
            f"• Odoo ERP: {'✅ Đã kết nối' if user_info.get('odoo_uid') else '❌ Chưa kết nối'}",
            f"• Telegram Binding: {'✅ Đã liên kết' if user_info.get('telegram_binding_id') else '❌ Chưa liên kết'}",
            f"• Google Workspace: {'✅ Đã cấp quyền' if conns.get('google_connected') else '❌ Chưa kết nối'}",
        ]
        return "\n".join(lines)

    def _format_task_list(self, tasks: list[dict[str, Any]]) -> str:
        if not tasks:
            return "📋 Bạn hiện không có công việc cá nhân nào cần làm."
        lines = ["📋 **Danh sách công việc cá nhân:**"]
        for t in tasks:
            due = f" (Hạn: {t.get('due_at') or t.get('due_date')})" if t.get("due_at") or t.get("due_date") else ""
            status = "✅" if t.get("status") == "DONE" or t.get("status") == "done" else "◻️"
            lines.append(f"{status} `{t.get('id')}`: {t.get('title')}{due}")
        return "\n".join(lines)

    def _safe_time(self, val: Any) -> str:
        """Trích xuất chuỗi thời gian an toàn từ str, dict hoặc None mà không gọi .get() trên chuỗi."""
        if not val:
            return ""
        if isinstance(val, str):
            return val
        if isinstance(val, dict):
            return val.get("dateTime") or val.get("date") or ""
        return str(val)

    def _format_calendar_events(
        self,
        events: list[dict[str, Any]],
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
    ) -> str:
        if not events:
            return "📆 Không tìm thấy cuộc họp hoặc sự kiện nào trong khoảng thời gian đã chọn."
        lines = ["📆 **Danh sách sự kiện Google Calendar:**"]
        for ev in events:
            st = self._safe_time(ev.get("start_time") or ev.get("start"))
            all_day = " (Cả ngày)" if ev.get("is_all_day") else ""
            lines.append(f"• **{ev.get('summary', 'Không tiêu đề')}** ({st}{all_day})")
        return "\n".join(lines)

    def _format_unread_emails(self, msgs: list[dict[str, Any]]) -> str:
        if not msgs:
            return "✉️ Bạn không có email mới nào chưa đọc."
        lines = ["✉️ **Email chưa đọc gần đây:**"]
        for m in msgs:
            sender = m.get("sender") or m.get("from") or "Không rõ"
            lines.append(f"• **{m.get('subject', 'Không tiêu đề')}** từ `{sender}`")
        return "\n".join(lines)

    def _format_crm_leads(self, leads: list[dict[str, Any]], query: str = "") -> str:
        if not leads:
            return f"💼 Không tìm thấy khách hàng hoặc cơ hội nào khớp với từ khóa '{query}'." if query else "💼 Không tìm thấy cơ hội kinh doanh nào."
        header = f"💼 **Kết quả tìm kiếm CRM cho '{query}':**" if query else "💼 **Danh sách cơ hội CRM:**"
        lines = [header]
        for ld in leads:
            rev_val = ld.get("expected_revenue")
            rev = f" - {rev_val:,.0f} VND" if rev_val is not None else ""
            lines.append(f"• `{ld.get('id')}`: **{ld.get('name')}**{rev}")
        return "\n".join(lines)

    def _handle_odoo_query_error(self, res: dict[str, Any], entity_name: str) -> Optional[list[str]]:
        status = res.get("status")
        if status == "OK":
            return None
        if status == "PERMISSION_DENIED":
            return [f"❌ Tài khoản Odoo của bạn không có quyền truy cập dữ liệu {entity_name}."]
        if status == "INVALID_QUERY":
            return [f"⚠️ Truy vấn {entity_name} không hợp lệ: {res.get('message')}"]
        if status in ["ODOO_UNAVAILABLE", "TIMEOUT"]:
            return [f"⏳ Không thể kết nối hoặc quá hạn chờ Odoo khi tra cứu {entity_name}. Vui lòng thử lại sau."]
        return [f"⚠️ Lỗi từ dịch vụ Odoo: {res.get('message', 'Không xác định')}"]

    def _display_val(self, val: Any) -> str:
        if val is None or val is False:
            return ""
        if isinstance(val, (list, tuple)) and len(val) >= 2:
            return str(val[1])
        return str(val)

    def _format_odoo_partners(self, items: list[dict[str, Any]], query: str = "") -> str:
        if not items:
            return f"🏢 Không tìm thấy đối tác nào khớp với từ khóa '{query}'." if query else "🏢 Danh sách đối tác Odoo trống."
        header = f"🏢 **Kết quả tìm kiếm đối tác cho '{query}':**" if query else "🏢 **Danh sách đối tác / khách hàng Odoo:**"
        lines = [header]
        for it in items:
            name = it.get("name") or "Chưa đặt tên"
            email = f" - ✉️ {it.get('email')}" if it.get("email") else ""
            phone = f" - 📞 {it.get('phone')}" if it.get("phone") else ""
            city = f" - 📍 {it.get('city')}" if it.get("city") else ""
            kind = "🏢 Công ty" if it.get("is_company") else "👤 Cá nhân"
            lines.append(f"• `{it.get('id')}`: **{name}** ({kind}){email}{phone}{city}")
        return "\n".join(lines)

    def _format_odoo_products(self, items: list[dict[str, Any]], query: str = "") -> str:
        if not items:
            return f"📦 Không tìm thấy sản phẩm nào khớp với '{query}'." if query else "📦 Danh mục sản phẩm Odoo trống."
        header = f"📦 **Kết quả tìm kiếm sản phẩm cho '{query}':**" if query else "📦 **Danh mục sản phẩm Odoo:**"
        lines = [header]
        for it in items:
            sku = f"[{it.get('default_code')}] " if it.get("default_code") else ""
            price = it.get("list_price")
            price_str = f" - Giá bán: {price:,.0f} đ" if price is not None else ""
            qty = it.get("qty_available")
            qty_str = f" - Tồn: {qty:,.1f}" if qty is not None else ""
            uom = f" {self._display_val(it.get('uom_id'))}" if it.get("uom_id") else ""
            lines.append(f"• `{it.get('id')}`: **{sku}{it.get('name')}**{price_str}{qty_str}{uom}")
        return "\n".join(lines)

    def _format_odoo_sales(self, items: list[dict[str, Any]], query: str = "") -> str:
        if not items:
            return f"🛒 Không tìm thấy đơn bán hàng nào khớp với '{query}'." if query else "🛒 Danh sách đơn bán hàng trống."
        state_map = {"draft": "Báo giá", "sent": "Đã gửi", "sale": "Đơn bán", "done": "Hoàn tất", "cancel": "Đã hủy"}
        header = f"🛒 **Kết quả đơn bán hàng cho '{query}':**" if query else "🛒 **Đơn bán hàng & Báo giá Odoo:**"
        lines = [header]
        for it in items:
            partner = self._display_val(it.get("partner_id"))
            partner_str = f" - Khách: {partner}" if partner else ""
            total = it.get("amount_total")
            total_str = f" - Tổng: {total:,.0f} đ" if total is not None else ""
            st = state_map.get(str(it.get("state")), str(it.get("state") or ""))
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{partner_str}{total_str} [{st}]")
        return "\n".join(lines)

    def _format_odoo_purchases(self, items: list[dict[str, Any]], query: str = "") -> str:
        if not items:
            return f"📥 Không tìm thấy đơn mua hàng nào khớp với '{query}'." if query else "📥 Danh sách đơn mua hàng trống."
        state_map = {"draft": "Yêu cầu báo giá", "sent": "Đã gửi RFQ", "purchase": "Đơn mua", "done": "Hoàn tất", "cancel": "Đã hủy"}
        header = f"📥 **Kết quả đơn mua hàng cho '{query}':**" if query else "📥 **Đơn mua hàng Odoo:**"
        lines = [header]
        for it in items:
            partner = self._display_val(it.get("partner_id"))
            partner_str = f" - NCC: {partner}" if partner else ""
            total = it.get("amount_total")
            total_str = f" - Tổng: {total:,.0f} đ" if total is not None else ""
            st = state_map.get(str(it.get("state")), str(it.get("state") or ""))
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{partner_str}{total_str} [{st}]")
        return "\n".join(lines)

    def _format_odoo_inventory(self, items: list[dict[str, Any]], query: str = "") -> str:
        if not items:
            return f"🚚 Không tìm thấy phiếu kho nào khớp với '{query}'." if query else "🚚 Danh sách điều chuyển kho trống."
        state_map = {"draft": "Dự thảo", "waiting": "Chờ hoạt động khác", "confirmed": "Chờ hàng", "assigned": "Sẵn sàng", "done": "Hoàn tất", "cancel": "Đã hủy"}
        header = f"🚚 **Kết quả phiếu điều chuyển kho cho '{query}':**" if query else "🚚 **Phiếu điều chuyển kho Odoo:**"
        lines = [header]
        for it in items:
            origin = f" - Nguồn: {it.get('origin')}" if it.get("origin") else ""
            st = state_map.get(str(it.get("state")), str(it.get("state") or ""))
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{origin} [{st}]")
        return "\n".join(lines)

    def _format_odoo_invoices(self, items: list[dict[str, Any]], query: str = "") -> str:
        if not items:
            return f"📄 Không tìm thấy hóa đơn nào khớp với '{query}'." if query else "📄 Danh sách hóa đơn trống."
        pay_map = {"not_paid": "Chưa thanh toán", "in_payment": "Đang thanh toán", "paid": "Đã thanh toán", "partial": "Thanh toán 1 phần"}
        header = f"📄 **Kết quả hóa đơn cho '{query}':**" if query else "📄 **Hóa đơn & Chứng từ kế toán Odoo:**"
        lines = [header]
        for it in items:
            partner = self._display_val(it.get("partner_id"))
            partner_str = f" - Đối tác: {partner}" if partner else ""
            total = it.get("amount_total")
            total_str = f" - Tổng: {total:,.0f} đ" if total is not None else ""
            pay = pay_map.get(str(it.get("payment_state")), str(it.get("payment_state") or ""))
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{partner_str}{total_str} [{pay}]")
        return "\n".join(lines)

    def _format_odoo_employees(self, items: list[dict[str, Any]], query: str = "") -> str:
        if not items:
            return f"👥 Không tìm thấy nhân sự nào khớp với '{query}'." if query else "👥 Danh bạ nhân viên Odoo trống."
        header = f"👥 **Kết quả tìm kiếm nhân viên cho '{query}':**" if query else "👥 **Danh bạ nhân viên Odoo:**"
        lines = [header]
        for it in items:
            job = f" - Chức danh: {it.get('job_title')}" if it.get("job_title") else ""
            dept = self._display_val(it.get("department_id"))
            dept_str = f" - PB: {dept}" if dept else ""
            email = f" - ✉️ {it.get('work_email')}" if it.get("work_email") else ""
            phone = f" - 📞 {it.get('work_phone')}" if it.get("work_phone") else ""
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{job}{dept_str}{email}{phone}")
        return "\n".join(lines)


unified_executor = UnifiedExecutor()

