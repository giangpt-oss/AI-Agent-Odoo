import os
import asyncio
import logging
from pathlib import Path
from typing import Any
from google import genai
from google.genai import types

from app.core.config import get_settings
from app.connectors.odoo.client import OdooAsyncClient

from app.skills.bootstrap import default_registry as skill_registry
from app.agent.router import SkillRouter
from app.agent.orchestrator import AgentOrchestrator
from app.agent.confirmation_manager import confirmation_manager
from app.models.context import SkillExecutionContext, UserSessionContext, FileAttachment

logger = logging.getLogger(__name__)


class OdooAgentService:
    """Hệ thống Trợ lý Điều hành AI Hopita sử dụng Gemini Function Calling (Tool Calling)
    kết nối trực tiếp với Odoo Cloud ERP, xử lý file đa phương thức và xuất file Excel.
    (Đã được refactor sang kiến trúc Modular Skill-based)
    """

    def __init__(self):
        self.settings = get_settings()
        self.ai_client = None
        gemini_key = self.settings.GEMINI_API_KEY
        if gemini_key:
            try:
                self.ai_client = genai.Client(api_key=gemini_key)
            except Exception as e:
                logger.error(f"Lỗi khởi tạo Gemini Client: {e}")

        # Singleton Odoo client để tái sử dụng connection pool
        self.odoo_client = OdooAsyncClient(
            base_url=self.settings.ODOO_URL,
            db=self.settings.ODOO_DB,
            username=self.settings.ODOO_ADMIN_USERNAME,
            api_key=self.settings.ODOO_API_KEY,
        )

    async def _ensure_odoo_auth(self):
        if not self.odoo_client.uid:
            await self.odoo_client.authenticate()

    async def execute_agent_turn(
        self,
        query: str,
        chat_id: int,
        employee: dict[str, Any] | None,
        user_name: str,
        attached_file_info: dict[str, Any] | None = None,
    ) -> tuple[str, list[str], Any]:
        """Xử lý câu hỏi và file của người dùng bằng AI Function Calling.
        Trả về tuple: (text, excel_files, pending_confirmation).
        """
        if not self.ai_client:
            return "⚠️ Hệ thống AI hiện chưa được cấu hình Google Gemini API Key.", [], None

        # Định danh & quyền hạn
        user_roles = set(employee.get("roles", [])) if employee else set()
        is_admin = bool({"admin", "ceo"}.intersection(user_roles))
        display_name = employee.get("full_name") if employee else user_name
        email = employee.get("email") if employee else "Chưa liên kết"

        from app.services.identity_store import identity_store
        linked = identity_store.get(chat_id)
        if not linked:
            return "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.", [], None
        user_odoo_client = OdooAsyncClient(
            base_url=self.settings.ODOO_URL, db=self.settings.ODOO_DB,
            username=linked["login"], api_key=linked["credential"]
        )

        from app.services.permissions import permission_service
        permissions = permission_service.get_permissions_for_roles(list(user_roles))
        
        # Tạo Skill Execution Context
        session = UserSessionContext(
            user_id=str(employee.get("id", "guest")) if employee else "guest",
            user_name=display_name,
            roles=list(user_roles),
            permissions=permissions,
            email=email,
            chat_id=chat_id
        )
        
        attachments = []
        if attached_file_info:
            attachments.append(
                FileAttachment(
                    filename=attached_file_info.get("filename", "Tài liệu đính kèm"),
                    summary=attached_file_info.get("summary", ""),
                    text_content=attached_file_info.get("text_content", ""),
                    raw_bytes=attached_file_info.get("raw_bytes"),
                    mime_type=attached_file_info.get("mime_type", "")
                )
            )
            
        context = SkillExecutionContext(
            session=session,
            attachments=attachments,
            providers={"odoo": user_odoo_client, "ai_client": self.ai_client},
            metadata=employee or {}
        )

        # System Instruction chi tiết
        greeting_instruction = "Xưng hô tôn trọng là 'Dạ chào Sếp " + display_name + "'." if is_admin else f"Xưng hô lịch sự là 'Chào bạn {display_name}'."
        system_instruction = (
            f"Bạn là Trợ lý Điều hành AI Hopita cấp cao, kết nối trực tiếp với Odoo Cloud ERP.\n"
            f"Người đang trò chuyện: {display_name} ({email}). {greeting_instruction}\n"
            f"Vai trò: {', '.join(user_roles) if user_roles else 'Chưa cấp quyền'}.\n\n"
            f"NGUYÊN TẮC HOẠT ĐỘNG:\n"
            f"1. Tra cứu dữ liệu doanh nghiệp (khách hàng, công ty, đối tác, sản phẩm, đơn hàng, CRM, nhân sự): BẮT BUỘC PHẢI GỌI TOOL tương ứng để lấy dữ liệu Odoo Cloud thật trước khi trả lời. Tuyệt đối KHÔNG trả lời hứa hẹn chung chung 'đang kiểm tra' hay 'sẽ kiểm tra' mà không gọi Tool!\n"
            f"2. Nếu hệ thống đã tìm thấy dữ liệu từ Odoo, hãy trình bày rõ ràng, chi tiết, kèm các số liệu cụ thể (mã số, số điện thoại, email, địa chỉ, đơn giá, số lượng).\n"
            f"3. Xử lý tài liệu đính kèm (File / Bảng tính / Báo cáo): Phân tích kỹ nội dung tài liệu người dùng gửi, trích xuất thông tin theo đúng yêu cầu.\n"
            f"4. YÊU CẦU XUẤT FILE EXCEL / BẢNG TÍNH:\n"
            f"Khi người dùng yêu cầu 'xuất file excel', 'lập file sheet', 'tải về file', 'xuất ra excel': bạn BẮT BUỘC PHẢI GỌI TOOL `export_data_to_excel` để sinh file vật lý! Hãy tổng hợp tiêu đề (title), danh sách tên cột (headers) và toàn bộ các dòng dữ liệu (rows) rồi gọi ngay tool này.\n"
            f"5. QUY TẮC AN TOÀN VÀ PHẠM VI NGHIỆP VỤ (GUARDRAIL & SECURITY POLICY):\n"
            f"Bạn là Trợ lý AI chuyên trách công việc doanh nghiệp và ERP Odoo. Tuân thủ nghiêm ngặt các rào chắn an ninh sau:\n"
            f"- YÊU CẦU XÓA / PHÁ HOẠI DỮ LIỆU (DESTRUCTIVE ACTIONS):\n"
            f"  * Nếu người dùng yêu cầu 'xóa toàn bộ dữ liệu', 'xóa database', 'xóa đơn hàng/sản phẩm/khách hàng', 'xóa hệ thống': Bạn BẮT BUỘC PHẢI TỪ CHỐI RÕ RÀNG VÀ CHUYÊN NGHIỆP: 'Dạ Sếp, để bảo đảm an toàn dữ liệu tuyệt đối cho doanh nghiệp, Trợ lý AI chỉ được phân quyền Tra cứu & Phân tích (Read-only) và hoàn toàn KHÔNG ĐƯỢC TRANG BỊ CÔNG CỤ để xóa dữ liệu trên Odoo ERP cũng như cơ sở dữ liệu nội bộ ạ.'\n"
            f"- CÂU HỎI NGOÀI LỀ KHÔNG LIÊN QUAN ĐẾN CÔNG VIỆC:\n"
            f"  * Cho phép chào hỏi lịch sự và giới thiệu tính năng nghiệp vụ.\n"
            f"  * Lịch sự từ chối các câu hỏi tán gẫu đời sống, giải trí, thể thao, ẩm thực, triết học viển vông: 'Dạ, em là Trợ lý Doanh nghiệp Odoo, em chỉ hỗ trợ các câu hỏi liên quan đến công việc, dữ liệu và nghiệp vụ công ty thôi ạ. Em có thể hỗ trợ gì cho công việc của bạn/Sếp không ạ?'\n"
            f"6. Trả lời bằng tiếng Việt gãy gọn, chuyên nghiệp, sử dụng markdown danh sách và emoji phù hợp.\n"
        )

        # Khởi tạo Router và Orchestrator
        router = SkillRouter(self.ai_client, skill_registry)
        orchestrator = AgentOrchestrator(self.ai_client, router, self.settings.DEFAULT_LLM_MODEL)
        
        return await orchestrator.process_turn(query, context, system_instruction)

    async def execute_confirmation(self, confirmation_id: str, chat_id: int, actor_id: int) -> str:
        from app.services.employee import employee_service
        from app.services.permissions import permission_service
        from app.security.kill_switch import kill_switch
        if actor_id != chat_id or await kill_switch.is_active():
            return "Không thể thực hiện yêu cầu xác nhận lúc này."
        employee = await employee_service.resolve_employee_identity(chat_id)
        if not employee:
            return "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."
        user_id = str(employee["id"])
        record = confirmation_manager.get_record(confirmation_id)
        if not record or not confirmation_manager.approve_confirmation(confirmation_id, user_id=user_id, chat_id=chat_id):
            return "Yêu cầu không thuộc tài khoản của bạn, đã hết hạn hoặc đã được xử lý."
        skill = skill_registry.get_skill(record.skill_name)
        permissions = permission_service.get_permissions_for_roles(employee.get("roles", []))
        if not permission_service.check_permission(skill, permissions):
            return "Bạn không còn quyền thực hiện thao tác này."
        if not confirmation_manager.check_and_consume_approval(
            record.skill_name, record.arguments, confirmation_id=confirmation_id, user_id=user_id, chat_id=chat_id
        ):
            return "Yêu cầu đã được xử lý."
        record.context.session.permissions = permissions
        record.context.session.roles = employee.get("roles", [])
        try:
            skill.validate_input(record.arguments)
            result = await skill.execute(record.context, **record.arguments)
            skill.validate_output(result)
            confirmation_manager.mark_executed(confirmation_id)
            import json
            return "Kết quả thực hiện:\n" + json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            confirmation_manager.mark_executed(confirmation_id, failed=True)
            logger.exception("Confirmed action failed: %s", record.skill_name)
            return "Thao tác gặp lỗi. Hãy kiểm tra trạng thái dữ liệu trước khi tạo yêu cầu mới."

    async def reject_confirmation(self, confirmation_id: str, chat_id: int, actor_id: int) -> bool:
        from app.services.employee import employee_service
        if actor_id != chat_id:
            return False
        employee = await employee_service.resolve_employee_identity(chat_id)
        return bool(employee and confirmation_manager.reject_confirmation(
            confirmation_id, user_id=str(employee["id"]), chat_id=chat_id
        ))


# Singleton instance
odoo_agent_service = OdooAgentService()
