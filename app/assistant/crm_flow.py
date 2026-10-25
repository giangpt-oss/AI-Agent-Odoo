import re
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.domain.employee import EmployeeProfile
from app.models.context import SkillExecutionContext, UserSessionContext
from app.agent.confirmation_manager import confirmation_manager

logger = logging.getLogger(__name__)


@dataclass
class CrmLeadDraft:
    chat_id: int
    user_id: str
    opportunity_name: Optional[str] = None
    partner_id: Optional[int] = None
    partner_name: Optional[str] = None
    expected_revenue: Optional[float] = None
    description: Optional[str] = None
    last_confirmation_id: Optional[str] = None
    pending_selection_partners: Optional[List[Dict[str, Any]]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def is_expired(self, ttl_seconds: int = 900) -> bool:
        return time.time() - self.updated_at > ttl_seconds

    def touch(self):
        self.updated_at = time.time()


def parse_vietnamese_currency(text: str) -> Optional[float]:
    """Phân tích số tiền bằng tiếng Việt:
    - '250 triệu', '250 tr', '250tr', '250 million' -> 250_000_000.0
    - '2.5 tỷ', '2,5 tỷ', '2.5 billion' -> 2_500_000_000.0
    - '500 nghìn', '500 ngàn', '500k' -> 500_000.0
    - '250.000.000', '250000000' -> 250_000_000.0
    Trả về None nếu không phát hiện số tiền hợp lệ (không tự đoán).
    """
    if not text:
        return None
    t = text.lower().strip()

    # 1. Tỷ / Billion
    ty_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:tỷ|ty|b|billion)\b', t)
    if ty_match:
        val_str = ty_match.group(1).replace(",", ".")
        try:
            return float(val_str) * 1_000_000_000.0
        except ValueError:
            pass

    # 2. Triệu / Million / Tr
    tr_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:triệu|trieu|tr|m|million)\b', t)
    if tr_match:
        val_str = tr_match.group(1).replace(",", ".")
        try:
            return float(val_str) * 1_000_000.0
        except ValueError:
            pass

    # 3. Nghìn / Ngàn / K
    k_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:nghìn|nghin|ngàn|ngan|k)\b', t)
    if k_match:
        val_str = k_match.group(1).replace(",", ".")
        try:
            return float(val_str) * 1_000.0
        except ValueError:
            pass

    # 4. Số tiền dạng chuẩn: 250.000.000 hoặc 250,000,000 hoặc 250000000
    num_match = re.search(r'(?:giá trị|doanh thu|giá|tiền)?\s*:?\s*(\d{1,3}(?:[.,]\d{3})+(?:,\d+)?|\d{5,})\s*(?:vnđ|vnd|đ|d)?', t)
    if num_match:
        raw_num = num_match.group(1)
        # Bỏ dấu chấm/phẩy phân tách hàng nghìn
        clean_num = raw_num.replace(".", "").replace(",", "")
        try:
            val = float(clean_num)
            if val >= 1000:
                return val
        except ValueError:
            pass

    return None


