"""Local draft persistence and write previews."""

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult
from app.services.context_resolver import validate_and_adjust_event_times


class PrepareWriteHandler:
    """Persist drafts and freeze write arguments for subsequent confirmation."""

    def _handle_draft(
        self, intent: IntentAnalysisResult, context: GatewayUserContext, chat_id: int
    ) -> list[str]:
        if not intent.draft:
            return ["⚠️ Yêu cầu bản nháp không hợp lệ (thiếu payload)."]

        draft_dict = intent.draft.model_dump()
        saved = self.conversation_history_store.set_active_draft(
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
            err = getattr(self.conversation_history_store, "last_draft_error", None)
            if err == "LOCK_TIMEOUT":
                return ["⚠️ Hệ thống đang bận xử lý một thao tác khác trên bản nháp. Vui lòng thử lại sau giây lát."]
            return ["⚠️ Bản nháp đã được chỉnh sửa ở một tác vụ khác. Không thể ghi đè bản nháp mới hơn."]

        draft_id = saved.get("draft_id")
        executing_warning = ""
        if draft_id:
            # Hủy toàn bộ pending preview của phiên bản cũ
            self.confirmation_service.cancel_pending_draft_confirmations(
                user_id=context.user_id,
                chat_id=chat_id,
                telegram_binding_id=context.telegram_binding_id or "default",
                draft_id=draft_id,
            )
            # Kiểm tra xem có preview nào của draft_id đang executing không
            with sqlite3.connect(self.confirmation_service.db_path) as conn:
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

    async def _handle_prepare_write(
        self, intent: IntentAnalysisResult, context: GatewayUserContext, chat_id: int
    ) -> list[str]:
        tool_name = intent.tool_name
        tool_def = self.registry.get_tool(tool_name) if tool_name else None
        if not tool_def:
            return [f"⚠️ Công cụ ghi `{tool_name}` không tồn tại trong hệ thống."]

        if tool_def.required_permission and not context.has_permission(tool_def.required_permission):
            return [f"⚠️ Bạn chưa được cấp quyền `{tool_def.required_permission}` để thực hiện thao tác này."]

        if tool_name == "knowledge.documents.index":
            return await self._execute_knowledge_tool(tool_name, dict(intent.parameters), context, chat_id)

        if tool_name == "business.preview":
            from app.business.operations import prepare as prepare_operation
            return await prepare_operation(context)

        if tool_name == "business.draft":
            # The model can label local draft collection as a write; it is still
            # a read tool and must retain its normal preview/confirmation flow.
            from app.business.operations import read as operation_read
            return await operation_read(tool_name, dict(intent.parameters), context)

        if tool_name in ('crm.leads.create', 'crm.create', 'crm.leads.update', 'crm.update', 'crm.write'):
            from app.core.config import get_settings
            if get_settings().ENABLE_BUSINESS_ACTIONS:
                from app.business.operations import read as operation_read, prepare as prepare_operation
                values = dict(intent.parameters)
                target_id = values.pop('lead_id', None)
                if 'email' in values:
                    values['email_from'] = values.pop('email')
                if 'stage_id' in values and len(values) > 1:
                    return ['Chuyển giai đoạn và sửa thông tin cần hai bản xem trước riêng. Hãy chọn thao tác muốn làm trước.']
                operation = 'stage' if 'stage_id' in values else 'update' if target_id else 'create'
                data = {'values': values}
                if target_id:
                    data['target_id'] = target_id
                messages = await operation_read('business.draft', {'action': 'start', 'business_action': f'business.crm.{operation}', 'data': data}, context)
                if (self.conversation_history_store.get_session(context.tenant, context.user_id, chat_id, context.telegram_binding_id, database=context.database).get('active_draft') or {}).get('business_action') != f'business.crm.{operation}':
                    return messages
                return await prepare_operation(context)

        if tool_name == "sales.quotation.preview":
            from app.business.workflow import prepare as prepare_quote
            return await prepare_quote(context)

        # Chuẩn hóa tham số tại adapter
        params = self._normalize_write_parameters(intent.parameters)
        binding_id = context.telegram_binding_id or "default"

        if tool_name and tool_name.startswith("workspace."):
            from app.workspace.agent import format_result, preview_text, validate_tool
            try:
                service_name, kind = validate_tool(tool_name, intent.parameters)
                if kind != "write":
                    return ["⚠️ Đây là công cụ đọc, không phải thao tác ghi."]
                display = preview_text(service_name, intent.parameters)
                if len(display) > 18000:
                    return ["⚠️ Nội dung quá lớn để xác nhận trong chat. Hãy chia thành thao tác nhỏ hơn."]
                preview = await self.gateway_client.workspace_prepare(context.user_id, service_name, intent.parameters)
                if preview.get("status") != "OK":
                    return format_result(service_name, preview)
                _, text = self.confirmation_service.create_confirmation(
                    user_id=context.user_id, chat_id=chat_id, telegram_binding_id=binding_id,
                    telegram_user_id=context.telegram_user_id, owner_key=context.owner_key,
                    action=tool_name, arguments=preview["arguments"], target_snapshot=preview.get("snapshot"),
                    preview_text=display,
                )
                return [text]
            except (ValueError, KeyError, TypeError) as exc:
                return [f"⚠️ Yêu cầu Workspace chưa hợp lệ: {exc}"]

        # Tasks create
        if tool_name in ["tasks.personal.create", "tasks.create"]:
            title = params.get("title")
            if not title:
                return ["⚠️ Thiếu tiêu đề công việc để tạo. Vui lòng cung cấp tiêu đề."]
            due_at = params.get("due_at") or params.get("due_date")
            priority = params.get("priority") or "medium"
            conf_id, preview = self.confirmation_service.create_confirmation(
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

            conf_id, preview = self.confirmation_service.create_confirmation(
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

            conf_id, preview = self.confirmation_service.create_confirmation(
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

            conf_id, preview = self.confirmation_service.create_confirmation(
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
                session = self.conversation_history_store.get_session(
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
                session = self.conversation_history_store.get_session(
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

            conf_id, preview = self.confirmation_service.create_confirmation(
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
                sess = self.conversation_history_store.get_session(
                    tenant=context.tenant, user_id=context.user_id, chat_id=chat_id, binding_id=binding_id, database=context.database
                )
                act_dr = sess.get("active_draft")
                if act_dr:
                    draft_id = act_dr.get("draft_id")
                    draft_ver = act_dr.get("version", 1)

            conf_id, preview = self.confirmation_service.create_confirmation(
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
            conf_id, preview = self.confirmation_service.create_confirmation(
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

            conf_id, preview = self.confirmation_service.create_confirmation(
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
