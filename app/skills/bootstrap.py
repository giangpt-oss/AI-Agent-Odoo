import logging
from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

def build_default_registry() -> SkillRegistry:
    """Khởi tạo và đăng ký toàn bộ skills có sẵn trong hệ thống."""
    registry = SkillRegistry()
    
    # 1. Odoo Skills
    try:
        from app.skills.odoo.hr_skill import OdooHrSkill
        from app.skills.odoo.crm_skill import OdooCrmSkill
        from app.skills.odoo.partner_skill import OdooPartnerSkill
        from app.skills.odoo.profile_skill import UserProfileSkill
        
        registry.register(OdooHrSkill())
        registry.register(OdooCrmSkill())
        registry.register(OdooPartnerSkill())
        registry.register(UserProfileSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Odoo skills: {e}")
        
    # 2. Office / Spreadsheets Skills
    try:
        from app.skills.office.excel_export_skill import ExcelExportSkill
        from app.skills.office.spreadsheet_skills import SpreadsheetReadSkill, SpreadsheetAnalysisSkill, SpreadsheetEditSkill
        from app.skills.office.presentation_skills import PresentationReadSkill, PresentationCreateSkill, PresentationEditSkill
        registry.register(ExcelExportSkill())
        registry.register(SpreadsheetReadSkill())
        registry.register(SpreadsheetAnalysisSkill())
        registry.register(SpreadsheetEditSkill())
        registry.register(PresentationReadSkill())
        registry.register(PresentationCreateSkill())
        registry.register(PresentationEditSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Office skills: {e}")
        
    # 3. Core Document & Text Skills
    try:
        from app.skills.documents.document_reader_skill import DocumentReaderSkill
        from app.skills.documents.document_search_skill import DocumentSearchSkill
        from app.skills.documents.pdf_skills import PdfReadSkill, PdfMergeSkill, PdfSplitSkill, PdfExtractSkill, PdfRotateSkill
        from app.skills.documents.docx_skills import DocxReadSkill, DocxCreateSkill, DocxEditSkill
        from app.skills.text.summarization_skill import SummarizationSkill
        from app.skills.text.linguistic_skills import TranslationSkill, ProofreadingSkill
        from app.skills.research.research_skill import ResearchSkill
        
        registry.register(DocumentReaderSkill())
        registry.register(DocumentSearchSkill())
        registry.register(PdfReadSkill())
        registry.register(PdfMergeSkill())
        registry.register(PdfSplitSkill())
        registry.register(PdfExtractSkill())
        registry.register(PdfRotateSkill())
        registry.register(DocxReadSkill())
        registry.register(DocxCreateSkill())
        registry.register(DocxEditSkill())
        registry.register(SummarizationSkill())
        registry.register(TranslationSkill())
        registry.register(ProofreadingSkill())
        registry.register(ResearchSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Document/Text skills: {e}")

    # 4. File Management Skills
    try:
        from app.skills.system.file_skills import FileListSkill, FileCopySkill, FileMoveSkill, FileDeleteSkill, FileCreateDirectorySkill, CopyDirectorySkill, MoveDirectorySkill, SearchFilesSkill, RenameFileSkill
        registry.register(FileListSkill())
        registry.register(FileCopySkill())
        registry.register(FileMoveSkill())
        registry.register(FileDeleteSkill())
        registry.register(FileCreateDirectorySkill())
        registry.register(CopyDirectorySkill())
        registry.register(MoveDirectorySkill())
        registry.register(SearchFilesSkill())
        registry.register(RenameFileSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký File Management skills: {e}")
    # 5. Email Skills
    try:
        from app.skills.email.email_skills import EmailSearchSkill, EmailReadSkill, EmailDraftSkill, EmailSendSkill, EmailReplySkill
        registry.register(EmailSearchSkill())
        registry.register(EmailReadSkill())
        registry.register(EmailDraftSkill())
        registry.register(EmailSendSkill())
        registry.register(EmailReplySkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Email skills: {e}")

    # 6. Calendar Skills
    try:
        from app.skills.calendar.calendar_skills import CalendarListEventsSkill, CalendarGetEventSkill, CalendarFindFreeTimeSkill, CalendarCreateEventSkill, CalendarUpdateEventSkill, CalendarCancelEventSkill
        registry.register(CalendarListEventsSkill())
        registry.register(CalendarGetEventSkill())
        registry.register(CalendarFindFreeTimeSkill())
        registry.register(CalendarCreateEventSkill())
        registry.register(CalendarUpdateEventSkill())
        registry.register(CalendarCancelEventSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Calendar skills: {e}")
    
    # 7. Task and Reminder Skills
    try:
        from app.skills.tasks.task_skills import TaskCreateSkill, TaskListSkill, TaskUpdateSkill, TaskCompleteSkill, TaskDeleteSkill, TaskBulkCreateSkill
        registry.register(TaskCreateSkill())
        registry.register(TaskListSkill())
        registry.register(TaskUpdateSkill())
        registry.register(TaskCompleteSkill())
        registry.register(TaskDeleteSkill())
        registry.register(TaskBulkCreateSkill())
        
        from app.skills.reminders.reminder_skills import ReminderCreateSkill, ReminderListSkill, ReminderUpdateSkill, ReminderCancelSkill
        registry.register(ReminderCreateSkill())
        registry.register(ReminderListSkill())
        registry.register(ReminderUpdateSkill())
        registry.register(ReminderCancelSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Task/Reminder skills: {e}")
        
    # 8. Meeting and Note Skills
    try:
        from app.skills.meetings.meeting_skills import MeetingCreateSkill, MeetingGetSkill, MeetingListSkill, MeetingUpdateSkill, MeetingCompleteSkill
        registry.register(MeetingCreateSkill())
        registry.register(MeetingGetSkill())
        registry.register(MeetingListSkill())
        registry.register(MeetingUpdateSkill())
        registry.register(MeetingCompleteSkill())
        
        from app.skills.meetings.processing_skills import MeetingPreparationSkill, MeetingNotesProcessSkill, MeetingMinutesSkill
        registry.register(MeetingPreparationSkill())
        registry.register(MeetingNotesProcessSkill())
        registry.register(MeetingMinutesSkill())
        
        from app.skills.notes.note_skills import NoteCreateSkill, NoteGetSkill, NoteListSkill, NoteSearchSkill, NoteUpdateSkill
        registry.register(NoteCreateSkill())
        registry.register(NoteGetSkill())
        registry.register(NoteListSkill())
        registry.register(NoteSearchSkill())
        registry.register(NoteUpdateSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Meeting/Note skills: {e}")

    # 9. Knowledge Skills
    try:
        from app.skills.knowledge.knowledge_skills import (
            KnowledgeIndexSkill,
            SemanticSearchSkill,
            KnowledgeAnswerSkill,
            KnowledgeRemoveSkill,
            KnowledgeListSourcesSkill
        )
        registry.register(KnowledgeIndexSkill())
        registry.register(SemanticSearchSkill())
        registry.register(KnowledgeAnswerSkill())
        registry.register(KnowledgeRemoveSkill())
        registry.register(KnowledgeListSourcesSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Knowledge skills: {e}")

    # 10. Memory Skills
    try:
        from app.skills.memory.memory_skills import (
            MemoryCreateSkill,
            MemorySearchSkill,
            MemoryDeleteSkill
        )
        registry.register(MemoryCreateSkill())
        registry.register(MemorySearchSkill())
        registry.register(MemoryDeleteSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Memory skills: {e}")
        
    # 11. Workflow Skills
    try:
        from app.skills.workflows.template_skills import (
            TemplateCreateSkill,
            TemplateListSkill,
            TemplateRenderSkill
        )
        from app.skills.workflows.workflow_skills import (
            WorkflowCreateSkill,
            WorkflowListSkill,
            WorkflowRunSkill
        )
        registry.register(TemplateCreateSkill())
        registry.register(TemplateListSkill())
        registry.register(TemplateRenderSkill())
        registry.register(WorkflowCreateSkill())
        registry.register(WorkflowListSkill())
        registry.register(WorkflowRunSkill())
    except Exception as e:
        logger.error(f"Lỗi đăng ký Workflow skills: {e}")

    logger.info(f"Đã bootstrap {len(registry.get_all_skills())} skills vào Registry.")
    return registry

# Singleton default registry để sử dụng chung nếu cần
default_registry = build_default_registry()
