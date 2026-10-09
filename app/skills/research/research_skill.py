from typing import Any
import os
from pathlib import Path
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.services.file_service import file_service
from app.services.document_parser import document_parser

class ResearchSkill(BaseSkill):
    name = "research_topic"
    description = "Nghiên cứu một câu hỏi dựa trên các tài liệu trong hệ thống hoặc web (hiện tại hỗ trợ local documents)."
    category = SkillCategory.UTILITY
    capabilities = ["research", "search", "synthesize", "document"]
    operation_type = OperationType.EXTERNAL_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Câu hỏi cần nghiên cứu"},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "Danh sách file cần tham khảo (nếu có)"},
            "mode": {"type": "string", "enum": ["documents", "web", "mixed"], "default": "documents"}
        },
        "required": ["question"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        question = kwargs["question"]
        mode = kwargs.get("mode", "documents")
        sources = kwargs.get("sources", [])
        
        if mode in ["web", "mixed"]:
            return {"status": "INSUFFICIENT_SOURCES", "message": "Web provider is not currently implemented."}
            
        ai_client = context.providers.get("ai_client")
        if not ai_client:
            return {"status": "ERROR", "message": "AI client missing."}

        evidence = []
        
        # 1. Source Discovery & Retrieve Evidence
        if not sources:
            # If no sources provided, auto-discover some simple text/docx/pdf files in root
            # Note: A real vector DB is needed for scale, fallback to top 3 files here
            safe_dir = file_service.workspace_root
            for root, dirs, files in os.walk(safe_dir):
                for f in files[:5]: # Hard limit for P0
                    if f.lower().endswith(('.txt', '.md', '.docx', '.pdf')):
                        sources.append(str(Path(os.path.join(root, f)).relative_to(safe_dir)))
                break # Only root
                
        if not sources:
            return {"status": "INSUFFICIENT_SOURCES", "message": "No local sources found to research."}

        for source in sources:
            try:
                safe_path = file_service.get_safe_path(source)
                if os.path.exists(safe_path) and os.path.isfile(safe_path):
                    if os.path.getsize(safe_path) < 2 * 1024 * 1024: # max 2MB
                        parsed = document_parser.parse_file(safe_path, os.path.basename(safe_path))
                        content = parsed.get("text_content", "")
                        if content:
                            evidence.append(f"--- Nguồn: {source} ---\n{content[:5000]}")
            except Exception:
                pass
                
        if not evidence:
            return {"status": "INSUFFICIENT_SOURCES", "message": "Sources are unreadable or empty."}
            
        # 2. Synthesize & Cite
        combined_evidence = "\n\n".join(evidence)
        prompt = (
            f"Hãy trả lời câu hỏi sau dựa TRÊN CÁC TÀI LIỆU DƯỚI ĐÂY (không bịa đặt thêm).\n"
            f"Question: {question}\n\n"
            f"Documents:\n{combined_evidence}\n\n"
            f"Yêu cầu: Nếu tài liệu không đủ thông tin, hãy trả lời 'Tài liệu không đủ thông tin'. "
            f"Nếu có, hãy trả lời và trích dẫn nguồn (ví dụ: [nguồn: report.pdf])."
        )
        
        try:
            import asyncio
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model="gemini-3.5-flash-lite",
                contents=prompt
            )
            return {
                "status": "SUCCESS", 
                "answer": response.text, 
                "sources_used": sources
            }
        except Exception as e:
            return {"status": "ERROR", "message": f"LLM error: {e}"}
