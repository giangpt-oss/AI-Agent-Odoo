import os
from typing import Any
from pathlib import Path
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.services.file_service import file_service

class DocumentSearchSkill(BaseSkill):
    name = "search_documents"
    description = "Tìm kiếm tài liệu dựa trên tên file, phần mở rộng, hoặc từ khóa."
    category = SkillCategory.DOCUMENT
    capabilities = ["document", "search", "find"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Từ khóa tìm kiếm (theo tên hoặc nội dung)"},
            "extension": {"type": "string", "description": "Phần mở rộng file (vd: .pdf, .docx)"},
            "directory": {"type": "string", "description": "Thư mục tìm kiếm (mặc định workspace gốc)", "default": "."}
        },
        "required": ["query"]
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        import asyncio
        query = kwargs["query"].lower()
        extension = kwargs.get("extension", "").lower()
        dir_path = kwargs.get("directory", ".")
        
        safe_dir = file_service.get_safe_path(dir_path)

        def _do_search():
            results = []
            for root, dirs, files in os.walk(safe_dir):
                for file in files:
                    if extension and not file.lower().endswith(extension):
                        continue
                    
                    # Search by filename
                    if query in file.lower():
                        rel_path = str(Path(os.path.join(root, file)).relative_to(file_service.workspace_root))
                        results.append({"path": rel_path, "match_type": "filename"})
                        continue
                        
                    # Basic text search for small files
                    if file.lower().endswith(('.txt', '.md', '.csv', '.json')):
                        try:
                            filepath = os.path.join(root, file)
                            if os.path.getsize(filepath) < 1024 * 1024: # max 1MB
                                with open(filepath, 'r', encoding='utf-8') as f:
                                    content = f.read().lower()
                                    if query in content:
                                        rel_path = str(Path(filepath).relative_to(file_service.workspace_root))
                                        results.append({"path": rel_path, "match_type": "content"})
                        except Exception:
                            pass
            return results
                        
        return await asyncio.to_thread(_do_search)
