"""Read intents and Workspace tool plans."""

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

from app.domain.gateway_context import GatewayPermission, GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.knowledge.models import IndexingJob
from app.services.context_resolver import resolve_relative_time_window
from app.services.morning_briefing_service import morning_briefing_service

logger = logging.getLogger(__name__)


def _normalize_workspace_params(service: str, params: dict) -> dict:
    if service == "drive" and isinstance(params, dict) and params.get("method") == "files.list":
        call_params = dict(params.get("parameters") or {})
        q = call_params.get("q")
        if isinstance(q, str):
            def _replace_name_eq(match):
                val = match.group(1)
                return f"(name = '{val}' or name contains '{val}')"
            new_q = re.sub(r"\bname\s*=\s*'([^']+)'", _replace_name_eq, q)
            if new_q != q:
                return {**params, "parameters": {**call_params, "q": new_q}}
    return params


class ReadHandler:
    """Resolve readable tools, format results and remember read data."""

    async def _handle_workspace_plan(
        self,
        intent: IntentAnalysisResult,
        context: GatewayUserContext,
        chat_id: int,
        deadline: float,
    ) -> list[str]:
        from app.core.config import get_settings
        from app.services.turn_trace import request_id
        from app.workspace.catalog import SERVICES

        turn_id = request_id.get()
        reads = {f"workspace.{service}.read" for service in SERVICES}
        if (get_settings().WORKSPACE_BACKGROUND_READS and turn_id
                and context.telegram_binding_id and context.telegram_user_id and context.chat_id
                and all(step.tool_name in reads and context.has_permission(step.tool_name)
                        for step in intent.steps)):
            documents = {"drive", "docs", "sheets", "slides"}
            long_read = any(step.tool_name.split(".")[1] in documents for step in intent.steps)
            job = await self.gateway_client.create_workspace_job(
                context.user_id, turn_id, "workspace", "plan.read",
                body={"steps": [step.model_dump() for step in intent.steps]},
                wait_seconds=0, deadline_seconds=300 if long_read else 120,
                telegram_chat_id=context.chat_id, telegram_user_id=context.telegram_user_id,
                telegram_binding_id=context.telegram_binding_id,
            )
            if job.get("job_id"):
                return [f"Đã nhận kế hoạch đọc (#{job['job_id']}). Tôi sẽ gửi kết quả khi hoàn tất."]
            return ["⚠️ Không thể đưa kế hoạch đọc vào hàng đợi."]
        return await self._execute_workspace_plan(intent, context, chat_id, deadline)

    async def _execute_workspace_plan(self, intent, context, chat_id, deadline, *, background=False):
        from app.services.workspace_tool_plan import StepResult, execute_plan, summarize_calendar_page
        from app.workspace.agent import format_result, validate_tool
        from app.workspace.catalog import SERVICES

        allowed_names = {
            f"workspace.{service}.{action}" for service in SERVICES for action in ("read", "write")
        }
        allowed_names.add("odoo.partners.search")
        writes = []
        for step in intent.steps:
            tool = self.registry.get_tool(step.tool_name)
            if (tool is None or tool.name not in allowed_names
                    or (tool.required_permission and not context.has_permission(tool.required_permission))
                    or (background and tool.action_type != "read")):
                if background:
                    raise PermissionError("Kế hoạch chứa công cụ ngoài quyền hoặc ngoài phạm vi.")
                return ["⚠️ Kế hoạch chứa công cụ ngoài quyền hoặc ngoài phạm vi hỗ trợ."]
            if tool.action_type == "write":
                writes.append(step.id)
        if len(writes) > 1 or any(
            dependency in writes for step in intent.steps for dependency in step.depends_on
        ):
            return ["⚠️ Kế hoạch chỉ hỗ trợ một bước chuẩn bị ghi ở cuối."]

        async def invoke(step, params, timeout):
            if step.tool_name == "odoo.partners.search":
                return await self._resolve_plan_partner(context, params)
            service, kind = validate_tool(step.tool_name, params)
            if kind == "write":
                child = IntentAnalysisResult(
                    intent_type=IntentType.PREPARE_WRITE, tool_name=step.tool_name, parameters=params,
                )
                messages = await self._handle_prepare_write(child, context, chat_id)
                return StepResult("preview", messages)
            params = _normalize_workspace_params(service, params)
            result = await self.gateway_client.workspace_query(
                context.user_id, service, params, timeout=timeout,
            )
            ok = result.get("status") == "OK"
            data = result.get("data") if ok else None
            if service == "calendar" and params["method"] == "events.list" and isinstance(data, dict):
                messages = summarize_calendar_page(data)
            else:
                messages = format_result(service, result)
            return StepResult("completed" if ok else "failed", messages, data)

        # Prepare a write only once every read has succeeded, even if the model
        # omitted a dependency. The existing confirmation remains authoritative.
        steps = [
            step.model_copy(update={"depends_on": [item.id for item in intent.steps if item.id != step.id]})
            if step.id in writes else step
            for step in intent.steps
        ]
        outcomes = await execute_plan(steps, invoke, deadline, writes=writes,
                                      task_timeout=300 if background else 120)
        if background:
            return {
                "steps": [
                    {"id": step.id, "tool": step.tool_name, "status": outcomes[step.id].status,
                     "messages": outcomes[step.id].messages}
                    for step in steps
                ],
                "partial": any(result.status != "completed" for result in outcomes.values()),
            }
        if len(steps) == 1:
            return ["\n".join(outcomes[steps[0].id].messages)]
        return [
            f"Bước {position} · {step.tool_name}\n" + "\n".join(outcomes[step.id].messages)
            for position, step in enumerate(steps, 1)
        ]

    async def _resolve_plan_partner(self, context, params):
        from app.business.source_filters import contact_filter
        from app.services.workspace_tool_plan import StepResult

        query = params.get("query")
        if (not isinstance(query, str) or not query.strip() or len(query) > 200
                or set(params) - {"query", "contact_kind", "record_scope"}):
            return StepResult("failed", ["Cần tên hoặc email đối tác cụ thể."])
        query = query.strip()
        domain, _ = contact_filter(params, context)
        if "@" in query:
            domain.append(("email", "=", query))
        elif "%" in query or "_" in query:
            domain.append(("name", "=", query))
        else:
            domain.append(("name", "ilike", query))
        result = await self.gateway_client.query_odoo(
            user_id=context.user_id, model="res.partner", fields=["id", "name", "email"],
            domain=domain, limit=2,
        )
        error = self._handle_odoo_query_error(result, "đối tác/khách hàng")
        if error:
            return StepResult("failed", error)
        matches = result.get("items", [])
        if len(matches) > 1 or result.get("total_count", 0) not in (None, 0, 1):
            return StepResult("failed", ["Có nhiều đối tác phù hợp. Hãy chỉ rõ tên hoặc email của đối tác cần chọn."])
        if len(matches) != 1 or not isinstance(matches[0].get("email"), str) or not matches[0]["email"].strip():
            return StepResult("failed", ["Chưa tìm được email của đối tác."])
        partner = matches[0]
        return StepResult("completed", ["Đã xác định một đối tác có email trong Odoo."],
                          {"email": partner["email"]})

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

    async def _handle_read(
        self,
        intent: IntentAnalysisResult,
        context: GatewayUserContext,
        chat_id: int,
        user_info: dict[str, Any],
        remaining_time: float,
        *,
        history: list[dict] | None = None,
    ) -> list[str]:
        tool_name = intent.tool_name
        tool_def = self.registry.get_tool(tool_name) if tool_name else None
        if not tool_def:
            return [f"⚠️ Công cụ đọc `{tool_name}` không tồn tại trong hệ thống."]

        if tool_def.required_permission and not context.has_permission(tool_def.required_permission):
            return [f"⚠️ Bạn chưa được cấp quyền `{tool_def.required_permission}` để tra cứu thông tin này."]

        params = dict(intent.parameters)
        timeout = max(1.0, min(10.0, remaining_time - 1.0))

        if tool_name == "knowledge.query":
            from app.knowledge.drive import (
                answer_from_drive, drive_query_terms, drive_reference, resolve_drive_follow_up,
            )
            from app.core.config import get_settings
            from app.services.turn_trace import request_id

            query = (params.get("query") or params.get("q") or "").strip()
            if query and get_settings().DRIVE_INDEX_ENABLED:
                query = resolve_drive_follow_up(query, history or [])
            turn_id = request_id.get()
            if (query and get_settings().WORKSPACE_BACKGROUND_READS and turn_id
                    and context.telegram_binding_id and context.telegram_user_id and context.chat_id):
                file_id, has_link = drive_reference(query)
                if has_link and not file_id:
                    return ["Hãy gửi link một tệp cụ thể hoặc đặt câu hỏi để tôi tìm trên Google Drive."]
                _, terms = drive_query_terms(query)
                job = await self.gateway_client.create_workspace_job(
                    context.user_id, turn_id, "drive", "knowledge.search",
                    parameters={"terms": terms, "file_id": file_id}, body={"question": query},
                    deadline_seconds=300, wait_seconds=0, telegram_chat_id=context.chat_id,
                    telegram_user_id=context.telegram_user_id, telegram_binding_id=context.telegram_binding_id,
                )
                if job.get("job_id"):
                    return [f"Đang tìm tài liệu Drive (#{job['job_id']}). Tôi sẽ gửi câu trả lời khi hoàn tất."]
                return ["⚠️ Không thể đưa yêu cầu tìm tài liệu vào hàng đợi. Vui lòng thử lại."]
            return await answer_from_drive(query, context)

        if tool_name and tool_name.startswith("business."):
            from app.business.operations import read as operation_read
            return await operation_read(tool_name, params, context)
        if tool_name and (tool_name.startswith("reports.") or tool_name.startswith("sales.quotation.")):
            from app.business.workflow import read as business_read
            return await business_read(tool_name, params, context)

        if tool_name == "workspace.catalog":
            from app.workspace.agent import catalog_messages
            try:
                return catalog_messages(params)
            except (ValueError, KeyError) as exc:
                return [f"⚠️ {exc}"]
        if tool_name and tool_name.startswith("workspace."):
            from app.workspace.agent import format_result, validate_tool
            try:
                service_name, kind = validate_tool(tool_name, params)
                if kind != "read":
                    return ["⚠️ Thao tác ghi cần bản xem trước và xác nhận."]
                params = _normalize_workspace_params(service_name, params)
                from app.core.config import get_settings
                from app.services.turn_trace import request_id

                turn_id = request_id.get()
                if (get_settings().WORKSPACE_BACKGROUND_READS and turn_id and context.telegram_binding_id
                        and context.telegram_user_id and context.chat_id):
                    job = await self.gateway_client.create_workspace_job(
                        context.user_id, turn_id, service_name, params["method"],
                        parameters=params.get("parameters"), body=params.get("body"), wait_seconds=0,
                        telegram_chat_id=context.chat_id, telegram_user_id=context.telegram_user_id,
                        telegram_binding_id=context.telegram_binding_id,
                    )
                    if job.get("job_id"):
                        return [f"Đã nhận tác vụ Workspace (#{job['job_id']}). Tôi sẽ gửi kết quả khi xử lý xong."]
                    return ["⚠️ Không thể đưa yêu cầu Workspace vào hàng đợi. Vui lòng thử lại."]
                result = await self.gateway_client.workspace_query(context.user_id, service_name, params, timeout=timeout)
                if result.get("status") == "OK":
                    content = result.get("data", {})
                    if len(json.dumps(content, ensure_ascii=False)) <= 12000 and not (isinstance(content, dict) and "content_base64" in content):
                        self.conversation_history_store.set_last_read_data(context.tenant, context.user_id, chat_id, context.telegram_binding_id, f"workspace.{service_name}", content, database=context.database)
                return format_result(service_name, result)
            except ValueError as exc:
                return [f"⚠️ Yêu cầu Workspace chưa hợp lệ: {exc}"]

        if tool_name in {"knowledge.documents.list", "knowledge.documents.status"}:
            return await self._execute_knowledge_tool(tool_name, params, context, chat_id)

        if tool_name == "identity.whoami":
            from app.api.v1.internal import format_whoami
            return [format_whoami(context)]

        if tool_name == "connections.status":
            from app.api.v1.internal import format_connection_status
            conns = await self.gateway_client.get_user_connections(context.user_id)
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
            self.conversation_history_store.set_last_read_data(
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
            self.conversation_history_store.set_last_read_data(
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
            self.conversation_history_store.set_last_read_data(
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
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "crm", leads, database=context.database
            )
            return [self._format_crm_leads(leads, query)]

        if tool_name == "odoo.partners.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 10)
            count_only = bool(params.get("count_only"))
            from app.business.source_filters import contact_filter, count_message
            try:
                domain, entity_label = contact_filter(params, context)
            except ValueError as exc:
                return [str(exc)]
            if query:
                domain += ["|", ("name", "ilike", query), ("email", "ilike", query)]
            res = await self.gateway_client.query_odoo(
                user_id=context.user_id,
                model="res.partner",
                domain=domain,
                limit=limit,
                count_only=count_only,
            )
            err = self._handle_odoo_query_error(res, "đối tác/khách hàng")
            if err:
                return err
            items = res.get("items", [])
            total_count = res.get("total_count", len(items))
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.partners", items, database=context.database
            )
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo_last_count",
                {"category": "odoo.partners", "total_count": total_count, "query": query, "model": "res.partner", "entity_label": entity_label},
                database=context.database
            )
            if count_only:
                return [count_message(res.get("total_count"), entity_label, query)]
            return ["Phạm vi: " + entity_label + ".\n" + self._format_odoo_partners(items, query, total_count=total_count, count_only=False)]

        if tool_name == "odoo.products.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 10)
            count_only = bool(params.get("count_only"))
            domain = []
            if query:
                domain = ["|", ("name", "ilike", query), ("default_code", "ilike", query)]
            res = await self.gateway_client.query_odoo(
                user_id=context.user_id,
                model="product.product",
                domain=domain,
                limit=limit,
                count_only=count_only,
            )
            err = self._handle_odoo_query_error(res, "sản phẩm")
            if err:
                return err
            items = res.get("items", [])
            total_count = res.get("total_count", len(items))
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.products", items, database=context.database
            )
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo_last_count",
                {"category": "odoo.products", "total_count": total_count, "query": query, "model": "product.product"},
                database=context.database
            )
            return [self._format_odoo_products(items, query, total_count=total_count, count_only=count_only)]

        if tool_name == "odoo.sales.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 10)
            count_only = bool(params.get("count_only"))
            from app.business.source_filters import count_message, document_filter, document_rows
            try:
                domain, entity_label = document_filter("sales", params, context)
            except ValueError as exc:
                return [str(exc)]
            if query:
                domain += ["|", ("name", "ilike", query), ("partner_id", "ilike", query)]
            res = await self.gateway_client.query_odoo(
                user_id=context.user_id,
                model="sale.order",
                fields=["id", "name", "partner_id", "date_order", "amount_total", "state", "currency_id", "user_id"],
                domain=domain,
                limit=limit,
                count_only=count_only,
            )
            err = self._handle_odoo_query_error(res, "đơn bán hàng")
            if err:
                return err
            items = res.get("items", [])
            total_count = res.get("total_count", len(items))
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.sales", items, database=context.database
            )
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo_last_count",
                {"category": "odoo.sales", "total_count": total_count, "query": query, "model": "sale.order", "entity_label": entity_label},
                database=context.database
            )
            if count_only:
                return [count_message(res.get("total_count"), entity_label, query)]
            return [document_rows(items, entity_label, res.get("total_count"))]

        if tool_name == "odoo.purchases.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 10)
            count_only = bool(params.get("count_only"))
            from app.business.source_filters import count_message, document_filter, document_rows
            try:
                domain, entity_label = document_filter("purchases", params, context)
            except ValueError as exc:
                return [str(exc)]
            if query:
                domain += ["|", ("name", "ilike", query), ("partner_id", "ilike", query)]
            res = await self.gateway_client.query_odoo(
                user_id=context.user_id,
                model="purchase.order",
                fields=["id", "name", "partner_id", "date_order", "amount_total", "state", "currency_id", "user_id"],
                domain=domain,
                limit=limit,
                count_only=count_only,
            )
            err = self._handle_odoo_query_error(res, "đơn mua hàng")
            if err:
                return err
            items = res.get("items", [])
            total_count = res.get("total_count", len(items))
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.purchases", items, database=context.database
            )
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo_last_count",
                {"category": "odoo.purchases", "total_count": total_count, "query": query, "model": "purchase.order", "entity_label": entity_label},
                database=context.database
            )
            if count_only:
                return [count_message(res.get("total_count"), entity_label, query)]
            return [document_rows(items, entity_label, res.get("total_count"))]

        if tool_name == "odoo.inventory.search":
            query = (params.get("query") or "").strip()
            state = (params.get("state") or "").strip()
            limit = int(params.get("limit") or 10)
            count_only = bool(params.get("count_only"))
            domain = []
            if query:
                domain = ["|", ("name", "ilike", query), ("origin", "ilike", query)]
            if state:
                domain.append(("state", "=", state))
            res = await self.gateway_client.query_odoo(
                user_id=context.user_id,
                model="stock.picking",
                domain=domain,
                limit=limit,
                count_only=count_only,
            )
            err = self._handle_odoo_query_error(res, "kho bãi")
            if err:
                return err
            items = res.get("items", [])
            total_count = res.get("total_count", len(items))
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.inventory", items, database=context.database
            )
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo_last_count",
                {"category": "odoo.inventory", "total_count": total_count, "query": query, "model": "stock.picking"},
                database=context.database
            )
            return [self._format_odoo_inventory(items, query, total_count=total_count, count_only=count_only)]

        if tool_name == "odoo.invoices.search":
            query = (params.get("query") or "").strip()
            pay_state = (params.get("payment_state") or "").strip()
            limit = int(params.get("limit") or 10)
            count_only = bool(params.get("count_only"))
            domain = []
            if query:
                domain.append(("name", "ilike", query))
            if pay_state:
                domain.append(("payment_state", "=", pay_state))
            res = await self.gateway_client.query_odoo(
                user_id=context.user_id,
                model="account.move",
                domain=domain,
                limit=limit,
                count_only=count_only,
            )
            err = self._handle_odoo_query_error(res, "hóa đơn")
            if err:
                return err
            items = res.get("items", [])
            total_count = res.get("total_count", len(items))
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.invoices", items, database=context.database
            )
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo_last_count",
                {"category": "odoo.invoices", "total_count": total_count, "query": query, "model": "account.move"},
                database=context.database
            )
            return [self._format_odoo_invoices(items, query, total_count=total_count, count_only=count_only)]

        if tool_name == "odoo.employees.search":
            query = (params.get("query") or "").strip()
            limit = int(params.get("limit") or 10)
            count_only = bool(params.get("count_only"))
            domain = []
            if query:
                domain = ["|", ("name", "ilike", query), ("work_email", "ilike", query)]
            res = await self.gateway_client.query_odoo(
                user_id=context.user_id,
                model="hr.employee",
                domain=domain,
                limit=limit,
                count_only=count_only,
            )
            err = self._handle_odoo_query_error(res, "danh bạ nhân sự")
            if err:
                return err
            items = res.get("items", [])
            total_count = res.get("total_count", len(items))
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo.employees", items, database=context.database
            )
            self.conversation_history_store.set_last_read_data(
                context.tenant, context.user_id, chat_id, context.telegram_binding_id, "odoo_last_count",
                {"category": "odoo.employees", "total_count": total_count, "query": query, "model": "hr.employee"},
                database=context.database
            )
            return [self._format_odoo_employees(items, query, total_count=total_count, count_only=count_only)]

        return [f"⚠️ Chưa có handler triển khai cho công cụ đọc `{tool_name}`."]

    async def _execute_knowledge_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        context: GatewayUserContext,
        chat_id: int,
    ) -> list[str]:
        import os
        from pathlib import Path
        from app.knowledge.store import metadata_store
        from app.knowledge.indexing import indexing_service
        from app.core.exceptions import SkillPermissionError
        from app.services.file_service import file_service

        workspace_id = context.knowledge_workspace
        owner_id = str(context.user_id)
        permission = GatewayPermission.KNOWLEDGE_WRITE if tool_name == "knowledge.documents.index" else GatewayPermission.KNOWLEDGE_READ
        if not workspace_id or not context.has_permission(permission):
            return ["Tài khoản chưa có quyền truy cập kho tri thức này."]

        if tool_name == "knowledge.documents.list":
            sources = await asyncio.to_thread(metadata_store.list_sources, workspace_id)
            if not sources:
                return [
                    "📚 **Kho Tri Thức Hiện Tại:**\n"
                    "Chưa có tài liệu nào được lập chỉ mục.\n\n"
                    "💡 *Bạn hãy đặt các tệp tài liệu (.pdf, .docx, .xlsx, .txt, .csv) vào thư mục `knowledge_docs/` và yêu cầu: 'Lập chỉ mục tài liệu' để bắt đầu!*"
                ]

            lines = [f"📚 **Danh mục tài liệu trong Kho Tri Thức ({len(sources)} tài liệu):**\n"]
            for idx, s in enumerate(sources, 1):
                state_icon = "✅" if s.state == "INDEXED" else "⏳" if s.state == "INDEXING" else "⚠️"
                lines.append(f"{idx}. {state_icon} **{s.title}** (`{s.source_type}`)")
                if s.indexed_at:
                    lines.append(f"   - Cập nhật: `{s.indexed_at[:19].replace('T', ' ')}`")
            return ["\n".join(lines)]

        if tool_name == "knowledge.documents.status":
            job_id = str(params.get("job_id") or "").strip()
            if not job_id:
                return ["Cú pháp: /knowledge status <mã tác vụ>"]
            job = await asyncio.to_thread(metadata_store.get_job, job_id)
            payload = (job.payload or {}) if job else {}
            if not job or payload.get("workspace_id") != workspace_id or payload.get("owner_id") != owner_id:
                return ["Không tìm thấy tác vụ lập chỉ mục."]
            return [self._format_knowledge_index_job(job)]

        if tool_name == "knowledge.documents.index":
            paths = params.get("paths") or []
            if isinstance(paths, str):
                paths = [paths]

            # Tự động quét thư mục knowledge_docs nếu không chỉ định đường dẫn
            if not paths:
                scan_dir = f"knowledge_docs/{workspace_id}"
                try:
                    safe_dir = file_service.get_safe_path(scan_dir)
                    if os.path.exists(safe_dir) and os.path.isdir(safe_dir):
                        valid_exts = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"}
                        for root, _, files in os.walk(safe_dir):
                            for fname in files:
                                if os.path.splitext(fname)[1].lower() in valid_exts:
                                    full_fpath = os.path.join(root, fname)
                                    rel_path = os.path.relpath(full_fpath, file_service.workspace_root)
                                    paths.append(rel_path.replace("\\", "/"))
                except Exception as exc:
                    logger.warning("Không thể quét thư mục %s: %s", scan_dir, exc)

            if not paths:
                return [
                    "⚠️ Không tìm thấy tài liệu nào để lập chỉ mục.\n"
                    "Vui lòng đặt các tệp (.pdf, .docx, .xlsx, .txt, .csv) vào thư mục `knowledge_docs/` hoặc chỉ định đường dẫn tệp cụ thể."
                ]

            root = file_service.workspace_root / "knowledge_docs" / workspace_id
            try:
                normalized = [Path(file_service.get_safe_path(path)) for path in paths]
                if any(not path.is_relative_to(root) for path in normalized):
                    return ["Chỉ có thể lập chỉ mục tệp trong knowledge_docs/."]
            except (ValueError, TypeError, SkillPermissionError):
                return ["Đường dẫn tài liệu không hợp lệ."]
            paths = list(dict.fromkeys(
                path.relative_to(file_service.workspace_root).as_posix() for path in normalized
            ))

            job = IndexingJob(
                type="FILE",
                status="PENDING",
                payload={"paths": paths, "workspace_id": workspace_id, "owner_id": owner_id},
            )
            job_id = job.job_id
            await asyncio.to_thread(metadata_store.update_job, job)

            try:
                await indexing_service.enqueue_indexing_job(job_id, paths, workspace_id, owner_id)
                wait_until = time.monotonic() + 5.0
                while time.monotonic() < wait_until:
                    stored = await asyncio.to_thread(metadata_store.get_job, job_id)
                    if stored and stored.status in {"COMPLETED", "PARTIAL", "FAILED"}:
                        return [self._format_knowledge_index_job(stored)]
                    await asyncio.sleep(0.25)
                return [
                    f"⏳ **Hệ thống đang tiến hành lập chỉ mục {len(paths)} tài liệu trong nền...**\n"
                    f"Mã tiến trình: `{job_id}`\n\n"
                    f"Quá trình này sẽ hoàn tất sau ít phút. Kiểm tra bằng `/knowledge status {job_id}`."
                ]
            except Exception as exc:
                logger.error("Lỗi lập chỉ mục tài liệu: %s", exc, exc_info=True)
                return [f"⚠️ Đã xảy ra lỗi khi lập chỉ mục tài liệu: {exc}"]

        return [f"⚠️ Chưa có handler triển khai cho công cụ knowledge `{tool_name}`."]

    @staticmethod
    def _format_knowledge_index_job(job: IndexingJob) -> str:
        from os.path import basename

        payload = job.payload or {}
        if job.status in {"PENDING", "RUNNING"}:
            return f"Lập chỉ mục đang xử lý ({job.progress}%). Mã tác vụ: {job.job_id}"
        indexed = payload.get("indexed", [])
        failed = payload.get("failed", [])
        total = len(payload.get("paths", []))
        lines = [f"Lập chỉ mục: {len(indexed)}/{total} tài liệu thành công."]
        if failed:
            lines.append("Tệp thất bại: " + ", ".join(basename(path) for path in failed))
        if job.error:
            lines.append("Tác vụ lập chỉ mục gặp lỗi.")
        return "\n".join(lines)

    def _format_whoami(self, ctx: GatewayUserContext) -> str:
        lines = [
            "👤 **Thông tin định danh người dùng:**",
            f"• Họ và tên: {ctx.name or 'Chưa rõ'}",
            f"• Email đăng nhập: {ctx.login or 'Chưa rõ'}",
            f"• Chức danh: {ctx.job_title or 'Chưa rõ'}",
            f"• Phòng ban: {ctx.department or 'Chưa rõ'}",
            f"• Google Workspace đang dùng: {ctx.google_account_email or 'Chưa liên kết'}",
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

    def _format_odoo_partners(self, items: list[dict[str, Any]], query: str = "", total_count: int | None = None, count_only: bool = False) -> str:
        if count_only:
            count_val = total_count if total_count is not None else len(items)
            if query:
                return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} liên hệ/đối tác khớp với '{query}'.**"
            return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} liên hệ/đối tác.**"

        if not items:
            return f"🏢 Không tìm thấy đối tác nào khớp với từ khóa '{query}'." if query else "🏢 Danh sách đối tác Odoo trống."

        count_str = f" (Tổng cộng **{total_count:,}** đối tác trên hệ thống)" if total_count is not None and total_count > len(items) else ""
        header = f"🏢 **Kết quả tìm kiếm đối tác cho '{query}'{count_str}:**" if query else f"🏢 **Hiện có tổng cộng {total_count or len(items):,} đối tác / khách hàng trên Odoo (hiển thị {len(items)} bản ghi gần nhất):**"
        lines = [header]
        for it in items:
            name = it.get("name") or "Chưa đặt tên"
            email = f" - ✉️ {it.get('email')}" if it.get("email") else ""
            phone = f" - 📞 {it.get('phone')}" if it.get("phone") else ""
            city = f" - 📍 {it.get('city')}" if it.get("city") else ""
            kind = "🏢 Công ty" if it.get("is_company") else "👤 Cá nhân"
            lines.append(f"• `{it.get('id')}`: **{name}** ({kind}){email}{phone}{city}")
        return "\n".join(lines)

    def _format_odoo_products(self, items: list[dict[str, Any]], query: str = "", total_count: int | None = None, count_only: bool = False) -> str:
        if count_only:
            count_val = total_count if total_count is not None else len(items)
            if query:
                return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} sản phẩm khớp với '{query}'.**"
            return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} sản phẩm.**"

        if not items:
            return f"📦 Không tìm thấy sản phẩm nào khớp với '{query}'." if query else "📦 Danh mục sản phẩm Odoo trống."

        count_str = f" (Tổng cộng **{total_count:,}** sản phẩm trên hệ thống)" if total_count is not None and total_count > len(items) else ""
        header = f"📦 **Kết quả tìm kiếm sản phẩm cho '{query}'{count_str}:**" if query else f"📦 **Danh mục sản phẩm Odoo (Tổng cộng {total_count or len(items):,} sản phẩm, hiển thị {len(items)} bản ghi gần nhất):**"
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

    def _format_odoo_sales(self, items: list[dict[str, Any]], query: str = "", total_count: int | None = None, count_only: bool = False) -> str:
        if count_only:
            count_val = total_count if total_count is not None else len(items)
            return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} đơn bán hàng/báo giá.**"

        if not items:
            return f"🛒 Không tìm thấy đơn bán hàng nào khớp với '{query}'." if query else "🛒 Danh sách đơn bán hàng trống."
        state_map = {"draft": "Báo giá", "sent": "Đã gửi", "sale": "Đơn bán", "done": "Hoàn tất", "cancel": "Đã hủy"}
        count_str = f" (Tổng cộng **{total_count:,}** đơn hàng)" if total_count is not None and total_count > len(items) else ""
        header = f"🛒 **Kết quả đơn bán hàng cho '{query}'{count_str}:**" if query else f"🛒 **Đơn bán hàng & Báo giá Odoo (Tổng cộng {total_count or len(items):,} đơn):**"
        lines = [header]
        for it in items:
            partner = self._display_val(it.get("partner_id"))
            partner_str = f" - Khách: {partner}" if partner else ""
            total = it.get("amount_total")
            total_str = f" - Tổng: {total:,.0f} đ" if total is not None else ""
            st = state_map.get(str(it.get("state")), str(it.get("state") or ""))
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{partner_str}{total_str} [{st}]")
        return "\n".join(lines)

    def _format_odoo_purchases(self, items: list[dict[str, Any]], query: str = "", total_count: int | None = None, count_only: bool = False) -> str:
        if count_only:
            count_val = total_count if total_count is not None else len(items)
            return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} đơn mua hàng.**"

        if not items:
            return f"📥 Không tìm thấy đơn mua hàng nào khớp với '{query}'." if query else "📥 Danh sách đơn mua hàng trống."
        state_map = {"draft": "Yêu cầu báo giá", "sent": "Đã gửi RFQ", "purchase": "Đơn mua", "done": "Hoàn tất", "cancel": "Đã hủy"}
        count_str = f" (Tổng cộng **{total_count:,}** đơn mua)" if total_count is not None and total_count > len(items) else ""
        header = f"📥 **Kết quả đơn mua hàng cho '{query}'{count_str}:**" if query else f"📥 **Đơn mua hàng Odoo (Tổng cộng {total_count or len(items):,} đơn):**"
        lines = [header]
        for it in items:
            partner = self._display_val(it.get("partner_id"))
            partner_str = f" - NCC: {partner}" if partner else ""
            total = it.get("amount_total")
            total_str = f" - Tổng: {total:,.0f} đ" if total is not None else ""
            st = state_map.get(str(it.get("state")), str(it.get("state") or ""))
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{partner_str}{total_str} [{st}]")
        return "\n".join(lines)

    def _format_odoo_inventory(self, items: list[dict[str, Any]], query: str = "", total_count: int | None = None, count_only: bool = False) -> str:
        if count_only:
            count_val = total_count if total_count is not None else len(items)
            return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} phiếu điều chuyển kho.**"

        if not items:
            return f"🚚 Không tìm thấy phiếu kho nào khớp với '{query}'." if query else "🚚 Danh sách điều chuyển kho trống."
        state_map = {"draft": "Dự thảo", "waiting": "Chờ hoạt động khác", "confirmed": "Chờ hàng", "assigned": "Sẵn sàng", "done": "Hoàn tất", "cancel": "Đã hủy"}
        count_str = f" (Tổng cộng **{total_count:,}** phiếu kho)" if total_count is not None and total_count > len(items) else ""
        header = f"🚚 **Kết quả phiếu điều chuyển kho cho '{query}'{count_str}:**" if query else f"🚚 **Phiếu điều chuyển kho Odoo (Tổng cộng {total_count or len(items):,} phiếu):**"
        lines = [header]
        for it in items:
            origin = f" - Nguồn: {it.get('origin')}" if it.get("origin") else ""
            st = state_map.get(str(it.get("state")), str(it.get("state") or ""))
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{origin} [{st}]")
        return "\n".join(lines)

    def _format_odoo_invoices(self, items: list[dict[str, Any]], query: str = "", total_count: int | None = None, count_only: bool = False) -> str:
        if count_only:
            count_val = total_count if total_count is not None else len(items)
            return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} hóa đơn.**"

        if not items:
            return f"📄 Không tìm thấy hóa đơn nào khớp với '{query}'." if query else "📄 Danh sách hóa đơn trống."
        pay_map = {"not_paid": "Chưa thanh toán", "in_payment": "Đang thanh toán", "paid": "Đã thanh toán", "partial": "Thanh toán 1 phần"}
        count_str = f" (Tổng cộng **{total_count:,}** hóa đơn)" if total_count is not None and total_count > len(items) else ""
        header = f"📄 **Kết quả hóa đơn cho '{query}'{count_str}:**" if query else f"📄 **Hóa đơn & Chứng từ kế toán Odoo (Tổng cộng {total_count or len(items):,} hóa đơn):**"
        lines = [header]
        for it in items:
            partner = self._display_val(it.get("partner_id"))
            partner_str = f" - Đối tác: {partner}" if partner else ""
            total = it.get("amount_total")
            total_str = f" - Tổng: {total:,.0f} đ" if total is not None else ""
            pay = pay_map.get(str(it.get("payment_state")), str(it.get("payment_state") or ""))
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{partner_str}{total_str} [{pay}]")
        return "\n".join(lines)

    def _format_odoo_employees(self, items: list[dict[str, Any]], query: str = "", total_count: int | None = None, count_only: bool = False) -> str:
        if count_only:
            count_val = total_count if total_count is not None else len(items)
            return f"📊 **Hiện tại trên hệ thống Odoo đang có chính xác {count_val:,} nhân sự.**"

        if not items:
            return f"👥 Không tìm thấy nhân sự nào khớp với '{query}'." if query else "👥 Danh bạ nhân viên Odoo trống."
        count_str = f" (Tổng cộng **{total_count:,}** nhân sự)" if total_count is not None and total_count > len(items) else ""
        header = f"👥 **Kết quả tìm kiếm nhân viên cho '{query}'{count_str}:**" if query else f"👥 **Danh bạ nhân viên Odoo (Tổng cộng {total_count or len(items):,} nhân sự):**"
        lines = [header]
        for it in items:
            job = f" - Chức danh: {it.get('job_title')}" if it.get("job_title") else ""
            dept = self._display_val(it.get("department_id"))
            dept_str = f" - PB: {dept}" if dept else ""
            email = f" - ✉️ {it.get('work_email')}" if it.get("work_email") else ""
            phone = f" - 📞 {it.get('work_phone')}" if it.get("work_phone") else ""
            lines.append(f"• `{it.get('id')}`: **{it.get('name')}**{job}{dept_str}{email}{phone}")
        return "\n".join(lines)
