import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.domain.employee import EmployeeProfile
from app.domain.roles import RoleClassifier, StandardRole
from app.domain.task_identity import build_owner_key
from app.domain.task_models import StructuredTaskResult
from app.providers.tasks.local import LocalTaskProvider
from app.services.gateway_client import GatewayClient, gateway_client

logger = logging.getLogger(__name__)


class MorningBriefingService:
    """Xử lý phân tích và tổng hợp điểm tin 'Hôm nay tôi có gì?' từ dữ liệu thật."""

    def __init__(self, task_provider: Optional[LocalTaskProvider] = None):
        self.task_provider = task_provider or LocalTaskProvider()

    async def generate_real_briefing(
        self,
        user_info: Dict[str, Any],
        client: Optional[GatewayClient] = None,
        now_dt: Optional[datetime] = None,
    ) -> str:
        """Tổng hợp điểm tin từ các nguồn dữ liệu thật qua Gateway và kho Task nội bộ.
        - Không sử dụng số liệu minh họa hoặc suy đoán.
        - Gọi đồng thời các nguồn độc lập, timeout từng nguồn tối đa 10 giây.
        - Một nguồn lỗi không ảnh hưởng đến các nguồn còn lại.
        """
        client = client or gateway_client
        user_id = user_info.get("user_id")
        user_name = user_info.get("name") or "Quý nhân viên"
        tz_name = user_info.get("tz") or "Asia/Bangkok"

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Bangkok")
            tz_name = "Asia/Bangkok"

        if now_dt is not None:
            now_tz = now_dt if now_dt.tzinfo else now_dt.replace(tzinfo=tz)
            now_tz = now_tz.astimezone(tz)
        else:
            now_tz = datetime.now(tz)

        start_of_day = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_next_day = start_of_day + timedelta(days=1)

        owner_key = build_owner_key(
            user_info.get("tenant"),
            user_info.get("database"),
            user_info.get("odoo_uid"),
        )

        # 1. Fetch data concurrently
        async def fetch_calendar():
            try:
                return await asyncio.wait_for(
                    client.get_calendar_events(
                        user_id=user_id,
                        time_min=start_of_day.isoformat(),
                        time_max=start_of_next_day.isoformat(),
                    ),
                    timeout=10.0,
                )
            except Exception as e:
                logger.warning("Error or timeout fetching calendar: %s", e)
                return {"status": "TIMEOUT" if isinstance(e, asyncio.TimeoutError) else "ERROR", "events": []}

        async def fetch_gmail():
            try:
                return await asyncio.wait_for(
                    client.get_unread_emails(user_id=user_id),
                    timeout=10.0,
                )
            except Exception as e:
                logger.warning("Error or timeout fetching gmail: %s", e)
                return {"status": "TIMEOUT" if isinstance(e, asyncio.TimeoutError) else "ERROR", "total_unread": None, "messages": []}

        async def fetch_tasks():
            try:
                # Chỉ lấy task thuộc owner_key xác thực của user này
                tasks = await self.task_provider.list_tasks({}, owner_key=owner_key)
                return {"status": "OK", "tasks": tasks}
            except Exception as e:
                logger.warning("Error fetching local tasks: %s", e)
                return {"status": "ERROR", "tasks": []}

        cal_res, gmail_res, task_res = await asyncio.gather(
            fetch_calendar(), fetch_gmail(), fetch_tasks(), return_exceptions=False
        )

        # 2. Build Markdown output
        lines: List[str] = [
            f"👋 Chào bạn **{user_name}**! Dưới đây là điểm tin và lịch trình hôm nay ({now_tz.strftime('%d/%m/%Y')}):\n"
        ]

        # --- Section 1: Lịch làm việc (Google Calendar) ---
        lines.append("📅 **LỊCH LÀM VIỆC HÔM NAY:**")
        cal_status = cal_res.get("status")
        if cal_status == "OK":
            events = cal_res.get("events", [])
            if not events:
                lines.append("• Không có sự kiện lịch nào trong ngày.")
            else:
                for ev in events:
                    summary = ev.get("summary") or "(Không có tiêu đề)"
                    is_all_day = ev.get("is_all_day", False)
                    link = ev.get("link")
                    loc = ev.get("location")
                    loc_suffix = f" (Địa điểm: {loc})" if loc else ""

                    if is_all_day:
                        time_str = "Cả ngày"
                    else:
                        start_raw = ev.get("start") or ""
                        end_raw = ev.get("end") or ""
                        try:
                            st_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(tz)
                            en_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(tz)
                            time_str = f"{st_dt.strftime('%H:%M')} - {en_dt.strftime('%H:%M')}"
                        except Exception:
                            time_str = f"{start_raw} - {end_raw}"

                    title_str = f"[{summary}]({link})" if link else f"**{summary}**"
                    lines.append(f"• `{time_str}`: {title_str}{loc_suffix}")
        elif cal_status == "NOT_CONNECTED":
            lines.append("• ⚠️ _Chưa liên kết Google Calendar. Vui lòng mở Cổng Portal để kết nối._")
        elif cal_status == "PERMISSION_DENIED":
            lines.append("• ⚠️ _Chưa cấp quyền Calendar. Vui lòng mở Cổng Portal để cấp thêm quyền._")
        elif cal_status == "RECONNECT_REQUIRED":
            lines.append("• ⚠️ _Kết nối Google hết hạn hoặc bị thu hồi. Vui lòng kết nối lại trên Portal._")
        else:
            lines.append("• ⚠️ _Không thể tải lịch Google (Lỗi kết nối hoặc hết thời gian chờ)._")

        lines.append("")

        # --- Section 2: Công việc cá nhân (Tasks) ---
        lines.append("📋 **CÔNG VIỆC CẦN XỬ LÝ (TASKS):**")
        task_status = task_res.get("status")
        if task_status != "OK":
            lines.append("• ⚠️ _Không thể tải danh sách công việc (Lỗi cơ sở dữ liệu nội bộ)._")
        else:
            all_tasks = task_res.get("tasks", [])
            # Exclude completed / cancelled
            active_tasks = [t for t in all_tasks if t.get("status") not in ["DONE", "CANCELLED", "COMPLETED"]]

            overdue_tasks = []
            today_tasks = []

            for t in active_tasks:
                due_str = t.get("due_at")
                if due_str:
                    try:
                        clean_due = due_str.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(clean_due)
                        if not dt.tzinfo:
                            dt = dt.replace(tzinfo=tz)
                        due_tz = dt.astimezone(tz)
                        if due_tz < start_of_day:
                            overdue_tasks.append((t, due_tz))
                        elif start_of_day <= due_tz < start_of_next_day:
                            today_tasks.append((t, due_tz))
                    except Exception:
                        pass

            if not active_tasks:
                lines.append("• Hiện tại bạn không có công việc nào chưa hoàn thành.")
            else:
                if overdue_tasks:
                    lines.append(f"• ⚠️ **Quá hạn ({len(overdue_tasks)} việc):**")
                    for ot, d_tz in overdue_tasks:
                        lines.append(f"   - {ot.get('title')} (Hạn: {d_tz.strftime('%H:%M %d/%m/%Y')})")
                if today_tasks:
                    lines.append(f"• 🎯 **Đến hạn hôm nay ({len(today_tasks)} việc):**")
                    for tt, d_tz in today_tasks:
                        lines.append(f"   - {tt.get('title')} (Hạn: {d_tz.strftime('%H:%M')})")
                if not overdue_tasks and not today_tasks:
                    lines.append(f"• Bạn có {len(active_tasks)} công việc đang tiến hành (chưa đến hạn hôm nay).")

        lines.append("")

        # --- Section 3: Hộp thư Gmail ---
        lines.append("📬 **HỘP THƯ GMAIL:**")
        g_status = gmail_res.get("status")
        if g_status == "OK":
            total_unread = gmail_res.get("total_unread")
            messages = gmail_res.get("messages", [])
            if total_unread is not None:
                lines.append(f"• Tổng số thư chưa đọc: **{total_unread}**")
            else:
                lines.append("• Tổng số thư chưa đọc: _(chưa xác định)_")
            if messages:
                lines.append("• Thư mới nhất:")
                for m in messages[:5]:
                    sender = m.get("from") or "Người gửi"
                    subj = m.get("subject") or "Không có tiêu đề"
                    mlink = m.get("link")
                    m_title = f"[{subj}]({mlink})" if mlink else f"**{subj}**"
                    lines.append(f"   - Từ `{sender}`: {m_title}")
        elif g_status == "PERMISSION_DENIED":
            lines.append("• ⚠️ _Chưa cấp quyền đọc Gmail. Vui lòng mở Cổng Portal để cấp thêm quyền._")
        elif g_status == "NOT_CONNECTED":
            lines.append("• ⚠️ _Chưa liên kết tài khoản Google._")
        elif g_status == "RECONNECT_REQUIRED":
            lines.append("• ⚠️ _Kết nối Google cần xác thực lại trên Portal._")
        else:
            lines.append("• ⚠️ _Không thể tải danh sách email (Lỗi kết nối hoặc hết thời gian chờ)._")

        lines.append("")
        lines.append(f"🕒 _Dữ liệu cập nhật lúc: {now_tz.strftime('%H:%M:%S %d/%m/%Y')} (Múi giờ: {tz_name})_")

        return "\n".join(lines)

    # --- Backwards compatibility for existing unit tests ---
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

        if not leads_unattended and not odoo_client:
            return StructuredTaskResult(
                status="Focus Recommendation (Sales)",
                summary=f"Chào Sếp {profile.full_name}, hiện chưa có kết nối dữ liệu Odoo CRM trực tiếp.",
                results_data=["⚠️ Chưa kết nối dữ liệu Odoo CRM thật để tải cơ hội kinh doanh, khách hàng, báo giá và cuộc họp khách hàng. Vui lòng liên kết tài khoản để xem điểm tin."],
                changes_performed=[],
                attention_required=["Yêu cầu cấu hình liên kết Odoo ERP."],
                next_suggested_steps=["Đăng nhập Cổng Portal để liên kết tài khoản Odoo và Google."]
            )

        count = len(leads_unattended)
        return StructuredTaskResult(
            status="Focus Recommendation (Sales)",
            summary=f"Chào Sếp {profile.full_name}, dưới đây là các trọng tâm kinh doanh cần ưu tiên xử lý hôm nay:",
            results_data=[
                f"🔥 **{count} cơ hội kinh doanh / khách hàng** cần tương tác hoặc theo dõi tiến độ.",
            ],
            changes_performed=[],
            attention_required=[],
            next_suggested_steps=[
                "Mở phân hệ CRM để kiểm tra chi tiết các cơ hội bán hàng.",
            ]
        )

    async def _generate_hr_briefing(self, profile: EmployeeProfile, odoo_client: Any) -> StructuredTaskResult:
        if not odoo_client:
            return StructuredTaskResult(
                status="Focus Recommendation (HR)",
                summary=f"Chào {profile.full_name}, hiện chưa có kết nối dữ liệu Nhân sự trực tiếp.",
                results_data=["⚠️ Chưa kết nối dữ liệu Odoo HR thật để tải thông tin nhân sự / ứng viên. Vui lòng liên kết tài khoản để xem điểm tin."],
                changes_performed=[],
                attention_required=["Yêu cầu cấu hình liên kết Odoo ERP."],
                next_suggested_steps=["Đăng nhập Cổng Portal để liên kết tài khoản."]
            )
        return StructuredTaskResult(
            status="Focus Recommendation (HR)",
            summary=f"Chào {profile.full_name}, các công việc nhân sự cần ưu tiên xử lý hôm nay bao gồm:",
            results_data=[],
            changes_performed=[],
            attention_required=[],
            next_suggested_steps=[]
        )

    async def _generate_accounting_briefing(self, profile: EmployeeProfile, odoo_client: Any) -> StructuredTaskResult:
        if not odoo_client:
            return StructuredTaskResult(
                status="Focus Recommendation (Accounting)",
                summary=f"Chào {profile.full_name}, hiện chưa có kết nối dữ liệu Kế toán trực tiếp.",
                results_data=["⚠️ Chưa kết nối dữ liệu Odoo Accounting thật để tải công nợ / hóa đơn. Vui lòng liên kết tài khoản để xem điểm tin."],
                changes_performed=[],
                attention_required=["Yêu cầu cấu hình liên kết Odoo ERP."],
                next_suggested_steps=["Đăng nhập Cổng Portal để liên kết tài khoản."]
            )
        return StructuredTaskResult(
            status="Focus Recommendation (Accounting)",
            summary=f"Chào {profile.full_name}, các công việc kế toán cần ưu tiên xử lý hôm nay bao gồm:",
            results_data=[],
            changes_performed=[],
            attention_required=[],
            next_suggested_steps=[]
        )

    async def _generate_general_briefing(self, profile: EmployeeProfile, odoo_client: Any) -> StructuredTaskResult:
        if not odoo_client:
            return StructuredTaskResult(
                status="Focus Recommendation (General)",
                summary=f"Chào {profile.full_name}, hiện chưa có kết nối dữ liệu Odoo trực tiếp.",
                results_data=["⚠️ Chưa kết nối dữ liệu Odoo thật để tải công việc / nhiệm vụ. Vui lòng liên kết tài khoản để xem điểm tin."],
                changes_performed=[],
                attention_required=["Yêu cầu cấu hình liên kết Odoo ERP."],
                next_suggested_steps=["Đăng nhập Cổng Portal để liên kết tài khoản."]
            )
        return StructuredTaskResult(
            status="Focus Recommendation (General)",
            summary=f"Chào {profile.full_name}, dưới đây là các việc trọng tâm hôm nay:",
            results_data=[],
            changes_performed=[],
            attention_required=[],
            next_suggested_steps=[]
        )


morning_briefing_service = MorningBriefingService()
