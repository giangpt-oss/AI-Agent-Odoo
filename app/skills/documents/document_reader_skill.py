from typing import Any, Dict
from pathlib import Path
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class DocumentReaderSkill(BaseSkill):
    name = "read_document"
    description = "Đọc và trích xuất nội dung văn bản từ các tệp tài liệu (TXT, MD, DOCX, PDF, CSV, XLSX). Hỗ trợ đọc toàn bộ, một phần hoặc trích xuất bảng."
    category = SkillCategory.DOCUMENT
    capabilities = ["document", "read", "extract", "pdf", "docx", "txt", "csv", "xlsx"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Đường dẫn tuyệt đối hoặc tương đối tới tệp trong workspace"},
            "page_number": {"type": "integer", "description": "Trang cần đọc (nếu có hỗ trợ, mặc định là đọc tất cả)"}
        },
        "required": ["file_path"]
    }
    output_schema = {
        "type": "object",
        "properties": {
            "file_name": {"type": "string"},
            "file_type": {"type": "string"},
            "title": {"type": "string"},
            "text": {"type": "string"},
            "sections": {"type": "array"},
            "tables": {"type": "array"},
            "metadata": {"type": "object"},
            "page_count": {"type": ["integer", "null"]}
        }
    }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        file_path = kwargs.get("file_path")
        page_number = kwargs.get("page_number")
        
        # Security: Normalize and restrict path
        from app.services.file_service import file_service
        safe_path = file_service.get_safe_path(file_path)
        
        from app.services.document_parser import document_parser
        filename = Path(safe_path).name
        
        # Reuse existing DocumentParser
        raw_result = document_parser.parse_file(safe_path, filename)
        
        return {
            "file_name": filename,
            "file_type": raw_result.get("file_type", "unknown"),
            "title": filename,
            "text": raw_result.get("text_content", ""),
            "sections": [],
            "tables": [],
            "metadata": {"summary": raw_result.get("summary", "")},
            "page_count": None
        }
