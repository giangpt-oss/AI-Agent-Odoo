"""Confirmed writes, cancellation and operation reconciliation."""

import asyncio
import json
import logging
from datetime import UTC, datetime

from app.domain.gateway_context import GatewayPermission, GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult
from app.providers.tasks.local import execute_task_confirmation_atomically

logger = logging.getLogger(__name__)


class ConfirmationHandler:
    """Claim, execute and reconcile writes against the shared confirmation ledger."""

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
            pending_list = self.confirmation_service.list_pending_confirmations(
                user_id=context.user_id, chat_id=chat_id, telegram_binding_id=binding_id
            )
            if len(pending_list) == 1:
                conf_id = pending_list[0]["confirmation_id"]
            elif len(pending_list) > 1:
                options = "\n".join(f"• {c['confirmation_id']}: {c['preview_text'][:200]}" for c in pending_list)
                return [f"⚠️ Bạn đang có nhiều thao tác chờ xác nhận. Vui lòng chọn mã cụ thể:\n{options}"]
            else:
                return ["⚠️ Không tìm thấy thao tác nào đang chờ xác nhận."]

        # 1. Claim nguyên tử và lấy dữ liệu đóng băng
        ok, claim_msg, conf_data = self.confirmation_service.claim_and_validate(
            confirmation_id=conf_id,
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=binding_id,
            telegram_user_id=telegram_user_id,
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
                self.confirmation_service.mark_failed(conf_id, "Quyền tasks.write bị từ chối hoặc đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Bạn không có quyền thực hiện thao tác công việc (yêu cầu quyền tasks.write)."]

            if not context.owner_key:
                self.confirmation_service.mark_failed(conf_id, "Thiếu thông tin owner_key của người dùng hiện tại.", execution_token=exec_token)
                return ["❌ Không thể xác định tài khoản người dùng để thực hiện thao tác công việc."]

            if conf_data.get("owner_key") and conf_data["owner_key"] != context.owner_key:
                self.confirmation_service.mark_failed(conf_id, "Owner key không khớp với bản xem trước ban đầu.", execution_token=exec_token)
                return ["❌ Bạn không có quyền thực hiện công việc của tài khoản khác."]

            if not conf_data.get("owner_key"):
                self.confirmation_service.mark_failed(conf_id, "Bản ghi cũ thiếu thông tin owner cần thiết, yêu cầu tạo preview mới.", execution_token=exec_token)
                return ["⚠️ Bản ghi xác nhận cũ thiếu thông tin người sở hữu hợp lệ. Vui lòng tạo yêu cầu mới."]

            # Chặn preview update/done cũ thiếu revision
            if norm_action in ["tasks.update", "tasks.personal.update", "tasks.done", "tasks.complete", "tasks.personal.done", "tasks.personal.complete"]:
                target_snap = conf_data.get("target_snapshot")
                if not target_snap or not isinstance(target_snap, dict) or target_snap.get("revision") is None:
                    self.confirmation_service.mark_failed(
                        conf_id,
                        "Bản xem trước cũ thiếu thông tin phiên bản (revision). Vui lòng tạo bản xem trước mới.",
                        execution_token=exec_token,
                    )
                    return ["⚠️ Công việc đã thay đổi sau bản xem trước, cần tạo yêu cầu mới."]

            # Thực thi Task mutation và mark completed trong CÙNG TRANSACTION SQLite
            try:
                execute_task_confirmation_atomically(
                    db_path=self.confirmation_service.db_path,
                    confirmation_id=conf_id,
                    execution_token=exec_token,
                    action=action,
                    arguments=arguments,
                    owner_key=context.owner_key,
                    target_snapshot=conf_data.get("target_snapshot"),
                )
                return [f"✅ **Thực hiện thành công:** {conf_data.get('preview_text', action)}"]
            except Exception as e:
                self.confirmation_service.mark_failed(conf_id, error_message=str(e), execution_token=exec_token)
                if "thay đổi sau bản xem trước" in str(e).lower() or "revision" in str(e).lower():
                    return ["⚠️ Công việc đã thay đổi sau bản xem trước, cần tạo yêu cầu mới."]
                return [f"❌ Không thể thực hiện công việc: {e}"]

        # Kiểm tra tính hợp lệ của Draft Version nếu confirmation tạo từ Draft
        if conf_data.get("draft_id"):
            expected_ver = conf_data.get("draft_version")
            if expected_ver is None:
                self.confirmation_service.mark_failed(
                    conf_id,
                    "Bản xem trước thiếu thông tin phiên bản (draft_version). Vui lòng tạo bản xem trước mới.",
                    execution_token=exec_token,
                )
                return ["⚠️ Bản xem trước không còn hợp lệ do thiếu phiên bản bản thảo. Vui lòng tạo bản xem trước mới."]

            sess = self.conversation_history_store.get_session(
                tenant=context.tenant,
                user_id=context.user_id,
                chat_id=chat_id,
                binding_id=binding_id,
                database=context.database,
            )
            act_dr = sess.get("active_draft") if sess else None

            # 1. Draft bị xóa, hết TTL hoặc không tồn tại
            if not act_dr:
                self.confirmation_service.mark_failed(
                    conf_id,
                    "Bản nháp liên kết với bản xem trước này đã bị xóa hoặc không còn tồn tại.",
                    execution_token=exec_token,
                )
                return ["⚠️ Bản nháp liên kết đã bị xóa hoặc không còn tồn tại. Thao tác bị hủy bỏ."]

            # 2. Draft khác ID
            if act_dr.get("draft_id") != conf_data["draft_id"]:
                self.confirmation_service.mark_failed(
                    conf_id,
                    f"Bản nháp hiện tại ({act_dr.get('draft_id')}) không khớp với bản xem trước ({conf_data['draft_id']}).",
                    execution_token=exec_token,
                )
                return ["⚠️ Bản nháp hiện tại đã thay đổi sang một bản thảo khác. Vui lòng tạo bản xem trước mới."]

            # 3. Draft khác version (dù cao hơn hay thấp hơn)
            curr_ver = act_dr.get("version")
            if curr_ver != expected_ver:
                self.confirmation_service.mark_failed(
                    conf_id,
                    f"Phiên bản bản nháp hiện tại (v{curr_ver}) không khớp chính xác với phiên bản xem trước (v{expected_ver}).",
                    execution_token=exec_token,
                )
                return [f"⚠️ Bản nháp đã được chỉnh sửa (phiên bản hiện tại: v{curr_ver}, bản xem trước: v{expected_ver}). Mã xác nhận cũ không còn hiệu lực, vui lòng tạo bản xem trước mới."]

        # Kiểm tra quyền cho các dịch vụ ngoài qua Gateway
        if norm_action.startswith("business."):
            from app.core.config import get_settings
            from app.business.operations import allowed_action
            if not allowed_action(get_settings(), norm_action) or not context.owner_key or conf_data.get("owner_key") != context.owner_key:
                self.confirmation_service.mark_failed(conf_id, "Chức năng chưa bật hoặc chủ sở hữu đã thay đổi.", execution_token=exec_token)
                return ["Không thể thực hiện thao tác nghiệp vụ cho tài khoản này."]
        if norm_action.startswith("workspace."):
            if not context.has_permission(norm_action):
                self.confirmation_service.mark_failed(conf_id, "Quyền Workspace đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Quyền Workspace đã bị thu hồi. Vui lòng kiểm tra kết nối."]
        if norm_action.startswith("calendar."):
            if not context.has_permission(GatewayPermission.CALENDAR_WRITE):
                self.confirmation_service.mark_failed(conf_id, "Quyền calendar.write bị từ chối hoặc đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Bạn không có quyền thao tác trên Google Calendar (thiếu quyền calendar.write)."]
        elif norm_action.startswith("gmail."):
            if not context.has_permission(GatewayPermission.GMAIL_SEND):
                self.confirmation_service.mark_failed(conf_id, "Quyền gmail.send bị từ chối hoặc đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Bạn không có quyền gửi email qua Gmail (thiếu quyền gmail.send)."]
        elif norm_action.startswith("crm."):
            if not (context.has_permission(GatewayPermission.CRM_WRITE) or context.has_permission(GatewayPermission.CRM_CREATE)):
                self.confirmation_service.mark_failed(conf_id, "Quyền CRM bị từ chối hoặc đã bị thu hồi.", execution_token=exec_token)
                return ["❌ Bạn không có quyền tạo hoặc sửa đổi CRM trên Odoo."]

        if norm_action == "sales.quotation.create":
            from app.core.config import get_settings
            if not get_settings().ENABLE_QUOTATIONS or not context.has_permission("sales.create"):
                self.confirmation_service.mark_failed(conf_id, "Chức năng hoặc quyền tạo báo giá đã bị thu hồi.", execution_token=exec_token)
                return ["Không có quyền tạo báo giá hoặc chức năng chưa bật."]
            if not context.owner_key or conf_data.get("owner_key") != context.owner_key:
                self.confirmation_service.mark_failed(conf_id, "Chủ sở hữu nháp không hợp lệ.", execution_token=exec_token)
                return ["Chủ sở hữu báo giá không khớp."]

        # 3. Xử lý Gateway Action qua Cổng Gateway với dispatch bền vững
        if not op_id:
            self.confirmation_service.mark_unknown(conf_id, "Bản ghi thiếu operation_id, chuyển sang đối soát thủ công.", execution_token=exec_token)
            return ["⚠️ Thao tác thiếu mã đối soát (operation_id) hợp lệ, chuyển sang đối soát thủ công."]

        # Ghi nhận dispatch_started_at trước khi gọi mạng; chỉ dispatch nếu token và lease còn hiệu lực
        if not self.confirmation_service.mark_dispatched(conf_id, execution_token=exec_token):
            return ["⚠️ Phiên xử lý thao tác đã hết hạn hoặc không còn hiệu lực. Vui lòng thử lại."]

        from app.business.operations import CHATTER_ACTIONS, PURCHASE_FLOW_ACTIONS, QUOTE_FLOW_ACTIONS, STOCK_FLOW_ACTIONS
        call_timeout = max(1.0, min(50.0 if norm_action in QUOTE_FLOW_ACTIONS | CHATTER_ACTIONS | PURCHASE_FLOW_ACTIONS | STOCK_FLOW_ACTIONS else 14.0, remaining_time - 1.0))

        try:
            gw_res = await self.gateway_client.execute_action(
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
                self.confirmation_service.mark_completed(conf_id, result=result, execution_token=exec_token)
                if norm_action.startswith("business."):
                    from app.business.operations import format_result
                    from app.business.workflow import remember
                    remember(context, "business_action_result", gw_res)
                    try:
                        self.conversation_history_store.clear_active_draft(
                            tenant=context.tenant,
                            user_id=context.user_id,
                            chat_id=chat_id,
                            binding_id=binding_id,
                            database=context.database,
                        )
                    except Exception:
                        pass
                    return format_result(gw_res)
                if norm_action == "sales.quotation.create":
                    from app.business.quote_status import result_message
                    return [result_message(result)]
                if norm_action.startswith("workspace."):
                    from app.workspace.agent import format_result
                    return format_result(norm_action.split(".")[1], gw_res)
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

            elif status in ("in_progress", "IN_PROGRESS"):
                if norm_action == 'sales.quotation.create':
                    return ['Trạng thái: Đang tạo báo giá trên Odoo, chưa nhận được số báo giá. Tôi sẽ thông báo số báo giá và trạng thái khi hoàn tất.']
                return [
                    f"⏳ **Thao tác đang được xử lý trên Gateway:**\n"
                    f"• Mã xác nhận: `{conf_id}`\n"
                    f"• Mã thao tác: `{op_id}`\n\n"
                    f"Thao tác đang trong quá trình thực hiện. Bạn có thể hỏi tiến độ của thao tác {conf_id}."
                ]

            elif status == "unknown_result":
                self.confirmation_service.mark_unknown(conf_id, reason=gw_res.get("message", "Chưa rõ kết quả từ Gateway"), execution_token=exec_token)
                return [
                    f"⚠️ **Chưa rõ kết quả thực hiện:** Yêu cầu đã được gửi tới Gateway nhưng phản hồi bị gián đoạn hoặc quá thời gian.\n"
                    f"• Mã xác nhận: `{conf_id}`\n"
                    f"• Mã thao tác: `{op_id}`\n\n"
                    f"Bạn có thể hỏi tiến độ của thao tác {conf_id} để đối soát mà không thực hiện lại."
                ]

            elif status == "CONFLICT":
                self.confirmation_service.mark_failed(conf_id, error_message=gw_res.get("message", "Xung đột phiên bản"), execution_token=exec_token)
                return [
                    "⚠️ **Tài nguyên đã bị thay đổi kể từ lúc xem trước:**\n"
                    "Để tránh ghi đè dữ liệu mới hơn của người khác, thao tác đã bị dừng. Vui lòng tạo yêu cầu xem trước mới."
                ]

            else:
                err_msg = gw_res.get("message") or "Lỗi dịch vụ ngoài"
                self.confirmation_service.mark_failed(conf_id, error_message=err_msg, execution_token=exec_token)
                if norm_action == 'sales.quotation.create':
                    from app.business.quote_status import quotation_failure_message
                    return [quotation_failure_message(err_msg, gw_res.get('result'))]
                return [f"❌ **Thao tác thất bại:** {err_msg}"]

        except (asyncio.CancelledError, asyncio.TimeoutError) as ce:
            logger.warning("Turn cancelled/timed out during gateway action dispatch: %s", ce)
            self.confirmation_service.mark_unknown(conf_id, reason=f"Hết thời gian turn hoặc hủy sau dispatch: {ce}", execution_token=exec_token)
            return [
                f"⚠️ **Yêu cầu đã gửi nhưng hết thời gian chờ phản hồi:**\n"
                f"• Mã xác nhận: `{conf_id}`\n"
                f"• Mã thao tác: `{op_id}`\n\n"
                f"Hệ thống không tự động gửi lại để tránh trùng lặp. Bạn có thể hỏi tiến độ của thao tác {conf_id}."
            ]

        except Exception as e:
            logger.error("Unexpected error executing gateway action: %s", e, exc_info=True)
            self.confirmation_service.mark_unknown(conf_id, reason=f"Lỗi kết nối sau dispatch: {e}", execution_token=exec_token)
            return [
                f"⚠️ **Sự cố kết nối sau khi gửi yêu cầu:**\n"
                f"• Mã xác nhận: `{conf_id}`\n"
                f"• Mã thao tác: `{op_id}`\n\n"
                f"Bạn có thể hỏi tiến độ của thao tác {conf_id} sau giây lát."
            ]

    def _handle_cancel(
        self, intent: IntentAnalysisResult, context: GatewayUserContext, chat_id: int, telegram_user_id: int
    ) -> list[str]:
        conf_id = intent.confirmation_id or intent.parameters.get("confirmation_id")
        binding_id = context.telegram_binding_id or "default"

        if not conf_id:
            pending_list = self.confirmation_service.list_pending_confirmations(
                user_id=context.user_id, chat_id=chat_id, telegram_binding_id=binding_id
            )
            if len(pending_list) == 1:
                conf_id = pending_list[0]["confirmation_id"]
            elif len(pending_list) > 1:
                options = "\n".join(f"• {c['confirmation_id']}: {c['preview_text'][:200]}" for c in pending_list)
                return [f"⚠️ Bạn đang có nhiều thao tác chờ. Vui lòng chọn mã để hủy:\n{options}"]
            else:
                return ["⚠️ Không tìm thấy thao tác nào đang chờ xác nhận để hủy."]

        success, msg = self.confirmation_service.cancel_confirmation(
            confirmation_id=conf_id,
            user_id=context.user_id,
            chat_id=chat_id,
            telegram_binding_id=binding_id,
            telegram_user_id=telegram_user_id,
        )
        return [msg if success else f"⚠️ {msg}"]

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
            return ["Bạn muốn kiểm tra thao tác nào? Hãy cho biết mã tham chiếu trong thông báo trước đó."]

        target_id = target_id.strip()

        # 1. Tìm trong SQLite Confirmation Service
        conf = self.confirmation_service.get_confirmation(target_id)
        if conf:
            # KIỂM TRA QUYỀN TRUY CẬP: user_id, chat_id, telegram_binding_id, owner_key
            if not self.confirmation_service.validate_access(
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
                self.confirmation_service.update_telegram_user_id(cid, telegram_user_id)
                conf["telegram_user_id"] = telegram_user_id

            c_status = conf.get("status")

            if c_status == "executing":
                now_ts = datetime.now(UTC).timestamp()
                lease = conf.get("lease_expires_at")
                if lease and now_ts < lease:
                    return [f"⏳ Thao tác `{target_id}` đang được thực thi trên hệ thống..."]
                # Lease thiếu hoặc đã hết hạn: thu hồi claim và chuyển sang unknown_result trước khi đối soát
                self.confirmation_service.recover_expired_leases()
                conf = self.confirmation_service.get_confirmation(cid) or conf
                c_status = conf.get("status")

            if c_status == "completed" and str(conf.get("action", "")).startswith("business."):
                from app.business.operations import format_result
                check = await self.gateway_client.get_action_status(user_id=context.user_id, operation_id=conf["operation_id"],
                    telegram_user_id=telegram_user_id, chat_id=chat_id, telegram_binding_id=binding_id,
                    timeout=max(1.0, min(5.0, remaining_time - 1.0)))
                return format_result(check)
            if c_status == "completed":
                return [f"✅ Thao tác `{target_id}` đã được thực thi thành công."]
            if c_status == "cancelled":
                return [f"ℹ️ Thao tác `{target_id}` đã được hủy bỏ an toàn."]
            if c_status == "failed":
                if conf.get('action') == 'sales.quotation.create':
                    from app.business.quote_status import quotation_failure_message
                    return [quotation_failure_message(conf.get('error_message'), json.loads(conf.get('result_json') or '{}'))]
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

                gw_check = await self.gateway_client.get_action_status(
                    user_id=context.user_id,
                    operation_id=op_id,
                    telegram_user_id=tele_uid,
                    chat_id=chat_id,
                    telegram_binding_id=binding_id,
                    timeout=max(1.0, min(5.0, remaining_time - 1.0)),
                )
                gw_st = gw_check.get("status")
                if gw_st == "completed":
                    self.confirmation_service.reconcile_confirmation_result(
                        cid, status="completed", result=gw_check.get("result"), operation_id=op_id
                    )
                    if str(conf.get("action", "")).startswith("business."):
                        from app.business.operations import format_result
                        return format_result(gw_check)
                    return [f"✅ Thao tác `{target_id}` đã hoàn tất thành công trên Cổng Gateway!"]
                elif gw_st == "failed":
                    self.confirmation_service.reconcile_confirmation_result(
                        cid, status="failed", result=gw_check.get('result'),
                        error_message=gw_check.get("message", "Thất bại trên Gateway"), operation_id=op_id
                    )
                    if conf.get('action') == 'sales.quotation.create':
                        from app.business.quote_status import quotation_failure_message
                        return [quotation_failure_message(gw_check.get('message'), gw_check.get('result'))]
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
            gw_check = await self.gateway_client.get_action_status(
                user_id=context.user_id,
                operation_id=target_id,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                telegram_binding_id=binding_id,
                timeout=max(1.0, min(5.0, remaining_time - 1.0)),
            )
            gw_st = gw_check.get("status")
            if gw_st == "completed":
                if str(gw_check.get("action", "")).startswith("business."):
                    from app.business.operations import format_result
                    return format_result(gw_check)
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