class CrmOpportunityFlow:
    """Quản lý luồng hội thoại nhiều lượt tạo cơ hội CRM trên Odoo:
    Telegram -> tìm khách hàng thật -> bổ sung thông tin -> xem trước -> xác nhận -> ghi Odoo.
    """

    def __init__(self):
        self._sessions: Dict[int, CrmLeadDraft] = {}

    def get_session(self, chat_id: int) -> Optional[CrmLeadDraft]:
        draft = self._sessions.get(chat_id)
        if draft:
            if draft.is_expired():
                del self._sessions[chat_id]
                return None
            return draft
        return None

    def clear_session(self, chat_id: int):
        self._sessions.pop(chat_id, None)

    def is_crm_intent(self, query: str, chat_id: int) -> bool:
        """Nhận diện xem câu hỏi có thuộc luồng tạo cơ hội CRM không."""
        q_lower = query.lower().strip()

        # 1. Đang có phiên soạn thảo dở dang trong 15 phút
        active_draft = self.get_session(chat_id)
        if active_draft is not None:
            return True

        # 2. Câu lệnh mới yêu cầu tạo cơ hội
        create_triggers = [
            "tạo cơ hội", "thêm cơ hội", "tạo lead", "thêm lead",
            "tạo deal", "thêm deal", "tạo crm", "thêm crm",
            "create opportunity", "create lead", "lập cơ hội"
        ]
        inquiry_triggers = [
            "có bao nhiêu", "danh sách cơ hội", "xem cơ hội", "tra cứu cơ hội",
            "báo cáo", "pipeline", "tình hình cơ hội", "thống kê cơ hội"
        ]
        has_create = any(t in q_lower for t in create_triggers)
        has_inquiry = any(t in q_lower for t in inquiry_triggers)

        return has_create and not has_inquiry

    async def _search_odoo_partners(self, odoo_client: Any, query_str: str) -> List[Dict[str, Any]]:
        """Tra cứu đối tác/khách hàng thật từ Odoo Cloud, trả về danh sách có ID."""
        clean_query = query_str.strip().strip("'\"")
        if not clean_query or not odoo_client:
            return []

        try:
            await odoo_client.authenticate()
            domain = [["name", "ilike", clean_query], ["active", "=", True]]
            records = await odoo_client.execute_kw(
                "res.partner",
                "search_read",
                [domain],
                {"fields": ["id", "name", "phone", "email", "vat"], "limit": 10}
            )
            return records or []
        except Exception as e:
            logger.warning(f"Lỗi tra cứu res.partner cho '{clean_query}': {e}")
            return []

    async def handle_crm_flow(
        self,
        query: str,
        chat_id: int,
        profile: EmployeeProfile,
        odoo_client: Any
    ) -> Tuple[bool, str, List[str], Optional[Dict[str, Any]]]:
        """Điểm xử lý luồng tạo cơ hội CRM nhiều lượt."""
        q = (query or "").strip()
        q_lower = q.lower()

        # 1. Hủy bỏ phiên đang soạn nếu người dùng yêu cầu
        if any(w == q_lower or q_lower.startswith(w) for w in ["hủy", "hủy bỏ", "bỏ qua", "cancel", "dừng lại"]):
            draft = self.get_session(chat_id)
            if draft:
                if draft.last_confirmation_id:
                    confirmation_manager.reject_confirmation(
                        draft.last_confirmation_id,
                        user_id=profile.user_id,
                        chat_id=chat_id
                    )
                self.clear_session(chat_id)
                return True, "❌ **Đã hủy yêu cầu tạo cơ hội CRM.** Phiên soạn thảo đã được dọn dẹp.", [], None

        draft = self.get_session(chat_id)
        if not draft:
            draft = CrmLeadDraft(chat_id=chat_id, user_id=profile.user_id)
            self._sessions[chat_id] = draft

        draft.touch()

        # 2. Xử lý trường hợp người dùng đang chọn khách hàng từ danh sách trùng lặp
        if draft.pending_selection_partners:
            candidates = draft.pending_selection_partners
            chosen_partner = None

            # Kiểm tra xem người dùng chọn số thứ tự (ví dụ: "1", "2")
            idx_match = re.match(r'^(?:chọn\s+)?(?:số\s+)?(\d+)$', q_lower)
            if idx_match:
                chosen_idx = int(idx_match.group(1))
                if 1 <= chosen_idx <= len(candidates):
                    chosen_partner = candidates[chosen_idx - 1]
                else:
                    # Kiểm tra xem có khớp ID trực tiếp không
                    for p in candidates:
                        if p.get("id") == chosen_idx:
                            chosen_partner = p
                            break

            if chosen_partner:
                draft.partner_id = int(chosen_partner["id"])
                draft.partner_name = chosen_partner.get("name")
                draft.pending_selection_partners = None
            else:
                # Nếu không chọn số thứ tự, thử coi câu này là từ khóa tìm kiếm mới
                new_partners = await self._search_odoo_partners(odoo_client, q)
                if len(new_partners) == 1:
                    draft.partner_id = int(new_partners[0]["id"])
                    draft.partner_name = new_partners[0].get("name")
                    draft.pending_selection_partners = None
                elif len(new_partners) > 1:
                    draft.pending_selection_partners = new_partners
                    lines = [
                        f"🔍 Tìm thấy **{len(new_partners)} khách hàng** khớp với '{q}' trên Odoo:",
                        "────────────────────────────"
                    ]
                    for idx, p in enumerate(new_partners, 1):
                        phone_str = f" | SĐT: {p.get('phone')}" if p.get("phone") else ""
                        lines.append(f"**{idx}.** [ID: #{p['id']}] **{p.get('name')}**{phone_str}")
                    lines.append("\n👉 Vui lòng nhắn **Số thứ tự (1, 2, ...)** hoặc **ID khách hàng** để chọn:")
                    return True, "\n".join(lines), [], None
                else:
                    lines = [
                        f"⚠️ Lựa chọn '{q}' không hợp lệ trong danh sách sau:",
                        "────────────────────────────"
                    ]
                    for idx, p in enumerate(candidates, 1):
                        lines.append(f"**{idx}.** [ID: #{p['id']}] {p.get('name')}")
                    lines.append("\n👉 Vui lòng nhắn số thứ tự (ví dụ: `1`) hoặc ID khách hàng để chọn:")
                    return True, "\n".join(lines), [], None

        # 3. Trích xuất thông tin từ câu lệnh khởi tạo hoặc tin nhắn bổ sung
        # 3.1. Phân tích doanh thu dự kiến
        found_revenue = parse_vietnamese_currency(q)
        if found_revenue is not None:
            if draft.expected_revenue is not None and draft.last_confirmation_id:
                # Sửa đổi giá trị sau khi đã có preview -> hủy xác nhận cũ
                confirmation_manager.reject_confirmation(
                    draft.last_confirmation_id,
                    user_id=profile.user_id,
                    chat_id=chat_id
                )
                draft.last_confirmation_id = None
            draft.expected_revenue = found_revenue

        # 3.2. Kiểm tra lệnh sửa đổi trực tiếp (sau preview)
        edit_revenue_match = re.search(r'(?:đổi|sửa|thay)\s+(?:doanh thu|giá trị|tiền)\s+(?:thành|là)?\s*(.+)', q_lower)
        if edit_revenue_match:
            new_rev = parse_vietnamese_currency(edit_revenue_match.group(1))
            if new_rev is not None:
                if draft.last_confirmation_id:
                    confirmation_manager.reject_confirmation(
                        draft.last_confirmation_id,
                        user_id=profile.user_id,
                        chat_id=chat_id
                    )
                    draft.last_confirmation_id = None
                draft.expected_revenue = new_rev

        edit_name_match = re.search(r'(?:đổi|sửa|thay)\s+tên\s+(?:thành|là)?\s*(.+)', q_lower)
        if edit_name_match:
            new_name = edit_name_match.group(1).strip()
            if new_name:
                if draft.last_confirmation_id:
                    confirmation_manager.reject_confirmation(
                        draft.last_confirmation_id,
                        user_id=profile.user_id,
                        chat_id=chat_id
                    )
                    draft.last_confirmation_id = None
                draft.opportunity_name = new_name

        # 3.3. Trích xuất tên cơ hội và tên khách hàng từ câu lệnh ban đầu
        # Câu mẫu: "Tạo cơ hội DEMO Máy siêu âm cho khách hàng X, giá trị 250 triệu"
        if not draft.opportunity_name or not draft.partner_id:
            # Tìm tên khách hàng
            partner_query = None
            customer_match = re.search(
                r'(?:cho\s+khách\s+hàng|cho\s+đối\s+tác|cho|khách\s+hàng|đối\s+tác)\s+([^,]+?)(?:,\s*giá\s+trị|\s+giá\s+trị|\s+với\s+giá|\s+doanh\s+thu|\s*$|\.)',
                q,
                re.IGNORECASE
            )
            if customer_match:
                extracted_partner = customer_match.group(1).strip()
                # Loại bỏ các từ thừa
                extracted_partner = re.sub(r'^(?:là|tên\s+là|bệnh\s+viện|công\s+ty)\s+', '', extracted_partner, flags=re.IGNORECASE)
                if extracted_partner:
                    partner_query = customer_match.group(1).strip()

            # Tìm tên cơ hội
            opp_match = re.search(
                r'(?:tạo|thêm|lập)\s+(?:mới\s+)?(?:cơ\s+hội|lead|deal|crm)\s+([^,]+?)(?:\s+cho\s+|\s+khách\s+hàng\s+|\s+đối\s+tác\s+|\s+giá\s+trị\s+|,\s*|\.|$)',
                q,
                re.IGNORECASE
            )
            if opp_match and not draft.opportunity_name:
                cand_name = opp_match.group(1).strip()
                if cand_name and len(cand_name) >= 3 and not cand_name.lower().startswith("mới"):
                    draft.opportunity_name = cand_name

            # Nếu chưa có tên cơ hội mà chỉ có tên trong câu bổ sung
            if not draft.opportunity_name and not customer_match and not found_revenue:
                # Nếu câu không có số tiền và không tìm thấy khách, có thể đây là tên cơ hội
                if len(q) >= 3 and not any(w in q_lower for w in ["tạo", "cơ hội", "lead", "deal"]):
                    draft.opportunity_name = q

            # Tra cứu khách hàng trên Odoo nếu có từ khóa
            if partner_query and not draft.partner_id:
                partners = await self._search_odoo_partners(odoo_client, partner_query)
                if len(partners) == 1:
                    draft.partner_id = int(partners[0]["id"])
                    draft.partner_name = partners[0].get("name")
                elif len(partners) > 1:
                    draft.pending_selection_partners = partners
                    lines = [
                        f"🔍 Tìm thấy **{len(partners)} khách hàng** khớp với '{partner_query}' trên Odoo:",
                        "────────────────────────────"
                    ]
                    for idx, p in enumerate(partners, 1):
                        phone_str = f" | SĐT: {p.get('phone')}" if p.get("phone") else ""
                        lines.append(f"**{idx}.** [ID: #{p['id']}] **{p.get('name')}**{phone_str}")
                    lines.append("\n👉 Vui lòng nhắn **Số thứ tự (1, 2, ...)** hoặc **ID khách hàng** để chọn:")
                    return True, "\n".join(lines), [], None
                else:
                    return (
                        True,
                        f"❌ Không tìm thấy khách hàng nào khớp với tên **'{partner_query}'** trên Odoo.\n\n"
                        f"💡 **Lưu ý:** Hệ thống chưa hỗ trợ tự tạo khách hàng mới. "
                        f"Vui lòng cung cấp lại tên chính xác hoặc Mã số thuế của khách hàng:",
                        [],
                        None
                    )

        # 4. Kiểm tra các thông tin còn thiếu (Slot Filling)
        missing_slots = []
        if not draft.opportunity_name:
            missing_slots.append("Tên cơ hội kinh doanh")
        if not draft.partner_id:
            missing_slots.append("Khách hàng / Đối tác Odoo")
        if draft.expected_revenue is None:
            missing_slots.append("Doanh thu dự kiến")

        if missing_slots:
            # Đang thiếu slot nào thì hỏi người dùng slot đó
            current_status = []
            if draft.opportunity_name:
                current_status.append(f"• Tên cơ hội: **{draft.opportunity_name}**")
            if draft.partner_name:
                current_status.append(f"• Khách hàng: **{draft.partner_name}** (ID: #{draft.partner_id})")
            if draft.expected_revenue is not None:
                rev_fmt = f"{draft.expected_revenue:,.0f}".replace(",", ".") + " VNĐ"
                current_status.append(f"• Doanh thu: **{rev_fmt}**")

            status_msg = "\n".join(current_status) + "\n\n" if current_status else ""

            if not draft.opportunity_name:
                prompt_msg = "📝 Vui lòng nhập **Tên cơ hội kinh doanh** (ví dụ: *'DEMO Máy siêu âm Doppler 4D'*):"
            elif not draft.partner_id:
                prompt_msg = "🏢 Cơ hội này dành cho **Khách hàng nào trên Odoo**? Vui lòng cung cấp tên hoặc mã số thuế để tra cứu:"
            else:
                prompt_msg = "💰 Vui lòng cung cấp **Doanh thu dự kiến** (ví dụ: *'250 triệu'* hoặc *'50.000.000 VNĐ'*):"

            return (
                True,
                f"📋 **BỔ SUNG THÔNG TIN CƠ HỘI CRM**\n"
                f"────────────────────────────\n"
                f"{status_msg}"
                f"{prompt_msg}\n\n"
                f"*(Gõ 'hủy' nếu bạn muốn dừng luồng này)*",
                [],
                None
            )

        # 5. Đã đủ 3 slot bắt buộc -> Tạo bản XEM TRƯỚC (PREVIEW) & Yêu cầu duyệt
        rev_formatted = f"{draft.expected_revenue:,.0f}".replace(",", ".") + " VNĐ"
        salesperson_name = profile.full_name or profile.company_email

        # Hủy yêu cầu xác nhận cũ nếu có trước khi tạo mới
        if draft.last_confirmation_id:
            confirmation_manager.reject_confirmation(
                draft.last_confirmation_id,
                user_id=profile.user_id,
                chat_id=chat_id
            )
            draft.last_confirmation_id = None

        context = SkillExecutionContext(
            session=UserSessionContext(
                user_id=profile.user_id,
                user_name=profile.full_name,
                roles=profile.roles,
                permissions=profile.permissions,
                email=profile.company_email,
                chat_id=chat_id
            ),
            metadata=profile.metadata,
            providers={"odoo": odoo_client}
        )

        arguments = {
            "name": draft.opportunity_name,
            "partner_id": draft.partner_id,
            "expected_revenue": draft.expected_revenue,
            "description": draft.description or ""
        }

        preview_data = {
            "opportunity_name": draft.opportunity_name,
            "partner_name": draft.partner_name,
            "partner_id": draft.partner_id,
            "expected_revenue": rev_formatted,
            "currency": "VNĐ",
            "salesperson": salesperson_name
        }

        rec = confirmation_manager.create_request(
            skill_name="create_crm_lead",
            arguments=arguments,
            preview=preview_data,
            context=context
        )
        draft.last_confirmation_id = rec.id

        preview_text = (
            f"📋 **XEM TRƯỚC CƠ HỘI KINH DOANH (CRM OPPORTUNITY)**\n"
            f"────────────────────────────\n"
            f"• **Tên cơ hội:** {draft.opportunity_name}\n"
            f"• **Khách hàng:** {draft.partner_name} (ID: #{draft.partner_id})\n"
            f"• **Doanh thu dự kiến:** {rev_formatted}\n"
            f"• **Tiền tệ:** VNĐ\n"
            f"• **Người phụ trách:** {salesperson_name}\n\n"
            f"⚠️ **LƯU Ý AN TOÀN:** Đây là thao tác ghi dữ liệu thật vào phân hệ Odoo CRM.\n"
            f"Vui lòng kiểm tra kỹ thông tin trên và bấm **Xác nhận** bên dưới để tạo bản ghi, "
            f"hoặc bấm **Hủy** để hủy bỏ.\n\n"
            f"💡 *Để sửa thông tin, bạn có thể nhắn ngay: 'Đổi doanh thu thành 300 triệu' hoặc 'Đổi tên thành ...'*"
        )

        confirmation_dict = rec.to_dict()
        confirmation_dict["status"] = "confirmation_required"

        return True, preview_text, [], confirmation_dict


crm_opportunity_flow = CrmOpportunityFlow()
