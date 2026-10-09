"""Explicit, fail-closed permissions for the skill execution path."""
from app.skills.base import BaseSkill

PERMISSION_SKILLS = {
    "READ_PROFILE": "get_user_profile",
    "READ_ODOO_CRM": "get_crm_pipeline get_partners_and_customers",
    "READ_ODOO_HR": "get_company_employees",
    "READ_ODOO_PRODUCTS": "get_products_and_inventory",
    "READ_ODOO_ORDERS": "get_sale_orders",
    "READ_ODOO_UNIVERSAL": "query_odoo_records",
    "READ_FILES": "list_files search_files search_documents read_document read_pdf read_docx read_presentation read_spreadsheet analyze_spreadsheet",
    "WRITE_FILES": "copy_file move_file rename_file create_directory copy_directory move_directory merge_pdfs split_pdf extract_pdf_pages rotate_pdf_pages create_docx edit_docx export_data_to_excel create_presentation edit_presentation edit_spreadsheet",
    "DELETE_FILES": "delete_file",
    "READ_EMAIL": "search_email read_email",
    "CREATE_EMAIL_DRAFT": "draft_email",
    "SEND_EMAIL": "send_email reply_email",
    "READ_CALENDAR": "list_calendar_events get_calendar_event find_free_time",
    "CREATE_EVENT": "create_calendar_event",
    "UPDATE_EVENT": "update_calendar_event",
    "CANCEL_EVENT": "cancel_calendar_event",
    "READ_TASKS": "list_tasks",
    "WRITE_TASKS": "create_task update_task complete_task bulk_create_tasks",
    "DELETE_TASKS": "delete_task",
    "READ_NOTES": "get_note list_notes search_notes",
    "WRITE_NOTES": "create_note update_note",
    "READ_MEETINGS": "get_meeting list_meetings prepare_meeting process_meeting_notes generate_meeting_minutes",
    "WRITE_MEETINGS": "create_meeting update_meeting complete_meeting",
    "READ_REMINDERS": "list_reminders",
    "WRITE_REMINDERS": "create_reminder update_reminder cancel_reminder",
    "READ_MEMORY": "memory_search",
    "WRITE_MEMORY": "memory_create memory_delete",
    "READ_KNOWLEDGE": "semantic_search knowledge_answer knowledge_list_sources",
    "WRITE_KNOWLEDGE": "knowledge_index knowledge_remove",
    "READ_WORKFLOWS": "template_list template_render workflow_list",
    "WRITE_WORKFLOWS": "template_create workflow_create workflow_run",
    "USE_AI": "summarize_text translate_text proofread_text research_topic",
}
SKILL_PERMISSIONS = {skill: perm for perm, names in PERMISSION_SKILLS.items() for skill in names.split()}
EMPLOYEE_PERMISSIONS = {
    "READ_PROFILE", "READ_FILES", "WRITE_FILES", "READ_EMAIL", "CREATE_EMAIL_DRAFT",
    "READ_CALENDAR", "READ_TASKS", "WRITE_TASKS", "READ_NOTES", "WRITE_NOTES",
    "READ_MEETINGS", "WRITE_MEETINGS", "READ_REMINDERS", "WRITE_REMINDERS",
    "READ_MEMORY", "WRITE_MEMORY", "READ_KNOWLEDGE", "WRITE_KNOWLEDGE",
    "READ_WORKFLOWS", "WRITE_WORKFLOWS", "USE_AI", "READ_ODOO_PRODUCTS", "READ_ODOO_ORDERS", "READ_ODOO_UNIVERSAL",
}

class PermissionService:
    def get_permissions_for_roles(self, roles: list[str]) -> list[str]:
        roles = set(roles)
        if "admin" in roles:
            return sorted(PERMISSION_SKILLS)
        perms = set(EMPLOYEE_PERMISSIONS) if roles.intersection({
            "employee", "sales_user", "sales_manager", "sales_write", "hr_user", "hr_manager", "warehouse_user"
        }) else set()
        if roles.intersection({"sales_user", "sales_manager", "sales_write"}):
            perms.add("READ_ODOO_CRM")
            perms.add("READ_ODOO_ORDERS")
            perms.add("READ_ODOO_PRODUCTS")
        if roles.intersection({"hr_user", "hr_manager"}):
            perms.add("READ_ODOO_HR")
        return sorted(perms)

    def check_permission(self, skill: BaseSkill, user_permissions: list[str]) -> bool:
        if "*" in user_permissions or "admin" in user_permissions:
            return True
        required = SKILL_PERMISSIONS.get(getattr(skill, "name", ""))
        return bool(required and required in user_permissions)

permission_service = PermissionService()
