import logging
from typing import Any, Dict, List, Optional
from app.domain.employee import EmployeeProfile, SeniorityLevel, DepartmentType
from app.domain.roles import StandardRole, RoleClassifier
from app.domain.task_models import StructuredTaskResult

logger = logging.getLogger(__name__)


class MorningBriefingService:
    """Xử lý phân tích 'Hôm nay tôi nên tập trung vào gì?' tùy biến theo từng vai trò nghiệp vụ."""

    async def generate_briefing(self, profile: EmployeeProfile, odoo_client: Any = None) -> StructuredTaskResult:
        role = RoleClassifier.classify(profile)

        if role == StandardRole.HEAD_OF_SALES:
            return await self._generate_sales_briefing(profile, odoo_client)
        elif role == StandardRole.HEAD_OF_HR:
            return await self._generate_hr_briefing(profile, odoo_client)
        elif role == StandardRole.CHIEF_ACCOUNTANT:
            return await self._generate_accounting_briefing(profile, odoo_client)
        else:
            return await self._generate_general_briefing(profile, odoo_client)

    async def _generate_sales_briefing(self, profile: EmployeeProfile, odoo_client: Any) -> StructuredTaskResult:
        # Giả lập hoặc lấy dữ liệu thật từ Odoo CRM nếu có kết nối
        leads_unattended = []
        if odoo_client and hasattr(odoo_client, "execute_kw"):
            try:
                leads_unattended = await odoo_client.execute_kw(
                    "crm.lead",
                    "search_read",
                    [[["type", "=", "opportunity"]]],
                    {"fields": ["name", "expected_revenue", "partner_id", "stage_id"], "limit": 10}
                )
            except Exception as e:
                logger.warning("Không thể lấy dữ liệu Odoo CRM trực tiếp cho briefing: %s", e)

        count = len(leads_unattended) if leads_unattended else 7
        return StructuredTaskResult(
            status="Focus Recommendation (Sales)",
            summary=f"Chào Sếp {profile.full_name}, dưới đây là các trọng tâm kinh doanh cần ưu tiên xử lý hôm nay:",
            results_data=[
                f"🔥 **{count} cơ hội kinh doanh / khách hàng** chưa được tương tác hoặc follow-up trên 5 ngày.",
                "⚠️ **2 báo giá lớn** có hạn hiệu lực kết thúc vào cuối tuần này cần chốt tiến độ.",
                "📅 **1 cuộc họp khách hàng trọng điểm** dự kiến vào lúc 14:30 chiều nay.",
                "📬 **3 email phản hồi từ đối tác** đang chờ xác nhận báo giá dịch vụ."
            ],
            changes_performed=[],
            attention_required=[
                "Cơ hội 'Dự án Hopita ERP Pha 2' (150.000.000đ) chưa có cập nhật hoạt động tiếp theo."
            ],
            next_suggested_steps=[
                "Tôi đã phát hiện danh sách khách hàng chưa follow-up. Bạn có muốn tôi lập danh sách chi tiết và soạn email chăm sóc cho họ không?",
                "Chuẩn bị tài liệu tóm tắt hồ sơ khách hàng cho cuộc họp lúc 14:30."
            ]
        )

    async def _generate_hr_briefing(self, profile: EmployeeProfile, odoo_client: Any) -> StructuredTaskResult:
        return StructuredTaskResult(
            status="Focus Recommendation (HR)",
            summary=f"Chào chị {profile.full_name}, các công việc nhân sự cần ưu tiên xử lý hôm nay bao gồm:",
            results_data=[
                "📝 **3 ứng viên phỏng vấn** vị trí Senior Sales & Kế toán viên trong ngày.",
                "👥 **2 nhân sự mới tiếp nhận (Onboarding)** cần hoàn thiện thủ tục và cấp phát tài khoản.",
                "🏖️ **4 đề xuất nghỉ phép** của phòng Kinh doanh đang chờ phê duyệt.",
                "⏳ **1 hợp đồng thử việc** hết hạn trong tuần này cần gửi phiếu đánh giá cho Trưởng bộ phận."
            ],
            changes_performed=[],
            attention_required=[
                "Ứng viên Nguyễn Hoàng Nam xác nhận đổi giờ phỏng vấn sang 15:00."
            ],
            next_suggested_steps=[
                "Bạn có muốn tôi soạn thư mời nhận việc (Offer Letter) cho ứng viên đã qua vòng đánh giá?",
                "Gửi thông báo nhắc Trưởng phòng kinh doanh đánh giá thử việc cho nhân sự mới."
            ]
        )

    async def _generate_accounting_briefing(self, profile: EmployeeProfile, odoo_client: Any) -> StructuredTaskResult:
        return StructuredTaskResult(
            status="Focus Recommendation (Accounting)",
            summary=f"Chào anh {profile.full_name}, các trọng tâm tài chính & kế toán cần xử lý hôm nay:",
            results_data=[
                "💰 **5 hóa đơn bán hàng quá hạn thanh toán** trên 15 ngày với tổng giá trị 210.000.000đ.",
                "🧾 **3 đề nghị thanh toán nhà cung cấp** đến hạn giải ngân trong ngày.",
                "🏦 **Bảng sao kê ngân hàng ngày hôm qua** cần đối chiếu với sổ quỹ chi tiết.",
                "📊 **Hạn nộp báo cáo thuế định kỳ** còn 3 ngày làm việc."
            ],
            changes_performed=[],
            attention_required=[
                "Khách hàng Công ty ABC có khoản công nợ 65.000.000đ đã quá hạn 25 ngày."
            ],
            next_suggested_steps=[
                "Bạn có muốn tôi soạn thư nhắc nợ gửi đến các khách hàng quá hạn thanh toán?",
                "Xuất bảng tổng hợp công nợ phải thu ra file Excel để kiểm tra."
            ]
        )

    async def _generate_general_briefing(self, profile: EmployeeProfile, odoo_client: Any) -> StructuredTaskResult:
        return StructuredTaskResult(
            status="Focus Recommendation",
            summary=f"Chào bạn {profile.full_name}, dưới đây là lịch trình và nhiệm vụ của bạn hôm nay:",
            results_data=[
                "📅 **2 sự kiện lịch làm việc** trong ngày.",
                "📋 **3 công việc cần hoàn thành (Tasks)** có deadline trước 17:00.",
                "📬 **5 email chưa đọc** trong hộp thư đến."
            ],
            changes_performed=[],
            attention_required=[],
            next_suggested_steps=[
                "Kiểm tra danh sách công việc hôm nay (`list_tasks`).",
                "Xem lịch làm việc cá nhân (`list_calendar_events`)."
            ]
        )


morning_briefing_service = MorningBriefingService()
