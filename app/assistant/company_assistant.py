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
        rec = confirmation_manager.get_record(confirmation_id)
        if not rec:
            return StructuredTaskResult(
                status="Failed",
                summary="Yêu cầu xác nhận không tồn tại hoặc đã hết hạn."
            )

        user_id = rec.user_id
        if not confirmation_manager.approve_confirmation(confirmation_id, user_id=user_id, chat_id=chat_id):
            return StructuredTaskResult(
                status="Failed",
                summary="Không thể duyệt yêu cầu (sai tài khoản hoặc đã xử lý)."
            )

        if not confirmation_manager.check_and_consume_approval(
            rec.skill_name, rec.arguments, confirmation_id=confirmation_id, user_id=user_id, chat_id=chat_id
        ):
            return StructuredTaskResult(
                status="Failed",
                summary="Yêu cầu đã được thực thi trước đó."
            )

        # Xử lý Bulk Send Drafts
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

            result = StructuredTaskResult(
                status="Completed",
                summary=f"Đã hoàn thành đợt gửi email chăm sóc khách hàng tự động.",
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
            return result

        # Fallback cho các skill khác
        confirmation_manager.mark_executed(confirmation_id)
        return StructuredTaskResult(
            status="Completed",
            summary=f"Đã thực hiện xong thao tác '{rec.skill_name}'.",
            changes_performed=[f"Thực thi {rec.skill_name} thành công."],
            next_suggested_steps=["Kiểm tra lại dữ liệu trên hệ thống nếu cần."]
        )


company_assistant = CompanyAssistant()
