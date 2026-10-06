from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class UserProfileSkill(BaseSkill):
    name = "get_user_profile"
    description = "Tra cứu hồ sơ định danh, chức vụ, bộ phận và các quyền hạn hệ thống của người đang trò chuyện."
    category = SkillCategory.SYSTEM
    capabilities = ["profile", "user_info", "roles"]
    operation_type = OperationType.READ
    input_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        roles = context.session.roles
        is_admin = bool({"admin", "ceo"}.intersection(set(roles)))
        is_sales = bool({"sales_user", "sales_manager", "sales_write"}.intersection(set(roles))) or is_admin
        
        # User metadata might hold more details if passed by the polling script
        job_title = context.metadata.get("job_title", "Nhân viên" if context.metadata.get("is_employee") else "Khách")
        department = context.metadata.get("department", "Chưa phân bổ")
        
        return {
            "full_name": context.session.user_name,
            "email": context.session.email,
            "telegram_chat_id": context.session.chat_id,
            "roles": roles,
            "is_admin": is_admin,
            "is_sales": is_sales,
            "job_title": job_title,
            "department": department,
        }
