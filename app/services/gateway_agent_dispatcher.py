import json
import re
import secrets
import logging
from datetime import datetime, UTC, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.domain.gateway_context import GatewayPermission, GatewayUserContext
from app.providers.gateway_calendar import GatewayCalendarProvider
from app.providers.gateway_crm import GatewayCRMProvider
from app.providers.gateway_gmail import GatewayGmailProvider
from app.providers.tasks.local import LocalTaskProvider
from app.services.confirmation_service import confirmation_service
from app.services.conversation_state import conversation_state_manager
from app.services.gateway_client import gateway_client

logger = logging.getLogger(__name__)


class GatewayAgentDispatcher:
    """Bộ điều phối hội thoại và nghiệp vụ Agent tích hợp qua Cổng Gateway."""

    def __init__(self):
        self.local_tasks = LocalTaskProvider()
        self.calendar_provider = GatewayCalendarProvider()
        self.gmail_provider = GatewayGmailProvider()
        self.crm_provider = GatewayCRMProvider()

    async def dispatch(
        self,
        context: GatewayUserContext,
        chat_id: int,
        telegram_user_id: int,
        raw_text: str,
    ) -> List[str]:
        clean_text = raw_text.strip()
        lower_text = clean_text.lower()
        user_id = context.user_id
        binding_id = context.telegram_binding_id or "default"

        # ---------------------------------------------------------
        # 1. Kiểm tra lệnh Xác nhận (/confirm <id>) & Hủy (/cancel <id>)
        # ---------------------------------------------------------
        confirm_match = re.match(r"^/confirm\s+(\S+)$", clean_text, re.IGNORECASE)
        if confirm_match:
            conf_id = confirm_match.group(1).strip()
            return await self._handle_confirm(context, chat_id, telegram_user_id, conf_id)

        cancel_match = re.match(r"^/cancel\s+(\S+)$", clean_text, re.IGNORECASE)
        if cancel_match:
            conf_id = cancel_match.group(1).strip()
            success, msg = confirmation_service.cancel_confirmation(
                confirmation_id=conf_id,
                user_id=user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
            )
            return [msg if success else f"⚠️ {msg}"]

        # Chặn các câu đồng ý suông thiếu mã xác nhận
        bare_agrees = [
            "đồng ý", "xác nhận", "chấp nhận", "ok", "yes", "confirm",
            "thực hiện đi", "làm đi", "tiến hành đi", "tiến hành",
        ]
        if lower_text in bare_agrees:
            return [
                "⚠️ Vui lòng cung cấp mã xác nhận cụ thể theo cú pháp: `/confirm <mã>` hoặc `/cancel <mã>`. "
                "Hệ thống không thể thực thi nếu chỉ nhận câu đồng ý mà thiếu mã xác nhận."
            ]

        # ---------------------------------------------------------
        # 2. Xử lý trạng thái hội thoại nhiều lượt (Conversation State)
        # ---------------------------------------------------------
        state = conversation_state_manager.get_state(user_id, binding_id)
        if state and "pending_intent" in state:
            handled, res = await self._handle_multi_turn(
                context, chat_id, telegram_user_id, clean_text, state
            )
            if handled:
                return res

        # ---------------------------------------------------------
        # 3. Chặn hành vi xóa dữ liệu
        # ---------------------------------------------------------
        if self._is_delete_intent(lower_text):
            return [
                "⚠️ Thao tác xóa dữ liệu hiện chưa được hỗ trợ qua cổng Telegram này "
                "theo chính sách an toàn và bảo toàn dữ liệu. Vui lòng thực hiện trên giao diện web Odoo."
            ]

        # ---------------------------------------------------------
        # 4. Các thao tác ghi / sửa dữ liệu cụ thể (ưu tiên trước đọc)
        # ---------------------------------------------------------
        # 4.1 Tasks - Tạo công việc
        task_create_match = self._match_task_create(clean_text)
        if task_create_match:
            return await self._prepare_task_create(context, chat_id, task_create_match)

        # 4.2 Tasks - Sửa công việc
        task_update_match = self._match_task_update(clean_text)
        if task_update_match:
            return await self._prepare_task_update(context, chat_id, task_update_match)

        # 4.3 Tasks - Hoàn thành công việc
        task_done_match = self._match_task_done(clean_text)
        if task_done_match:
            return await self._prepare_task_complete(context, chat_id, task_done_match)

        # 4.4 Calendar - Tạo sự kiện lịch
        cal_create_match = self._match_calendar_create(clean_text)
        if cal_create_match:
            return await self._prepare_calendar_create(context, chat_id, cal_create_match)

        # 4.5 Calendar - Cập nhật sự kiện lịch
        cal_update_match = self._match_calendar_update(clean_text)
        if cal_update_match:
            return await self._prepare_calendar_update(context, chat_id, cal_update_match)

        # 4.6 Gmail - Gửi email
        email_send_match = self._match_email_send(clean_text)
        if email_send_match:
            return await self._prepare_email_send(context, chat_id, email_send_match)

        # 4.7 CRM - Tạo CRM Lead
        crm_create_match = self._match_crm_create(clean_text)
        if crm_create_match:
            return await self._prepare_crm_create(context, chat_id, crm_create_match)

        # 4.8 CRM - Cập nhật CRM Lead
        crm_update_match = self._match_crm_update(clean_text)
        if crm_update_match:
            return await self._prepare_crm_update(context, chat_id, crm_update_match)

        # ---------------------------------------------------------
        # 5. Các thao tác tra cứu / đọc cụ thể
        # ---------------------------------------------------------
        # 5.1 CRM - Tìm kiếm Lead (/crm search ...)
        crm_search_term = self._match_crm_search(clean_text)
        if crm_search_term is not None:
            return await self._handle_crm_search(context, crm_search_term)

        # 5.2 Calendar - Tra cứu lịch
        if self._is_calendar_view_intent(lower_text):
            return await self._handle_calendar_view(context)

        # 5.3 Gmail - Đọc email chưa đọc
        if self._is_email_read_intent(lower_text):
            return await self._handle_email_read(context)

        # ---------------------------------------------------------
        # 6. Tham chiếu ngữ cảnh hội thoại (Context References)
        # ---------------------------------------------------------
        # 6.1 "gửi bản vừa soạn" / "gửi thư vừa soạn" / "gửi email vừa rồi"
        draft_send_phrases = ["gửi bản vừa soạn", "gửi thư vừa soạn", "gửi email vừa rồi", "gửi bản nháp vừa soạn", "gửi email vừa soạn"]
        if any(p in lower_text for p in draft_send_phrases):
            state = conversation_state_manager.get_state(user_id, binding_id)
            draft = state.get("last_draft_email")
            if not draft:
                return [
                    "⚠️ Không tìm thấy bản thảo email nào vừa được soạn trong phiên làm việc hiện tại. "
                    "Vui lòng soạn email theo cú pháp: `/email send <người_nhận> | <tiêu_đề> | <nội_dung>`."
                ]
            return await self._prepare_email_send(context, chat_id, draft)

        # 6.2 "sửa giờ cuộc họp đó" / "dời lịch cuộc họp đó"
        cal_ref_phrases = ["sửa giờ cuộc họp", "dời lịch cuộc họp", "đổi giờ cuộc họp", "đổi lịch họp"]
        if any(p in lower_text for p in cal_ref_phrases):
            state = conversation_state_manager.get_state(user_id, binding_id)
            recent_evs = state.get("recent_calendar_events", [])
            if not recent_evs:
                return [
                    "⚠️ Không tìm thấy sự kiện lịch nào trong ngữ cảnh gần đây. "
                    "Vui lòng tra cứu lịch trước bằng lệnh `/calendar` hoặc chỉ định mã sự kiện: `/calendar update <mã_sự_kiện> | ...`."
                ]
            if len(recent_evs) > 1:
                # Có nhiều cuộc họp: yêu cầu người dùng chọn
                lines = ["❓ Trong lịch gần đây của bạn có nhiều cuộc họp. Bạn muốn sửa cuộc họp nào?"]
                for idx, ev in enumerate(recent_evs, 1):
                    lines.append(f"{idx}. `[ID {ev.get('id')}]` **{ev.get('summary')}** ({ev.get('start', 'N/A')})")
                lines.append("\n👉 *Vui lòng phản hồi số thứ tự hoặc mã sự kiện kèm thời gian mới (ví dụ: `1 | 2026-10-27T15:00:00`).*")
                conversation_state_manager.update_state(
                    user_id,
                    binding_id,
                    {"pending_intent": "select_calendar_event", "data": {"recent_events": recent_evs}},
                )
                return ["\n".join(lines)]
            else:
                # Đúng 1 sự kiện duy nhất
                ev_id = recent_evs[0].get("id")
                return await self._prepare_calendar_update(
                    context, chat_id, {"event_id": ev_id, "summary": recent_evs[0].get("summary", "")}
                )

        # ---------------------------------------------------------
        # 7. Xử lý câu ngôn ngữ tự nhiên qua LLM (nếu có cấu hình)
        # ---------------------------------------------------------
        parsed_action = await self._call_llm_parse_action(context, clean_text)
        if parsed_action and "action" in parsed_action:
            act = parsed_action["action"]
            args = parsed_action.get("arguments", {})

            if act == "tasks.create" and context.has_permission(GatewayPermission.TASKS_WRITE):
                return await self._prepare_task_create(context, chat_id, args)
            elif act == "tasks.update" and context.has_permission(GatewayPermission.TASKS_WRITE):
                return await self._prepare_task_update(context, chat_id, args)
            elif act == "tasks.complete" and context.has_permission(GatewayPermission.TASKS_WRITE):
                return await self._prepare_task_complete(context, chat_id, args.get("task_id", ""))
            elif act == "calendar.create" and context.has_permission(GatewayPermission.CALENDAR_WRITE):
                return await self._prepare_calendar_create(context, chat_id, args)
            elif act == "calendar.update" and context.has_permission(GatewayPermission.CALENDAR_WRITE):
                return await self._prepare_calendar_update(context, chat_id, args)
            elif act == "calendar.view" and context.has_permission(GatewayPermission.CALENDAR_READ):
                return await self._handle_calendar_view(context)
            elif act == "gmail.send" and context.has_permission(GatewayPermission.GMAIL_SEND):
                return await self._prepare_email_send(context, chat_id, args)
            elif act == "gmail.read" and context.has_permission(GatewayPermission.GMAIL_READ):
                return await self._handle_email_read(context)
            elif act == "crm.create" and context.has_permission(GatewayPermission.CRM_CREATE):
                return await self._prepare_crm_create(context, chat_id, args)
            elif act == "crm.update" and context.has_permission(GatewayPermission.CRM_WRITE):
                return await self._prepare_crm_update(context, chat_id, args)
            elif act == "crm.search" and context.has_permission(GatewayPermission.CRM_READ):
                return await self._handle_crm_search(context, args.get("search_term", ""))

        # 8. Phản hồi an toàn khi không hiểu hoặc LLM chưa sẵn sàng
        return [
            "⚠️ Yêu cầu này chưa được hỗ trợ qua Gateway hoặc dịch vụ AI phân tích tự nhiên hiện chưa được kích hoạt "
            "(chỉ hỗ trợ: điểm tin, xem hồ sơ cá nhân, kiểm tra kết nối, công việc cá nhân, Google Calendar, Gmail và Odoo CRM). "
            "Vui lòng sử dụng các lệnh trực tiếp (ví dụ: `/task`, `/calendar`, `/email`, `/crm`, `/help`) để thực hiện yêu cầu."
        ]

    # =========================================================================
    # LOGIC XÁC NHẬN VÀ THỰC THI (CONFIRM EXECUTION)
    # =========================================================================

    async def _handle_confirm(
        self,
        context: GatewayUserContext,
        chat_id: int,
        telegram_user_id: int,
        conf_id: str,
    ) -> List[str]:
        user_id = context.user_id
        binding_id = context.telegram_binding_id or "default"

        valid, err_msg, action_data = confirmation_service.claim_and_validate(
            confirmation_id=conf_id,
            user_id=user_id,
            chat_id=chat_id,
            telegram_binding_id=binding_id,
        )
        if not valid or not action_data:
            return [f"⚠️ Xác nhận không thành công: {err_msg}"]

        action = action_data["action"]
        args = action_data["arguments"]
        snapshot = action_data.get("target_snapshot")

        # 1. Tái xác minh quyền thực tế trước khi ghi
        if action.startswith("tasks.") and not context.has_permission(GatewayPermission.TASKS_WRITE):
            return ["⚠️ Quyền thực thi bị từ chối: Bạn không có quyền ghi công việc (`tasks.write`)."]

        if action.startswith("calendar.") and not context.has_permission(GatewayPermission.CALENDAR_WRITE):
            return ["⚠️ Quyền thực thi bị từ chối: Tài khoản chưa được cấp quyền chỉnh sửa lịch (`calendar.write`)."]

        if action == "gmail.send" and not context.has_permission(GatewayPermission.GMAIL_SEND):
            return ["⚠️ Quyền thực thi bị từ chối: Tài khoản chưa được cấp quyền gửi thư Gmail (`gmail.send`)."]

        if action == "crm.create" and not context.has_permission(GatewayPermission.CRM_CREATE):
            return ["⚠️ Quyền thực thi bị từ chối: Bạn không có quyền tạo lead CRM (`crm.create`)."]

        if action == "crm.update" and not context.has_permission(GatewayPermission.CRM_WRITE):
            return ["⚠️ Quyền thực thi bị từ chối: Bạn không có quyền chỉnh sửa lead CRM (`crm.write`)."]

        # 2. Thực thi tương ứng theo provider
        try:
            # Nhóm Local Tasks
            if action == "tasks.create":
                with confirmation_service.get_connection() as conn:
                    task_id = f"task_{secrets.token_hex(4)}"
                    now_str = datetime.now(UTC).isoformat()
                    tags_json = json.dumps(args.get("tags", []))
                    conn.execute(
                        """
                        INSERT INTO tasks (id, title, description, status, priority, due_at, timezone, project, tags, created_at, updated_at, owner_key)
                        VALUES (?, ?, ?, 'TODO', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            args["title"],
                            args.get("description", ""),
                            args.get("priority", "medium"),
                            args.get("due_at"),
                            context.timezone,
                            args.get("project"),
                            tags_json,
                            now_str,
                            now_str,
                            context.owner_key,
                        ),
                    )
                    conn.execute(
                        "UPDATE pending_confirmations SET status = 'completed', updated_at = ? WHERE confirmation_id = ?",
                        (now_str, conf_id.strip()),
                    )
                    conn.commit()

                return [
                    f"✅ **Đã tạo thành công công việc cá nhân:**\n"
                    f"- Mã: `{task_id}`\n"
                    f"- Tiêu đề: **{args['title']}**\n"
                    f"- Mức ưu tiên: `{args.get('priority', 'medium')}`\n"
                    f"- Hạn chót: {args.get('due_at') or 'Không có'}"
                ]

            elif action == "tasks.update":
                task_id = args["task_id"]
                cur_task = await self.local_tasks.get_task(task_id, owner_key=context.owner_key)
                if not cur_task:
                    confirmation_service.mark_failed(conf_id, "Task not found")
                    return [f"⚠️ Không tìm thấy công việc cá nhân `{task_id}`."]

                altered = False
                if snapshot:
                    if cur_task.get("updated_at") != snapshot.get("updated_at"):
                        altered = True
                    for field in ["title", "status", "priority", "due_at"]:
                        if cur_task.get(field) != snapshot.get(field):
                            altered = True
                if altered:
                    confirmation_service.mark_failed(conf_id, "Target data altered after preview")
                    return [
                        "⚠️ Dữ liệu công việc đích đã bị thay đổi kể từ khi tạo bản xem trước. "
                        "Thao tác ghi bị hủy để tránh ghi đè dữ liệu sai lệch. Vui lòng tạo yêu cầu mới."
                    ]
                
                with confirmation_service.get_connection() as conn:
                    now_str = datetime.now(UTC).isoformat()
                    fields = []
                    params = []
                    for k, v in args["updates"].items():
                        if k in ["title", "description", "status", "priority", "due_at", "timezone", "project"]:
                            fields.append(f"{k} = ?")
                            params.append(v)
                        elif k == "tags":
                            fields.append("tags = ?")
                            params.append(json.dumps(v))
                    if fields:
                        fields.append("updated_at = ?")
                        params.append(now_str)
                        if context.owner_key:
                            query = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND owner_key = ?"
                            params.extend([task_id, context.owner_key])
                        else:
                            query = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND (owner_key IS NULL OR owner_key = '')"
                            params.append(task_id)
                        conn.execute(query, params)
                    conn.execute(
                        "UPDATE pending_confirmations SET status = 'completed', updated_at = ? WHERE confirmation_id = ?",
                        (now_str, conf_id.strip()),
                    )
                    conn.commit()

                return [
                    f"✅ **Đã cập nhật công việc cá nhân thành công:**\n"
                    f"- Mã: `{task_id}`\n"
                    f"- Tiêu đề: **{args['updates'].get('title', cur_task.get('title'))}**\n"
                    f"- Trạng thái: `{args['updates'].get('status', cur_task.get('status'))}`"
                ]

            elif action == "tasks.complete":
                task_id = args["task_id"]
                cur_task = await self.local_tasks.get_task(task_id, owner_key=context.owner_key)
                if not cur_task:
                    confirmation_service.mark_failed(conf_id, "Task not found")
                    return [f"⚠️ Không tìm thấy công việc cá nhân `{task_id}`."]

                altered = False
                if snapshot:
                    if cur_task.get("updated_at") != snapshot.get("updated_at"):
                        altered = True
                    for field in ["title", "status", "priority", "due_at"]:
                        if cur_task.get(field) != snapshot.get(field):
                            altered = True
                if altered:
                    confirmation_service.mark_failed(conf_id, "Target data altered after preview")
                    return [
                        "⚠️ Dữ liệu công việc đích đã bị thay đổi kể từ khi tạo bản xem trước. "
                        "Thao tác ghi bị hủy để tránh ghi đè dữ liệu sai lệch. Vui lòng tạo yêu cầu mới."
                    ]

                if cur_task.get("status") in ["DONE", "COMPLETED"]:
                    confirmation_service.mark_completed(conf_id, {"task_id": task_id, "status": "DONE"})
                    return [f"ℹ️ Công việc `{task_id}` vốn đã ở trạng thái hoàn thành."]

                with confirmation_service.get_connection() as conn:
                    now_str = datetime.now(UTC).isoformat()
                    if context.owner_key:
                        conn.execute("UPDATE tasks SET status = 'DONE', updated_at = ? WHERE id = ? AND owner_key = ?", (now_str, task_id, context.owner_key))
                    else:
                        conn.execute("UPDATE tasks SET status = 'DONE', updated_at = ? WHERE id = ? AND (owner_key IS NULL OR owner_key = '')", (now_str, task_id))
                    conn.execute(
                        "UPDATE pending_confirmations SET status = 'completed', updated_at = ? WHERE confirmation_id = ?",
                        (now_str, conf_id.strip()),
                    )
                    conn.commit()

                return [
                    f"✅ **Đã đánh dấu hoàn thành công việc:**\n"
                    f"- Mã: `{task_id}`\n"
                    f"- Tiêu đề: **{cur_task.get('title')}**\n"
                    f"- Trạng thái: ✅ `DONE`"
                ]

            # Nhóm Gateway Actions (Calendar, Gmail, CRM)
            elif action in ["calendar.create", "calendar.update", "gmail.send", "crm.create", "crm.update"]:
                # Sử dụng operation_id đã sinh và lưu bền vững từ lúc tạo preview; tuyệt đối không fallback sinh ID mới
                operation_id = action_data.get("operation_id")
                if not operation_id:
                    confirmation_service.mark_unknown(
                        conf_id,
                        "Thiếu operation_id từ dữ liệu xem trước cũ (chặn thực thi lặp)",
                    )
                    return [
                        "⚠️ Bản xem trước thiếu mã định danh thao tác (`operation_id`). "
                        "Để bảo đảm an toàn và không thực thi lặp lại, thao tác bị chặn và chuyển sang đối soát. "
                        "Vui lòng tạo yêu cầu xem trước mới."
                    ]

                try:
                    resp = await gateway_client.execute_action(
                        user_id=user_id,
                        operation_id=operation_id,
                        telegram_user_id=telegram_user_id,
                        chat_id=chat_id,
                        telegram_binding_id=binding_id,
                        action=action,
                        arguments=args,
                    )
                except Exception as net_err:
                    logger.error("Network error during execute_action %s: %s", operation_id, net_err)
                    confirmation_service.mark_unknown(conf_id, f"Mất kết nối sau khi gửi request: {net_err}")
                    resp = {
                        "status": "unknown_result",
                        "message": f"Mất kết nối mạng tới Gateway sau khi gửi yêu cầu ({net_err}).",
                    }

                status_val = str(resp.get("status", "")).lower()
                if status_val == "completed":
                    confirmation_service.mark_completed(conf_id, resp)
                elif status_val in ["unknown_result", "in_progress", "timeout"]:
                    confirmation_service.mark_unknown(conf_id, resp.get("message") or status_val)
                else:
                    confirmation_service.mark_failed(conf_id, resp.get("message") or status_val or "Action execution failed")

                return self._format_action_result(action, resp)

            else:
                confirmation_service.mark_failed(conf_id, f"Unsupported action: {action}")
                return [f"⚠️ Không nhận diện được loại hành động: `{action}`."]

        except Exception as e:
            curr = confirmation_service.get_confirmation(conf_id)
            if not curr or curr.get("status") != "unknown_result":
                confirmation_service.mark_failed(conf_id, str(e))
            logger.error("Error executing confirmed action %s: %s", action, e, exc_info=True)
            return [f"⚠️ Đã có sự cố xảy ra trong quá trình thực thi hành động `{action}`: {str(e)}."]

    def _format_action_result(self, action: str, resp: Dict[str, Any]) -> List[str]:
        raw_status = str(resp.get("status", ""))
        status_val = raw_status.lower()
        msg = resp.get("message") or ""

        if status_val == "completed":
            result = resp.get("result", {})
            if action in ["calendar.create", "calendar.update"]:
                if not result.get("id"):
                    return ["⚠️ Dịch vụ Google Calendar không trả về mã sự kiện sau khi thực thi."]
                if action == "calendar.create":
                    return [
                        f"✅ **Đã tạo thành công sự kiện Lịch Google:**\n"
                        f"- Tiêu đề: **{result.get('summary')}**\n"
                        f"- Bắt đầu: {result.get('start', {}).get('dateTime') or result.get('start', {}).get('date') or result.get('start')}\n"
                        f"- Kết thúc: {result.get('end', {}).get('dateTime') or result.get('end', {}).get('date') or result.get('end')}\n"
                        f"- Liên kết: {result.get('htmlLink', 'N/A')}"
                    ]
                else:
                    return [
                        f"✅ **Đã cập nhật sự kiện Lịch Google thành công:**\n"
                        f"- Tiêu đề: **{result.get('summary', 'Đã cập nhật')}**\n"
                        f"- Bắt đầu: {result.get('start', {}).get('dateTime') or result.get('start', {}).get('date') or result.get('start') or 'N/A'}\n"
                        f"- Kết thúc: {result.get('end', {}).get('dateTime') or result.get('end', {}).get('date') or result.get('end') or 'N/A'}\n"
                        f"- Liên kết: {result.get('htmlLink', 'N/A')}"
                    ]
            elif action == "gmail.send":
                if not result.get("id"):
                    return ["⚠️ Dịch vụ Google Gmail không trả về mã thư sau khi gửi."]
                return [
                    f"✅ **Đã gửi thư Gmail thành công:**\n"
                    f"- Mã thư: `{result.get('id')}`\n"
                    f"- Trạng thái: Đã gửi qua máy chủ thư của Google"
                ]
            elif action == "crm.create":
                lead_id = result.get("id") or result.get("lead", {}).get("id")
                if not lead_id:
                    return [
                        "⚠️ Thao tác tạo cơ hội CRM hoàn thành nhưng không nhận được mã bản ghi (ID) từ hệ thống. "
                        "Kết quả chưa được xác minh đầy đủ, vui lòng kiểm tra trên Odoo trước khi thực hiện lại."
                    ]
                lead_name = result.get("name") or result.get("lead", {}).get("name")
                expected_rev = result.get("expected_revenue")
                if expected_rev is None and isinstance(result.get("lead"), dict):
                    expected_rev = result["lead"].get("expected_revenue")

                lines = [
                    f"✅ **Đã tạo cơ hội CRM mới trên Odoo:**",
                    f"- ID: `{lead_id}`",
                ]
                if lead_name:
                    lines.append(f"- Tên cơ hội: **{lead_name}**")
                if expected_rev is not None:
                    lines.append(f"- Doanh thu dự kiến: {float(expected_rev):,.0f} VNĐ")
                return ["\n".join(lines)]

            elif action == "crm.update":
                lead_id = result.get("id") or result.get("lead", {}).get("id")
                if not lead_id:
                    return [
                        "⚠️ Thao tác cập nhật cơ hội CRM hoàn thành nhưng không nhận được mã bản ghi (ID) từ hệ thống. "
                        "Kết quả chưa được xác minh đầy đủ, vui lòng kiểm tra trên Odoo trước khi thực hiện lại."
                    ]
                return [
                    f"✅ **Đã cập nhật cơ hội CRM `{lead_id}` trên Odoo thành công.**"
                ]
            return ["✅ Thao tác thực thi thành công."]

        elif raw_status.upper() == "CONFLICT":
            return [
                "⚠️ Dữ liệu đích trên máy chủ (Google/Odoo) đã bị thay đổi kể từ khi tạo bản xem trước. "
                "Thao tác ghi bị hủy để tránh xung đột dữ liệu (ETag/Write Date). Vui lòng tạo lại yêu cầu xem trước mới."
            ]
        elif raw_status.upper() == "RECONNECT_REQUIRED":
            return [
                "⚠️ Tài khoản dịch vụ ngoại vi cần được kết nối lại do phiên ủy quyền đã hết hạn hoặc bị thu hồi. "
                "Vui lòng truy cập Cổng Portal để cấp lại quyền truy cập."
            ]
        elif raw_status.upper() == "NOT_CONNECTED":
            return ["⚠️ Tài khoản dịch vụ ngoại vi chưa được liên kết. Vui lòng liên kết qua Cổng Portal."]
        elif raw_status.upper() == "PERMISSION_DENIED":
            return [f"⚠️ Dịch vụ từ chối thực hiện do thiếu quyền hoặc quyền bị thu hồi: {msg or 'PERMISSION_DENIED'}."]
        elif status_val in ["unknown_result", "in_progress", "timeout"]:
            return [
                "⚠️ Thao tác đang được xử lý hoặc chưa rõ kết quả cuối cùng từ dịch vụ ngoài (Gateway/Google/Odoo). "
                "Hệ thống đã lưu lại trạng thái để đối soát và chặn thực thi lặp lại. "
                "Vui lòng kiểm tra lại trên hệ thống (Google Calendar, Gmail hoặc Odoo CRM) trước khi thực hiện hành động mới."
            ]
        else:
            return [f"⚠️ Thực thi thất bại: {msg or 'Lỗi từ dịch vụ ngoại vi'}."]

    # =========================================================================
    # NHÓM LOCAL TASKS (XEM, TẠO, SỬA, HOÀN THÀNH, CHẶN XÓA)
    # =========================================================================

    def _is_delete_intent(self, text: str) -> bool:
        phrases = ["xóa việc", "xóa task", "xóa công việc", "/task delete", "delete task"]
        return any(p in text for p in phrases)

    def _match_task_create(self, text: str) -> Optional[Dict[str, Any]]:
        # /task create <title> | [due_at] | [priority]
        m = re.match(r"^/task\s+create\s+(.+)$", text, re.IGNORECASE)
        if m:
            parts = [p.strip() for p in m.group(1).split("|")]
            title = parts[0]
            due_at = parts[1] if len(parts) > 1 and parts[1] else None
            priority = parts[2] if len(parts) > 2 and parts[2] else "medium"
            return {"title": title, "due_at": due_at, "priority": priority}

        lower = text.lower()
        triggers = ["tạo công việc", "tạo việc", "thêm công việc", "thêm task", "tạo task"]
        for trig in triggers:
            if lower.startswith(trig):
                content = text[len(trig):].strip(" :,-")
                if content:
                    return {"title": content, "due_at": None, "priority": "medium"}
                return {"title": "", "due_at": None, "priority": "medium"}
        return None

    def _match_task_update(self, text: str) -> Optional[Dict[str, Any]]:
        # /task update <task_id> | <new_title>
        m = re.match(r"^/task\s+update\s+(\S+)\s+(.+)$", text, re.IGNORECASE)
        if m:
            task_id = m.group(1).strip()
            new_title = m.group(2).strip()
            return {"task_id": task_id, "title": new_title}
        return None

    def _match_task_done(self, text: str) -> Optional[str]:
        # /task done <task_id>
        m = re.match(r"^/task\s+(?:done|complete)\s+(\S+)$", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        lower = text.lower()
        done_triggers = ["hoàn thành việc", "hoàn thành task", "xong việc", "xong task"]
        for trig in done_triggers:
            if lower.startswith(trig):
                tid = text[len(trig):].strip(" :,-")
                if tid:
                    return tid
        return None

    async def _prepare_task_create(
        self, context: GatewayUserContext, chat_id: int, match_data: Dict[str, Any]
    ) -> List[str]:
        if not context.has_permission(GatewayPermission.TASKS_WRITE):
            return ["⚠️ Tài khoản của bạn chưa có quyền tạo công việc cá nhân (`tasks.write`)."]

        title = match_data.get("title", "").strip()
        if not title:
            # Lưu state để hỏi làm rõ
            conversation_state_manager.update_state(
                context.user_id,
                context.telegram_binding_id,
                {"pending_intent": "create_task", "data": match_data},
            )
            return ["❓ Bạn muốn đặt tiêu đề cho công việc mới là gì?"]

        args = {
            "title": title,
            "due_at": match_data.get("due_at"),
            "priority": match_data.get("priority", "medium"),
            "description": match_data.get("description", ""),
        }
        preview_text = (
            f"📋 **Bản xem trước tạo công việc cá nhân:**\n"
            f"- Tiêu đề: **{title}**\n"
            f"- Mức ưu tiên: `{args['priority']}`\n"
            f"- Hạn chót: {args['due_at'] or 'Không có'}\n"
            f"- Chủ sở hữu: `{context.name}` (`{context.owner_key}`)"
        )
        _, full_msg = confirmation_service.create_confirmation(
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=context.telegram_binding_id or "default",
            action="tasks.create",
            arguments=args,
            preview_text=preview_text,
            owner_key=context.owner_key,
        )
        return [full_msg]

    async def _prepare_task_update(
        self, context: GatewayUserContext, chat_id: int, match_data: Dict[str, Any]
    ) -> List[str]:
        if not context.has_permission(GatewayPermission.TASKS_WRITE):
            return ["⚠️ Tài khoản của bạn chưa có quyền sửa công việc cá nhân (`tasks.write`)."]

        task_id = match_data["task_id"]
        try:
            cur_task = await self.local_tasks.get_task(task_id, owner_key=context.owner_key)
        except KeyError:
            return [f"⚠️ Không tìm thấy công việc `{task_id}` hoặc bạn không có quyền truy cập."]

        new_title = match_data.get("title", cur_task["title"])
        args = {"task_id": task_id, "updates": {"title": new_title}}

        preview_text = (
            f"📝 **Bản xem trước sửa công việc cá nhân:**\n"
            f"- Mã công việc: `{task_id}`\n"
            f"- Tiêu đề cũ: ~~{cur_task['title']}~~\n"
            f"- Tiêu đề mới: **{new_title}**\n"
            f"- Trạng thái hiện tại: `{cur_task['status']}`"
        )
        _, full_msg = confirmation_service.create_confirmation(
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=context.telegram_binding_id or "default",
            action="tasks.update",
            arguments=args,
            preview_text=preview_text,
            owner_key=context.owner_key,
            target_snapshot=cur_task,
        )
        return [full_msg]

    async def _prepare_task_complete(
        self, context: GatewayUserContext, chat_id: int, task_id: str
    ) -> List[str]:
        if not context.has_permission(GatewayPermission.TASKS_WRITE):
            return ["⚠️ Tài khoản của bạn chưa có quyền cập nhật công việc cá nhân (`tasks.write`)."]

        try:
            cur_task = await self.local_tasks.get_task(task_id, owner_key=context.owner_key)
        except KeyError:
            return [f"⚠️ Không tìm thấy công việc `{task_id}` hoặc bạn không có quyền truy cập."]

        if cur_task.get("status") in ["DONE", "COMPLETED"]:
            return [f"ℹ️ Công việc `{task_id}` đã hoàn thành trước đó."]

        args = {"task_id": task_id}
        preview_text = (
            f"✅ **Bản xem trước hoàn thành công việc cá nhân:**\n"
            f"- Mã công việc: `{task_id}`\n"
            f"- Tiêu đề: **{cur_task['title']}**\n"
            f"- Trạng thái trước: `{cur_task['status']}`\n"
            f"- Trạng thái sau: `DONE` (Hoàn thành)"
        )
        _, full_msg = confirmation_service.create_confirmation(
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=context.telegram_binding_id or "default",
            action="tasks.complete",
            arguments=args,
            preview_text=preview_text,
            owner_key=context.owner_key,
            target_snapshot=cur_task,
        )
        return [full_msg]

    # =========================================================================
    # NHÓM GOOGLE CALENDAR
    # =========================================================================

    def _is_calendar_view_intent(self, text: str) -> bool:
        clean = text.strip()
        lower = clean.lower()
        exact_phrases = {
            "/calendar",
            "/calendar list",
            "/calendar view",
            "xem lịch",
            "lịch họp",
            "lịch trình",
            "lịch hôm nay",
            "lịch tuần này",
            "kiểm tra lịch",
        }
        if lower in exact_phrases:
            return True
        prefix_phrases = (
            "xem lịch ",
            "lịch tuần này",
            "lịch của tôi",
            "kiểm tra lịch ",
        )
        return lower.startswith(prefix_phrases)

    def _match_calendar_create(self, text: str) -> Optional[Dict[str, Any]]:
        # /calendar create <summary> | <start_iso> | <end_iso> | [location]
        m = re.match(r"^/calendar\s+create\s+(.+)$", text, re.IGNORECASE)
        if m:
            parts = [p.strip() for p in m.group(1).split("|")]
            summary = parts[0]
            start_time = parts[1] if len(parts) > 1 else ""
            end_time = parts[2] if len(parts) > 2 else ""
            location = parts[3] if len(parts) > 3 else ""
            return {"summary": summary, "start_time": start_time, "end_time": end_time, "location": location}

        lower = text.lower()
        cal_triggers = ["tạo lịch họp", "đặt lịch họp", "tạo sự kiện lịch", "tạo lịch"]
        for trig in cal_triggers:
            if lower.startswith(trig):
                rem = text[len(trig):].strip(" :,-")
                return {"summary": rem, "start_time": "", "end_time": ""}
        return None

    def _match_calendar_update(self, text: str) -> Optional[Dict[str, Any]]:
        # /calendar update <event_id> | <summary> | [start_iso] | [end_iso] | [location]
        m = re.match(r"^/calendar\s+update\s+(\S+)\s+(.+)$", text, re.IGNORECASE)
        if m:
            event_id = m.group(1).strip()
            parts = [p.strip() for p in m.group(2).split("|")]
            summary = parts[0] if len(parts) > 0 and parts[0] else ""
            start_time = parts[1] if len(parts) > 1 and parts[1] else ""
            end_time = parts[2] if len(parts) > 2 and parts[2] else ""
            location = parts[3] if len(parts) > 3 and parts[3] else ""
            return {
                "event_id": event_id,
                "summary": summary,
                "start_time": start_time,
                "end_time": end_time,
                "location": location,
            }
        return None

    async def _handle_calendar_view(self, context: GatewayUserContext) -> List[str]:
        if not context.has_permission(GatewayPermission.CALENDAR_READ):
            return ["⚠️ Tài khoản chưa được cấp quyền xem lịch Google Calendar (`calendar.read`)."]

        now = datetime.now(UTC)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=7)).isoformat()
        res = await self.calendar_provider.get_events(context.user_id, time_min, time_max)
        if res.get("status") != "OK":
            return [f"⚠️ Không thể tải lịch từ Google: {res.get('status')}"]

        events = res.get("events")
        if events is None:
            events = res.get("items", [])

        if not events:
            return ["📅 **Lịch Google (7 ngày tới):**\nBạn không có cuộc họp hoặc sự kiện nào sắp tới."]

        lines = [f"📅 **Lịch Google (7 ngày tới - {len(events)} sự kiện):**"]
        recent_for_context = []
        for i, ev in enumerate(events[:10], 1):
            summary = ev.get("summary") or "Không tiêu đề"
            is_all_day = ev.get("is_all_day", False)
            start_raw = ev.get("start")
            start = (
                start_raw.get("dateTime") or start_raw.get("date")
                if isinstance(start_raw, dict)
                else (str(start_raw) if start_raw else "Chưa rõ")
            )
            end_raw = ev.get("end")
            end = (
                end_raw.get("dateTime") or end_raw.get("date")
                if isinstance(end_raw, dict)
                else (str(end_raw) if end_raw else "")
            )

            if is_all_day:
                time_display = f"{start} (Cả ngày)"
            elif end and end != start:
                time_display = f"{start} ➔ {end}"
            else:
                time_display = start

            ev_id = ev.get("id")
            if ev_id:
                recent_for_context.append({"id": ev_id, "summary": summary, "start": time_display})
            id_prefix = f"`[ID {ev_id}]` " if ev_id else ""
            lines.append(f"{i}. {id_prefix}**{summary}**\n   ⏰ Thời gian: {time_display}")

        if recent_for_context:
            conversation_state_manager.update_state(
                context.user_id,
                context.telegram_binding_id,
                {"recent_calendar_events": recent_for_context},
            )
        return ["\n".join(lines)]

    async def _prepare_calendar_create(
        self, context: GatewayUserContext, chat_id: int, match_data: Dict[str, Any]
    ) -> List[str]:
        if not context.has_permission(GatewayPermission.CALENDAR_WRITE):
            return ["⚠️ Tài khoản chưa được cấp quyền tạo sự kiện Lịch Google (`calendar.write`)."]

        summary = match_data.get("summary", "").strip()
        start_time = match_data.get("start_time", "").strip()
        end_time = match_data.get("end_time", "").strip()

        if not summary or not start_time:
            conversation_state_manager.update_state(
                context.user_id,
                context.telegram_binding_id,
                {"pending_intent": "create_calendar", "data": match_data},
            )
            return [
                "❓ Vui lòng cung cấp tiêu đề và thời gian sự kiện theo cú pháp: "
                "`/calendar create Tiêu đề | YYYY-MM-DDTHH:MM:SS | YYYY-MM-DDTHH:MM:SS`"
            ]

        # Lấy thông tin tài khoản Google đang kết nối để hiển thị preview
        conns = await gateway_client.get_user_connections(context.user_id)
        google_email = "Chưa rõ"
        if isinstance(conns, dict) and conns.get("status") == "OK":
            google_email = conns.get("google", {}).get("email") or "Chưa liên kết"

        args = {
            "summary": summary,
            "start_time": start_time,
            "end_time": end_time or start_time,
            "location": match_data.get("location", ""),
            "description": match_data.get("description", ""),
        }
        preview_text = (
            f"📅 **Bản xem trước tạo sự kiện Lịch Google:**\n"
            f"- Tài khoản Google: `{google_email}`\n"
            f"- Tiêu đề: **{summary}**\n"
            f"- Thời gian bắt đầu: `{start_time}`\n"
            f"- Thời gian kết thúc: `{args['end_time']}`\n"
            f"- Múi giờ: `{context.timezone}`\n"
            f"- Địa điểm: {args['location'] or 'Trực tuyến / Chưa đặt'}"
        )
        _, full_msg = confirmation_service.create_confirmation(
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=context.telegram_binding_id or "default",
            action="calendar.create",
            arguments=args,
            preview_text=preview_text,
        )
        return [full_msg]

    async def _prepare_calendar_update(
        self, context: GatewayUserContext, chat_id: int, match_data: Dict[str, Any]
    ) -> List[str]:
        if not context.has_permission(GatewayPermission.CALENDAR_WRITE):
            return ["⚠️ Tài khoản chưa được cấp quyền chỉnh sửa Lịch Google (`calendar.write`)."]

        event_id = match_data.get("event_id", "").strip()
        summary = match_data.get("summary", "").strip()
        start_time = match_data.get("start_time", "").strip()
        end_time = match_data.get("end_time", "").strip()
        location = match_data.get("location", "").strip()

        # Lấy dữ liệu thật của sự kiện từ gateway
        ev_resp = await gateway_client.get_calendar_event(context.user_id, event_id)
        if ev_resp.get("status") != "OK" or not ev_resp.get("event"):
            return [f"⚠️ Không thể lấy thông tin sự kiện `{event_id}` từ Google Calendar để cập nhật: {ev_resp.get('message', 'Không tìm thấy')}"]

        current_ev = ev_resp["event"]
        etag = current_ev.get("etag")

        conns = await gateway_client.get_user_connections(context.user_id)
        google_email = "Chưa rõ"
        if isinstance(conns, dict) and conns.get("status") == "OK":
            google_email = conns.get("google", {}).get("email") or "Chưa liên kết"

        args = {
            "event_id": event_id,
            "calendar_id": "primary",
        }
        if etag:
            args["etag"] = etag

        changes_lines = [f"- Mã sự kiện: `{event_id}`"]
        old_summary = current_ev.get("summary") or "Không tiêu đề"
        if summary:
            args["summary"] = summary
            changes_lines.append(f"- Tiêu đề: `{old_summary}` ➔ **{summary}**")
        else:
            changes_lines.append(f"- Tiêu đề: **{old_summary}** (Không đổi)")

        old_start = current_ev.get("start")
        if isinstance(old_start, dict):
            old_start = old_start.get("dateTime") or old_start.get("date")
        if start_time:
            args["start_time"] = start_time
            changes_lines.append(f"- Bắt đầu: `{old_start}` ➔ `{start_time}`")
        elif old_start:
            changes_lines.append(f"- Bắt đầu: `{old_start}` (Không đổi)")

        old_end = current_ev.get("end")
        if isinstance(old_end, dict):
            old_end = old_end.get("dateTime") or old_end.get("date")
        if end_time:
            args["end_time"] = end_time
            changes_lines.append(f"- Kết thúc: `{old_end}` ➔ `{end_time}`")

        if location:
            args["location"] = location
            old_loc = current_ev.get("location") or "Không có"
            changes_lines.append(f"- Địa điểm: `{old_loc}` ➔ `{location}`")

        preview_text = (
            f"📅 **Bản xem trước cập nhật sự kiện Lịch Google:**\n"
            f"- Tài khoản Google: `{google_email}`\n"
            f"- Múi giờ: `{context.timezone}`\n"
            + "\n".join(changes_lines)
        )
        _, full_msg = confirmation_service.create_confirmation(
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=context.telegram_binding_id or "default",
            action="calendar.update",
            arguments=args,
            preview_text=preview_text,
            target_snapshot=current_ev,
        )
        return [full_msg]

    # =========================================================================
    # NHÓM GOOGLE GMAIL
    # =========================================================================

    def _is_email_read_intent(self, text: str) -> bool:
        clean = text.strip()
        lower = clean.lower()
        exact_phrases = {
            "/email",
            "/gmail",
            "/email list",
            "/gmail list",
            "/email unread",
            "/gmail unread",
            "thư chưa đọc",
            "xem email",
            "kiểm tra email",
            "hộp thư đến",
            "email chưa đọc",
        }
        if lower in exact_phrases:
            return True
        prefix_phrases = (
            "thư chưa đọc",
            "xem email",
            "kiểm tra email",
            "hộp thư đến",
        )
        return lower.startswith(prefix_phrases)

    def _match_email_send(self, text: str) -> Optional[Dict[str, Any]]:
        # /email send <to> | <subject> | <body>
        m = re.match(r"^/(?:email|gmail)\s+send\s+(.+)$", text, re.IGNORECASE)
        if m:
            parts = [p.strip() for p in m.group(1).split("|")]
            to = parts[0]
            subject = parts[1] if len(parts) > 1 else ""
            body = parts[2] if len(parts) > 2 else ""
            return {"to": to, "subject": subject, "body": body}

        lower = text.lower()
        if lower.startswith("gửi email cho ") or lower.startswith("gửi thư cho "):
            rem = text.split("cho", 1)[1].strip()
            return {"to": rem, "subject": "", "body": ""}
        return None

    async def _handle_email_read(self, context: GatewayUserContext) -> List[str]:
        if not context.has_permission(GatewayPermission.GMAIL_READ):
            return ["⚠️ Tài khoản chưa được cấp quyền đọc thư Gmail (`gmail.read`)."]

        res = await self.gmail_provider.get_unread(context.user_id)
        if res.get("status") != "OK":
            return [f"⚠️ Không thể kiểm tra Gmail: {res.get('status')}"]

        messages = res.get("messages", [])
        if not messages:
            return ["📬 **Hộp thư Gmail:**\nBạn không có thư mới chưa đọc nào."]

        lines = [f"📬 **Thư Gmail chưa đọc ({len(messages)} thư):**"]
        for i, m in enumerate(messages[:5], 1):
            subject = m.get("subject", "Không có tiêu đề")
            sender = m.get("from", "Không rõ người gửi")
            lines.append(f"{i}. **{subject}**\n   Từ: `{sender}`")
        return ["\n".join(lines)]

    async def _prepare_email_send(
        self, context: GatewayUserContext, chat_id: int, match_data: Dict[str, Any]
    ) -> List[str]:
        if not context.has_permission(GatewayPermission.GMAIL_SEND):
            return ["⚠️ Tài khoản chưa được cấp quyền gửi thư qua Gmail (`gmail.send`)."]

        to = match_data.get("to", "").strip()
        subject = match_data.get("subject", "").strip()
        body = match_data.get("body", "").strip()

        if not to or not subject or not body:
            conversation_state_manager.update_state(
                context.user_id,
                context.telegram_binding_id,
                {"pending_intent": "send_email", "data": match_data},
            )
            return [
                "❓ Vui lòng cung cấp đầy đủ thông tin gửi thư theo cú pháp: "
                "`/email send Người_nhận@example.com | Tiêu đề | Nội dung thư`"
            ]

        conns = await gateway_client.get_user_connections(context.user_id)
        google_email = "Chưa rõ"
        if isinstance(conns, dict) and conns.get("status") == "OK":
            google_email = conns.get("google", {}).get("email") or "Chưa liên kết"

        args = {"to": to, "subject": subject, "body": body}
        preview_text = (
            f"✉️ **Bản xem trước gửi thư Gmail:**\n"
            f"- Tài khoản gửi: `{google_email}`\n"
            f"- Người nhận: `{to}`\n"
            f"- Tiêu đề: **{subject}**\n"
            f"- Nội dung:\n```\n{body}\n```"
        )
        _, full_msg = confirmation_service.create_confirmation(
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=context.telegram_binding_id or "default",
            action="gmail.send",
            arguments=args,
            preview_text=preview_text,
        )
        conversation_state_manager.update_state(
            context.user_id,
            context.telegram_binding_id,
            {"last_draft_email": args},
        )
        return [full_msg]

    # =========================================================================
    # NHÓM ODOO CRM
    # =========================================================================

    def _match_crm_search(self, text: str) -> Optional[str]:
        # /crm search <term>
        m = re.match(r"^/crm\s+search\s*(.*)$", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        lower = text.lower()
        phrases = ["tìm lead", "tìm cơ hội", "tra cứu crm", "danh sách lead", "cơ hội bán hàng"]
        for p in phrases:
            if lower.startswith(p):
                return text[len(p):].strip(" :,-")
        return None

    def _match_crm_create(self, text: str) -> Optional[Dict[str, Any]]:
        # /crm create <name> | [phone] | [email] | [expected_revenue]
        m = re.match(r"^/crm\s+create\s+(.+)$", text, re.IGNORECASE)
        if m:
            parts = [p.strip() for p in m.group(1).split("|")]
            name = parts[0]
            phone = parts[1] if len(parts) > 1 else ""
            email = parts[2] if len(parts) > 2 else ""
            rev = float(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0.0
            return {"name": name, "phone": phone, "email_from": email, "expected_revenue": rev}

        lower = text.lower()
        if lower.startswith("tạo lead ") or lower.startswith("thêm cơ hội "):
            rem = text.split(" ", 2)[2].strip()
            return {"name": rem, "phone": "", "email_from": "", "expected_revenue": 0.0}
        return None

    def _match_crm_update(self, text: str) -> Optional[Dict[str, Any]]:
        # /crm update <lead_id> | [stage_id] | [expected_revenue]
        m = re.match(r"^/crm\s+update\s+(\d+)\s+(.+)$", text, re.IGNORECASE)
        if m:
            lead_id = int(m.group(1))
            parts = [p.strip() for p in m.group(2).split("|")]
            stage_id = int(parts[0]) if parts[0].isdigit() else None
            rev = float(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            return {"lead_id": lead_id, "stage_id": stage_id, "expected_revenue": rev}
        return None

    async def _handle_crm_search(self, context: GatewayUserContext, term: str) -> List[str]:
        if not context.has_permission(GatewayPermission.CRM_READ):
            return ["⚠️ Tài khoản chưa được cấp quyền tra cứu CRM (`crm.read`)."]

        res = await self.crm_provider.search_leads(context.user_id, search_term=term or None, limit=10)
        if res.get("status") != "OK":
            return [f"⚠️ Không thể tra cứu cơ hội CRM: {res.get('status')}"]

        leads = res.get("leads", [])
        if not leads:
            return [f"💼 **CRM Odoo:** Không tìm thấy cơ hội nào phù hợp với từ khóa `{term}`."]

        lines = [f"💼 **Kết quả tra cứu CRM Odoo ({len(leads)} cơ hội):**"]
        for i, ld in enumerate(leads, 1):
            rev = ld.get("expected_revenue") or 0
            lines.append(
                f"{i}. `[ID {ld.get('id')}]` **{ld.get('name')}**\n"
                f"   - Doanh thu dự kiến: {rev:,.0f} VNĐ\n"
                f"   - Khách hàng: {ld.get('contact_name') or ld.get('email_from') or 'N/A'}"
            )
        if len(leads) >= 10:
            lines.append("\n💡 *Gợi ý: Có nhiều bản ghi phù hợp, hãy cung cấp thêm từ khóa chi tiết hơn.*")
        return ["\n".join(lines)]

    async def _prepare_crm_create(
        self, context: GatewayUserContext, chat_id: int, match_data: Dict[str, Any]
    ) -> List[str]:
        if not context.has_permission(GatewayPermission.CRM_CREATE):
            return ["⚠️ Tài khoản chưa được cấp quyền tạo cơ hội CRM (`crm.create`)."]

        name = match_data.get("name", "").strip()
        if not name:
            conversation_state_manager.update_state(
                context.user_id,
                context.telegram_binding_id,
                {"pending_intent": "create_crm", "data": match_data},
            )
            return ["❓ Vui lòng cho biết tên cơ hội / khách hàng muốn tạo trên CRM:"]

        args = {
            "name": name,
            "phone": match_data.get("phone", ""),
            "email_from": match_data.get("email_from", ""),
            "expected_revenue": match_data.get("expected_revenue", 0.0),
            "description": match_data.get("description", ""),
        }
        preview_text = (
            f"💼 **Bản xem trước tạo cơ hội CRM trên Odoo:**\n"
            f"- Tên cơ hội: **{name}**\n"
            f"- Doanh thu dự kiến: {args['expected_revenue']:,.0f} VNĐ\n"
            f"- Điện thoại: `{args['phone'] or 'Không có'}`\n"
            f"- Email: `{args['email_from'] or 'Không có'}`\n"
            f"- Hệ thống Odoo: `{context.tenant}` / `{context.database}`"
        )
        _, full_msg = confirmation_service.create_confirmation(
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=context.telegram_binding_id or "default",
            action="crm.create",
            arguments=args,
            preview_text=preview_text,
        )
        return [full_msg]

    async def _prepare_crm_update(
        self, context: GatewayUserContext, chat_id: int, match_data: Dict[str, Any]
    ) -> List[str]:
        if not context.has_permission(GatewayPermission.CRM_WRITE):
            return ["⚠️ Tài khoản chưa được cấp quyền cập nhật cơ hội CRM (`crm.write`)."]

        lead_id = match_data["lead_id"]

        # Lấy dữ liệu thật của lead từ Odoo CRM qua Gateway
        lead_resp = await gateway_client.get_crm_lead(context.user_id, lead_id)
        if lead_resp.get("status") != "OK" or not lead_resp.get("lead"):
            return [f"⚠️ Không thể lấy thông tin cơ hội CRM ID `{lead_id}` từ Odoo: {lead_resp.get('message', 'Không tìm thấy')}"]

        current_lead = lead_resp["lead"]
        write_date = current_lead.get("write_date")

        args = {
            "lead_id": lead_id,
        }
        if write_date:
            args["expected_write_date"] = write_date

        changes_lines = [
            f"- Tên cơ hội: **{current_lead.get('name', 'Không rõ')}**",
            f"- ID: `{lead_id}`",
        ]

        stage_id = match_data.get("stage_id")
        old_stage = current_lead.get("stage_id")
        if stage_id is not None:
            args["stage_id"] = stage_id
            changes_lines.append(f"- Giai đoạn (Stage ID): `{old_stage}` ➔ `{stage_id}`")
        else:
            changes_lines.append(f"- Giai đoạn (Stage ID): `{old_stage}` (Không đổi)")

        rev = match_data.get("expected_revenue")
        old_rev = current_lead.get("expected_revenue") or 0.0
        if rev is not None:
            args["expected_revenue"] = rev
            changes_lines.append(f"- Doanh thu dự kiến: {old_rev:,.0f} VNĐ ➔ **{rev:,.0f} VNĐ**")
        else:
            changes_lines.append(f"- Doanh thu dự kiến: {old_rev:,.0f} VNĐ (Không đổi)")

        preview_text = (
            f"💼 **Bản xem trước cập nhật cơ hội CRM ID `{lead_id}`:**\n"
            + "\n".join(changes_lines)
        )
        _, full_msg = confirmation_service.create_confirmation(
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=context.telegram_binding_id or "default",
            action="crm.update",
            arguments=args,
            preview_text=preview_text,
            target_snapshot=current_lead,
        )
        return [full_msg]

    # =========================================================================
    # HỘI THOẠI NHIỀU LƯỢT (MULTI-TURN CLARIFICATION)
    # =========================================================================

    async def _handle_multi_turn(
        self,
        context: GatewayUserContext,
        chat_id: int,
        telegram_user_id: int,
        text: str,
        state: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        intent = state.get("pending_intent")
        data = state.get("data", {})

        if intent == "create_task":
            # Người dùng vừa gửi bổ sung tiêu đề công việc
            conversation_state_manager.clear_state(context.user_id, context.telegram_binding_id)
            data["title"] = text.strip()
            res = await self._prepare_task_create(context, chat_id, data)
            return True, res

        elif intent == "send_email":
            # Kiểm tra xem đang thiếu gì
            conversation_state_manager.clear_state(context.user_id, context.telegram_binding_id)
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 2:
                data["subject"] = parts[0]
                data["body"] = parts[1]
            else:
                data["body"] = text.strip()
                if not data.get("subject"):
                    data["subject"] = "Thông báo từ Telegram AI Bot"
            res = await self._prepare_email_send(context, chat_id, data)
            return True, res

        elif intent == "create_crm":
            conversation_state_manager.clear_state(context.user_id, context.telegram_binding_id)
            data["name"] = text.strip()
            res = await self._prepare_crm_create(context, chat_id, data)
            return True, res

        elif intent == "create_calendar":
            conversation_state_manager.clear_state(context.user_id, context.telegram_binding_id)
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 2:
                data["start_time"] = parts[0]
                data["end_time"] = parts[1]
            else:
                data["start_time"] = text.strip()
                data["end_time"] = text.strip()
            res = await self._prepare_calendar_create(context, chat_id, data)
            return True, res

        elif intent == "select_calendar_event":
            conversation_state_manager.clear_state(context.user_id, context.telegram_binding_id)
            recent_events = data.get("recent_events", [])
            parts = [p.strip() for p in text.split("|")]
            choice = parts[0]
            new_time = parts[1] if len(parts) > 1 else ""

            selected_id = None
            selected_summary = ""
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(recent_events):
                    selected_id = recent_events[idx]["id"]
                    selected_summary = recent_events[idx].get("summary", "")
            else:
                for ev in recent_events:
                    if ev["id"] == choice or ev["id"] in choice:
                        selected_id = ev["id"]
                        selected_summary = ev.get("summary", "")
                        break

            if not selected_id:
                return True, [
                    "⚠️ Không nhận diện được sự kiện bạn đã chọn. "
                    "Vui lòng thử lại với cú pháp: `/calendar update <mã_sự_kiện> | <tiêu_đề_mới> | <thời_gian_mới>`."
                ]

            match_update = {"event_id": selected_id, "summary": selected_summary}
            if new_time:
                match_update["start_time"] = new_time
            res = await self._prepare_calendar_update(context, chat_id, match_update)
            return True, res

        return False, []

    # =========================================================================
    # BỘ PHÂN TÍCH Ý ĐỊNH NGÔN NGỮ TỰ NHIÊN (LLM ACTION PARSER)
    # =========================================================================

    async def _call_llm_parse_action(
        self, context: GatewayUserContext, text: str
    ) -> Optional[Dict[str, Any]]:
        """Sử dụng LLM để phân tích yêu cầu tự nhiên thành Action Schema được cấp phép."""
        from app.core.config import get_settings
        settings = get_settings()
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            return None

        allowed_actions = []
        if context.has_permission(GatewayPermission.TASKS_WRITE):
            allowed_actions.extend(["tasks.create", "tasks.update", "tasks.complete"])
        if context.has_permission(GatewayPermission.CALENDAR_WRITE):
            allowed_actions.extend(["calendar.create", "calendar.update"])
        if context.has_permission(GatewayPermission.CALENDAR_READ):
            allowed_actions.append("calendar.view")
        if context.has_permission(GatewayPermission.GMAIL_SEND):
            allowed_actions.append("gmail.send")
        if context.has_permission(GatewayPermission.GMAIL_READ):
            allowed_actions.append("gmail.read")
        if context.has_permission(GatewayPermission.CRM_CREATE):
            allowed_actions.append("crm.create")
        if context.has_permission(GatewayPermission.CRM_WRITE):
            allowed_actions.append("crm.update")
        if context.has_permission(GatewayPermission.CRM_READ):
            allowed_actions.append("crm.search")

        if not allowed_actions:
            return None

        prompt = f"""Bạn là bộ phân tích ý định cho trợ lý AI doanh nghiệp.
Nhiệm vụ: Phân tích yêu cầu ngôn ngữ tự nhiên thành JSON hành động có cấu trúc.
CHỈ ĐƯỢC CHỌN TRONG CÁC HÀNH ĐỘNG ĐƯỢC CẤP PHÉP SAU: {allowed_actions}
Nếu không thuộc danh sách trên hoặc không rõ ràng, trả về {{"action": "unknown"}}.

Cấu trúc JSON:
{{"action": "<tên_action>", "arguments": {{...}}}}

Yêu cầu người dùng: "{text}"
Múi giờ: {context.timezone}
Thời gian hiện tại: {datetime.now(UTC).isoformat()}
"""
        def _sync_call():
            from google import genai
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=settings.DEFAULT_LLM_MODEL or "gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return json.loads(resp.text)

        try:
            data = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=6.0)
            if data and data.get("action") in allowed_actions:
                return data
            return None
        except Exception as e:
            logger.warning("LLM parse action failed: %s", e)
            return None


gateway_agent_dispatcher = GatewayAgentDispatcher()
