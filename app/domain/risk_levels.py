from enum import IntEnum
from typing import Dict, Any


class ActionRiskLevel(IntEnum):
    LEVEL_0_READ_ONLY = 0
    LEVEL_1_DRAFT_ANALYSIS = 1
    LEVEL_2_REVERSIBLE_WRITE = 2
    LEVEL_3_HIGH_IMPACT = 3


# Map skill names to default risk levels
SKILL_RISK_MAP: Dict[str, ActionRiskLevel] = {
    # Level 0: Read-only
    "get_user_profile": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "get_crm_pipeline": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "get_company_employees": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "get_partners_and_customers": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "search_email": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "read_email": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "list_calendar_events": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "get_calendar_event": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "find_free_time": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "list_tasks": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "get_note": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "list_notes": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "search_notes": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "get_meeting": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "list_meetings": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "list_reminders": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "read_spreadsheet": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "analyze_spreadsheet": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "read_document": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "read_pdf": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "read_docx": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "read_presentation": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "semantic_search": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "knowledge_answer": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "knowledge_list_sources": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "summarize_text": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "translate_text": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "proofread_text": ActionRiskLevel.LEVEL_0_READ_ONLY,
    "research_topic": ActionRiskLevel.LEVEL_0_READ_ONLY,

    # Level 1: Draft / Analysis / Intermediate preview
    "draft_email": ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS,
    "prepare_meeting": ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS,
    "process_meeting_notes": ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS,
    "generate_meeting_minutes": ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS,
    "export_data_to_excel": ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS,
    "create_docx": ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS,
    "create_presentation": ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS,
    "template_render": ActionRiskLevel.LEVEL_1_DRAFT_ANALYSIS,

    # Level 2: Reversible write action (Requires preview & confirmation)
    "create_task": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "update_task": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "complete_task": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "bulk_create_tasks": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "create_calendar_event": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "update_calendar_event": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "cancel_calendar_event": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "create_note": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "update_note": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "create_meeting": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "update_meeting": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "create_reminder": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "update_reminder": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "cancel_reminder": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "edit_spreadsheet": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "edit_docx": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "edit_presentation": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "copy_file": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "move_file": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "rename_file": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,
    "create_directory": ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE,

    # Level 3: High-impact action (Explicit confirmation + immediate authorization check)
    "send_email": ActionRiskLevel.LEVEL_3_HIGH_IMPACT,
    "reply_email": ActionRiskLevel.LEVEL_3_HIGH_IMPACT,
    "delete_task": ActionRiskLevel.LEVEL_3_HIGH_IMPACT,
    "delete_file": ActionRiskLevel.LEVEL_3_HIGH_IMPACT,
    "knowledge_remove": ActionRiskLevel.LEVEL_3_HIGH_IMPACT,
    "memory_delete": ActionRiskLevel.LEVEL_3_HIGH_IMPACT,
}


def get_skill_risk_level(skill_name: str) -> ActionRiskLevel:
    return SKILL_RISK_MAP.get(skill_name, ActionRiskLevel.LEVEL_2_REVERSIBLE_WRITE)
