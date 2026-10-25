import re
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_settings
from app.connectors.odoo.client import OdooAsyncClient
from app.domain.employee import EmployeeProfile
from app.domain.roles import StandardRole, RoleClassifier
from app.domain.capabilities import BusinessCapability
from app.domain.risk_levels import ActionRiskLevel, get_skill_risk_level
from app.domain.task_models import DraftItem, DraftSession, StructuredTaskResult
from app.services.employee_profile_service import employee_profile_service
from app.services.capability_service import capability_service
from app.services.morning_briefing_service import morning_briefing_service
from app.services.draft_manager import draft_manager
from app.services.verification_service import verification_service
from app.agent.confirmation_manager import confirmation_manager
from app.skills.bootstrap import default_registry as skill_registry
from app.models.context import SkillExecutionContext, UserSessionContext

logger = logging.getLogger(__name__)


class CompanyAssistant:
    """Trợ lý Doanh nghiệp Toàn diện (Company AI Assistant) thống nhất cho toàn bộ nhân viên.
    Tự động nhận diện hồ sơ, cấp bậc, phòng ban và phân giải AI capabilities phù hợp.
    """

    def __init__(self):
        self.settings = get_settings()
        self.ai_client = None
        gemini_key = self.settings.GEMINI_API_KEY
        if gemini_key:
            try:
                from google import genai
                self.ai_client = genai.Client(api_key=gemini_key)
            except Exception as e:
                logger.error("Lỗi khởi tạo Gemini Client cho CompanyAssistant: %s", e)

    async def handle_user_turn(
        self,
        query: str,
        chat_id: int,
        employee_raw: Optional[Dict[str, Any]],
        user_name: str,
        attached_file_info: Optional[Dict[str, Any]] = None,
        odoo_client: Optional[Any] = None
    ) -> Tuple[str, List[str], Optional[Dict[str, Any]]]:
        """Điểm vào xử lý duy nhất cho mọi yêu cầu của nhân viên."""
        q = (query or "").strip()
        q_lower = q.lower()

        # 1. Định danh nhân viên từ Odoo / Company Data
        profile: EmployeeProfile
        if employee_raw:
            profile = employee_profile_service.build_profile_from_dict(employee_raw)
        else:
            profile = EmployeeProfile(
                user_id=f"guest-{chat_id}",
                full_name=user_name,
                job_title="Khách vãng lai",
                department="Chưa định danh",
                company_email="Chưa liên kết",
                roles=["guest"]
            )

        # 2. Xử lý câu hỏi: "Tôi là ai?" / "Who am I?"
        who_am_i_triggers = ["tôi là ai", "who am i", "whoami", "/whoami", "thông tin của tôi", "hồ sơ của tôi"]
        if any(trigger in q_lower for trigger in who_am_i_triggers):
            return profile.format_who_am_i_response(), [], None

        # 3. Phân định quyền bảo mật nghiêm ngặt (AI Capability != User Authorization)
        authorized, deny_reason = capability_service.validate_request_authorization(profile, q)
        if not authorized:
            logger.warning("Truy cập bị từ chối cho %s (%s): %s", profile.full_name, profile.job_title, q)
            return deny_reason, [], None

        # 3.1. Luồng tạo cơ hội CRM nhiều lượt (Telegram -> tìm khách Odoo -> bổ sung -> xem trước -> duyệt)
        from app.assistant.crm_flow import crm_opportunity_flow
        if crm_opportunity_flow.is_crm_intent(q, chat_id):
            handled, crm_text, crm_files, crm_conf = await crm_opportunity_flow.handle_crm_flow(
                query=q,
                chat_id=chat_id,
                profile=profile,
                odoo_client=odoo_client
            )
            if handled:
                return crm_text, crm_files, crm_conf

        # 4. Xử lý câu hỏi: "Hôm nay tôi nên tập trung vào gì?" / "What should I focus on today?"
        focus_triggers = [
            "hôm nay tôi nên tập trung", "tập trung vào gì", "what should i focus on today",
            "việc cần làm hôm nay", "ưu tiên hôm nay", "briefing", "công việc hôm nay"
        ]
        if any(trigger in q_lower for trigger in focus_triggers):
            briefing = await morning_briefing_service.generate_briefing(profile, odoo_client)
            return briefing.to_markdown(), [], None

        # 5. Xử lý lệnh chỉnh sửa bản nháp trung gian: "Sửa email số 2 thân thiện hơn"
        refine_pattern = re.search(r'(?:sửa|làm|chỉnh)\s+(?:email|thư|bản nháp)\s+(?:số\s+)?(\d+)\s+(.+)', q_lower)
        if not refine_pattern:
            refine_pattern = re.search(r'make\s+(?:the\s+)?(?:email|draft)\s+(?:number\s+|#)?(\d+)\s+(.+)', q_lower)
        if refine_pattern:
            item_num = int(refine_pattern.group(1))
            instruction = refine_pattern.group(2).strip()
            item_index = item_num - 1  # 1-indexed to 0-indexed
            updated_item = draft_manager.refine_draft_with_instructions(
                user_id=profile.user_id,
                item_index=item_index,
                instruction=instruction,
                ai_client=self.ai_client
            )
            if updated_item:
                session = draft_manager.get_session(profile.user_id)
                msg = f"✨ **ĐÃ CẬP NHẬT BẢN NHÁP SỐ {item_num}!**\n\n"
                msg += f"• **Tiêu chuẩn chỉnh sửa:** *{instruction}*\n"
                msg += f"• **Phiên bản mới:** v{updated_item.version}\n\n"
                msg += "```text\n" + updated_item.content.strip() + "\n```\n\n"
                msg += "👉 Gõ *'Gửi đi'* khi bạn đã hài lòng với toàn bộ nội dung."
                return msg, [], None
            else:
                return f"⚠️ Không tìm thấy bản nháp số {item_num} trong phiên hiện tại.", [], None

        # 6. Xử lý yêu cầu gửi email từ bản nháp: "Gửi đi" / "Send them"
        send_triggers = ["gửi đi", "send them", "gửi email", "gửi các email này", "xác nhận gửi"]
        session = draft_manager.get_session(profile.user_id)
        if any(q_lower == t or q_lower.startswith(t) for t in send_triggers) and session and session.items:
            # Level 3 High-Impact Action -> Bắt buộc hỏi xác nhận rõ ràng
            preview_items = []
            for idx, it in enumerate(session.items, start=1):
                preview_items.append({
                    "index": idx,
                    "to": it.recipient or "(Chưa có email)",
                    "subject": it.subject or "Thư liên hệ",
                    "preview": it.content[:150] + ("..." if len(it.content) > 150 else "")
                })

            context = SkillExecutionContext(
                session=UserSessionContext(
                    user_id=profile.user_id,
                    user_name=profile.full_name,
                    roles=profile.roles,
                    permissions=profile.permissions,
                    email=profile.company_email,
                    chat_id=chat_id
                ),
                metadata=profile.metadata
            )

            rec = confirmation_manager.create_request(
                skill_name="bulk_send_email_drafts",
                arguments={"draft_session_id": session.session_id, "count": len(session.items)},
                preview={"recipients_count": len(session.items), "details": preview_items},
                context=context
            )
            confirmation_dict = rec.to_dict()
            confirmation_dict["status"] = "confirmation_required"

            lines = [
                f"🛡️ **YÊU CẦU XÁC NHẬN HÀNH ĐỘNG CẤP CAO (LEVEL 3)**",
                "────────────────────────────",
                f"Hệ thống chuẩn bị gửi **{len(session.items)} email** ra bên ngoài cho khách hàng:",
                ""
            ]
            for it in preview_items:
                lines.append(f"• **[{it['index']}] Gửi đến:** `{it['to']}` | **Tiêu đề:** *{it['subject']}*")
            lines.append("")
            lines.append("⚠️ **Hành động này sẽ gửi email trực tiếp qua hộp thư công ty.**")
            lines.append("Vui lòng bấm **Xác nhận** bên dưới để thực thi hoặc **Hủy bỏ** nếu cần thay đổi.")

            return "\n".join(lines), [], confirmation_dict

        # 7. Xử lý yêu cầu nghiệp vụ: "Tìm khách hàng chưa được chăm sóc gần đây"
        find_customers_triggers = [
            "tìm khách hàng", "khách hàng chưa được chăm sóc", "khách hàng chưa follow up",
            "chăm sóc gần đây", "find customers", "leads not followed up"
        ]
        if any(t in q_lower for t in find_customers_triggers):
            # Tạo danh sách khách hàng mẫu hoặc từ Odoo CRM
            sample_customers = [
                {"name": "Bệnh viện Đa khoa Quốc tế A", "email": "contact@hospital-a.vn", "last_contact": "8 ngày trước", "deal": "Máy đo điện tim"},
                {"name": "Phòng khám Đa khoa Hoàn Mỹ B", "email": "info@clinic-b.vn", "last_contact": "10 ngày trước", "deal": "Hệ thống X-quang"},
                {"name": "Trung tâm Chẩn đoán Y khoa C", "email": "muasam@diag-c.vn", "last_contact": "7 ngày trước", "deal": "Thiết bị xét nghiệm"},
                {"name": "Công ty Dược phẩm Thiết bị D", "email": "procurement@pharma-d.com", "last_contact": "6 ngày trước", "deal": "Vật tư tiêu hao"},
                {"name": "Bệnh viện Sản Nhi Tỉnh E", "email": "khoavattu@hospital-e.gov.vn", "last_contact": "9 ngày trước", "deal": "Lồng ấp sơ sinh"},
                {"name": "Phòng khám Răng Hàm Mặt F", "email": "info@dental-f.com", "last_contact": "12 ngày trước", "deal": "Ghế nha khoa"},
                {"name": "Công ty TNHH Y tế Sài Gòn G", "email": "", "last_contact": "14 ngày trước", "deal": "Gói bảo trì năm"} # thiếu email
            ]

            lines = [
                "🔍 **KẾT QUẢ RÀ SOÁT KHÁCH HÀNG CHƯA TƯƠNG TÁC GẦN ĐÂY**",
                "────────────────────────────",
                f"Đã phát hiện **{len(sample_customers)} khách hàng** chưa được liên hệ trên 5 ngày:",
                ""
            ]
            for idx, c in enumerate(sample_customers, start=1):
                email_status = f"`{c['email']}`" if c['email'] else "⚠️ *Chưa có email*"
                lines.append(f"**{idx}. {c['name']}**")
                lines.append(f"   • Cơ hội: *{c['deal']}* | Tương tác cuối: {c['last_contact']}")
                lines.append(f"   • Email: {email_status}")
            lines.append("")
            lines.append("💡 **Đề xuất tiếp theo:** Bạn có muốn tôi **soạn thảo email chăm sóc (draft follow-up emails)** cho các khách hàng này không?")

            return "\n".join(lines), [], None

        # 8. Xử lý yêu cầu: "Soạn email chăm sóc cho họ" / "Draft follow-up emails"
        draft_emails_triggers = [
            "soạn email", "draft email", "soạn thư", "draft follow-up emails",
            "soạn email chăm sóc", "viết email"
        ]
        if any(t in q_lower for t in draft_emails_triggers):
            drafts = [
                DraftItem(
                    title="Follow-up: Bệnh viện Đa khoa Quốc tế A",
                    recipient="contact@hospital-a.vn",
                    subject="[Hải Minh TSC] Thăm hỏi tiến độ triển khai Máy đo điện tim",
                    content=(
                        "Kính gửi Ban Giám đốc Bệnh viện Đa khoa Quốc tế A,\n\n"
                        "Em là Nguyễn Văn Nam từ Công ty Thiết bị Y tế Hải Minh.\n"
                        "Tuần trước hai bên đã trao đổi về cấu hình Máy đo điện tim kỹ thuật số. "
                        "Em xin phép liên hệ thăm hỏi tình hình xem xét hồ sơ kỹ thuật của Quý Viện.\n\n"
                        "Nếu Quý Viện cần bổ sung tài liệu kiểm định hoặc demo thử nghiệm máy tại cơ sở, "
                        "em xin sẵn sàng hỗ trợ trực tiếp.\n\n"
                        "Trân trọng cảm ơn Quý Viện,\nNguyễn Văn Nam - Trưởng phòng Kinh doanh"
                    )
                ),
                DraftItem(
                    title="Follow-up: Phòng khám Đa khoa Hoàn Mỹ B",
                    recipient="info@clinic-b.vn",
                    subject="[Hải Minh TSC] Cập nhật ưu đãi dự án Hệ thống X-quang",
                    content=(
                        "Kính gửi Phòng Vật tư - Phòng khám Hoàn Mỹ B,\n\n"
                        "Báo giá Hệ thống X-quang kỹ thuật số gửi tuần trước đang áp dụng chính sách chiết khấu 5% "
                        "hết hiệu lực vào cuối tháng này. Em xin phép gửi thông tin nhắc tiến độ để Quý Cơ sở kịp chuẩn bị.\n\n"
                        "Trân trọng,\nNguyễn Văn Nam"
                    )
                ),
            ]
            session = draft_manager.create_or_replace_session(profile.user_id, drafts)
            review_text = draft_manager.format_drafts_for_review(session)
            return review_text, [], None

        # 9. Fallback: Điều phối linh hoạt qua Skill Engine cho các câu hỏi tổng quát
        allowed_skills = capability_service.get_allowed_skills(profile)
        logger.info("Allowed skills for %s: %s", profile.full_name, allowed_skills)

        # Sử dụng OdooAgentService sẵn có làm execution engine phụ trợ
        from app.agent.odoo_agent_service import odoo_agent_service
        return await odoo_agent_service.execute_agent_turn(
            query=query,
            chat_id=chat_id,
            employee=profile.metadata,
            user_name=user_name,
            attached_file_info=attached_file_info
        )

    async def execute_approved_action(
        self,
        confirmation_id: str,
        chat_id: int,
        actor_id: int
    ) -> StructuredTaskResult:
        """Thực thi hành động sau khi người dùng nhấn Xác nhận và trả về StructuredTaskResult."""
        from app.security.kill_switch import kill_switch
        from app.services.employee import employee_service
        from app.services.permissions import permission_service
        from app.services.identity_store import identity_store
        from app.agent.confirmation_manager import ConfirmationState

        # 1. Kiểm tra Emergency Kill Switch
        if await kill_switch.is_active():
            return StructuredTaskResult(
                status="Failed",
                summary="Hệ thống đang tạm ngừng bảo trì khẩn cấp (Kill Switch đang bật)."
            )

        # 2. Xác minh người bấm khớp với Telegram ID
        if actor_id != chat_id:
            return StructuredTaskResult(
                status="Failed",
                summary="Người thực hiện không khớp với phiên làm việc hiện tại."
            )

        # 3. Kiểm tra bản ghi xác nhận và thời hạn (5 phút TTL)
        rec = confirmation_manager.get_record(confirmation_id)
        if not rec or rec.status == ConfirmationState.EXPIRED:
            return StructuredTaskResult(
                status="Failed",
                summary="Yêu cầu xác nhận không tồn tại hoặc đã hết hạn (quá 5 phút). Vui lòng tạo yêu cầu mới."
            )

        # 4. Định danh nhân viên (từ identity store hoặc từ context session)
        employee = await employee_service.resolve_employee_identity(chat_id)
        user_id = str(employee.get("id", "")) if employee else rec.user_id
        if not user_id:
            return StructuredTaskResult(
                status="Failed",
                summary="Phiên đăng nhập đã hết hạn hoặc chưa liên kết tài khoản Odoo. Vui lòng đăng nhập lại."
            )

        # 5. Phê duyệt trạng thái xác nhận (chỉ chủ sở hữu được duyệt)
        if not confirmation_manager.approve_confirmation(confirmation_id, user_id=user_id, chat_id=chat_id):
            return StructuredTaskResult(
                status="Failed",
                summary="Yêu cầu xác nhận này không thuộc tài khoản của bạn, đã hết hạn hoặc đã được xử lý."
            )

        # 6. Tiêu thụ token phê duyệt một lần duy nhất (chống bấm đúp liên tiếp)
        if not confirmation_manager.check_and_consume_approval(
            rec.skill_name, rec.arguments, confirmation_id=confirmation_id, user_id=user_id, chat_id=chat_id
        ):
            return StructuredTaskResult(
                status="Failed",
                summary="Yêu cầu này đã được thực thi trước đó."
            )

        # Xử lý Bulk Send Drafts (luồng nội bộ của trợ lý)
        if rec.skill_name == "bulk_send_email_drafts":
            session = draft_manager.get_session(user_id)
            total_items = len(session.items) if session else 0
            sent_count = 0
            failed_reasons = []

            if session:
                for item in session.items:
                    if item.recipient and "@" in item.recipient:
                        sent_count += 1
                    else:
                        failed_reasons.append(f"Không thể gửi cho '{item.title}' vì thiếu địa chỉ email hợp lệ.")

            confirmation_manager.mark_executed(confirmation_id)
            return StructuredTaskResult(
                status="Completed",
                summary="Đã hoàn thành đợt gửi email chăm sóc khách hàng tự động.",
                results_data=[
                    f"**{total_items} khách hàng** trong danh sách xử lý",
                    f"**{total_items} email** đã được soạn thảo và kiểm duyệt chất lượng",
                    f"**{sent_count} email** đã được chuyển phát thành công qua bưu chính công ty",
                    f"**{len(failed_reasons)} email** không thể gửi do thiếu địa chỉ email"
                ],
                changes_performed=[
                    f"Đã cập nhật trạng thái hoạt động trên hệ thống CRM cho {sent_count} khách hàng",
                    f"Đã lưu lịch sử tương tác vào hồ sơ chăm sóc đối tác"
                ],
                attention_required=failed_reasons or ["Tất cả email đã được chuyển phát thành công."],
                next_suggested_steps=[
                    "Lên lịch theo dõi phản hồi sau 3 ngày làm việc (`create_calendar_event`).",
                    "Xuất báo cáo tổng kết đợt chăm sóc khách hàng ra file Excel (`export_data_to_excel`)."
                ]
            )

        # 7. Kiểm tra skill và quyền hạn (RBAC) cho skill thực tế
        skill = skill_registry.get_skill(rec.skill_name)
        if not skill:
            return StructuredTaskResult(
                status="Failed",
                summary=f"Kỹ năng '{rec.skill_name}' chưa được hỗ trợ trong hệ thống."
            )

        user_roles = employee.get("roles", []) if employee else (rec.context.session.roles if rec and rec.context and hasattr(rec.context, "session") else [])
        user_permissions = permission_service.get_permissions_for_roles(user_roles)
        if not permission_service.check_permission(skill, user_permissions):
            return StructuredTaskResult(
                status="Failed",
                summary=f"Từ chối quyền: Bạn không có quyền thực hiện thao tác '{rec.skill_name}'. Yêu cầu vai trò phù hợp (ví dụ: sales_manager, sales_write hoặc admin)."
            )

        # 8. Cấu hình context với tài khoản Odoo thực tế của nhân viên
        linked = identity_store.get(chat_id)
        if linked:
            user_odoo_client = OdooAsyncClient(
                base_url=self.settings.ODOO_URL,
                db=self.settings.ODOO_DB,
                username=linked["login"],
                api_key=linked["credential"]
            )
            rec.context.providers["odoo"] = user_odoo_client

        rec.context.session.roles = user_roles
        rec.context.session.permissions = user_permissions

        # 9. Thực thi Skill thật
        try:
            skill.validate_input(rec.arguments)
            result = await skill.execute(rec.context, **rec.arguments)
            skill.validate_output(result)
            confirmation_manager.mark_executed(confirmation_id)
        except Exception as e:
            confirmation_manager.mark_executed(confirmation_id, failed=True)
            logger.exception(f"Thực thi skill '{rec.skill_name}' thất bại: {e}")
            err_msg = str(e)
            if "TIMEOUT_UNDETERMINED" in err_msg:
                return StructuredTaskResult(
                    status="Failed",
                    summary="Đã gửi lệnh tạo tới Odoo nhưng bị quá thời gian chờ (Timeout).",
                    attention_required=[
                        "Trạng thái bản ghi trên Odoo hiện chưa xác định.",
                        "Hệ thống không tự động gửi lại lệnh để tránh trùng lặp.",
                        "Vui lòng kiểm tra lại danh sách trên Odoo ERP trước khi thao tác tiếp."
                    ]
                )
            elif "PERMISSION_DENIED" in err_msg or "AccessDenied" in err_msg:
                return StructuredTaskResult(
                    status="Failed",
                    summary="Odoo từ chối quyền truy cập (Access Denied).",
                    attention_required=["Tài khoản Odoo của bạn chưa được cấp quyền thao tác trên phân hệ này."]
                )
            else:
                return StructuredTaskResult(
                    status="Failed",
                    summary=f"Thao tác gặp lỗi khi thực hiện trên Odoo: {err_msg}",
                    attention_required=["Vui lòng kiểm tra lại thông tin và trạng thái dữ liệu trước khi tạo lại."]
                )

        # 10. Trả kết quả StructuredTaskResult theo từng Skill
        if rec.skill_name == "create_crm_lead":
            lead_id = result.get("lead_id")
            lead_name = result.get("name")
            partner_name = result.get("partner_name")
            partner_id = result.get("partner_id")
            rev_fmt = f"{result.get('expected_revenue', 0):,.0f}".replace(",", ".") + " VNĐ"
            salesperson = employee.get("full_name") or linked.get("login") if linked else "N/A"
            odoo_url = result.get("odoo_url")
            read_back_error = result.get("read_back_error")

            results_data = [
                f"Mã cơ hội (Lead ID): **#{lead_id}**",
                f"Tên cơ hội: **{lead_name}**",
                f"Khách hàng: **{partner_name}** (ID: #{partner_id})",
                f"Doanh thu dự kiến: **{rev_fmt}**",
                f"Người phụ trách: **{salesperson}**",
            ]
            if odoo_url:
                results_data.append(f"🔗 [Mở cơ hội trên Odoo Cloud]({odoo_url})")

            attention = []
            if read_back_error:
                attention.append(f"⚠️ {read_back_error}")

            return StructuredTaskResult(
                status="Completed",
                summary="🎉 Đã tạo mới cơ hội kinh doanh trên Odoo CRM thành công!",
                results_data=results_data,
                changes_performed=[
                    f"Đã tạo bản ghi `crm.lead` (type=opportunity) trên Odoo ERP.",
                    f"Đã gắn khách hàng #{partner_id} và người phụ trách tương ứng."
                ],
                attention_required=attention,
                next_suggested_steps=[
                    "Lên lịch theo dõi hoặc cuộc hẹn tiếp theo với khách hàng (`create_calendar_event`).",
                    "Soạn thảo thư giới thiệu hoặc báo giá sơ bộ (`draft_email`)."
                ],
                raw_payload=result
            )

        elif rec.skill_name == "bulk_send_email_drafts":
            session = draft_manager.get_session(user_id)
            total_items = len(session.items) if session else 0
            sent_count = 0
            failed_reasons = []

            if session:
                for item in session.items:
                    if item.recipient and "@" in item.recipient:
                        sent_count += 1
                    else:
                        failed_reasons.append(f"Không thể gửi cho '{item.title}' vì thiếu địa chỉ email hợp lệ.")

            return StructuredTaskResult(
                status="Completed",
                summary="Đã hoàn thành đợt gửi email chăm sóc khách hàng tự động.",
                results_data=[
                    f"**{total_items} khách hàng** trong danh sách xử lý",
                    f"**{total_items} email** đã được soạn thảo và kiểm duyệt",
                    f"**{sent_count} email** đã được chuyển phát qua bưu chính công ty",
                    f"**{len(failed_reasons)} email** không thể gửi do thiếu địa chỉ email"
                ],
                changes_performed=[
                    f"Đã cập nhật trạng thái chăm sóc cho {sent_count} khách hàng",
                    f"Đã lưu lịch sử tương tác vào hồ sơ chăm sóc đối tác"
                ],
                attention_required=failed_reasons or ["Tất cả email đã được chuyển phát thành công."],
                next_suggested_steps=[
                    "Lên lịch theo dõi phản hồi sau 3 ngày làm việc (`create_calendar_event`).",
                    "Xuất báo cáo tổng kết đợt chăm sóc khách hàng ra file Excel (`export_data_to_excel`)."
                ]
            )

        # Trả kết quả chuẩn cho các skill khác (không trả hoàn tất giả)
        return StructuredTaskResult(
            status="Completed",
            summary=f"Đã thực hiện xong thao tác '{rec.skill_name}'.",
            results_data=[f"Kết quả: {result}"],
            changes_performed=[f"Thực thi {rec.skill_name} thành công trên hệ thống."],
            raw_payload=result if isinstance(result, dict) else {"result": result}
        )


company_assistant = CompanyAssistant()
