from typing import Any
import asyncio
from app.skills.base import BaseSkill, SkillCategory, OperationType, RiskLevel
from app.models.context import SkillExecutionContext

from app.knowledge.indexing import indexing_service
from app.knowledge.retrieval import search_service
from app.knowledge.rag import rag_service
from app.knowledge.store import metadata_store
from app.providers.vectorstore.chroma import default_vector_store

class KnowledgeIndexSkill(BaseSkill):
    name = "knowledge_index"
    description = "Lập chỉ mục (index) các file tài liệu vào Knowledge Base để tìm kiếm sau này. Hỗ trợ PDF, DOCX, TXT, MD, XLSX, CSV."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.WRITE
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["WRITE_FILES"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách đường dẫn các file cần index"
            }
        },
        "required": ["paths"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        paths = kwargs["paths"]
        # Trigger persistent background indexing
        import uuid
        from app.knowledge.models import IndexingJob
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        owner_id = context.session.user_id
        job = IndexingJob(
            job_id=f"job_{uuid.uuid4().hex[:8]}",
            type="INDEX",
            status="PENDING",
            payload={"paths": paths, "workspace_id": workspace_id, "owner_id": owner_id}
        )
        await asyncio.to_thread(metadata_store.update_job, job)
        
        # Enqueue job vào worker có cơ chế tự phục hồi sau restart
        await indexing_service.enqueue_indexing_job(
            job.job_id, 
            paths, 
            workspace_id,
            owner_id
        )
        return {"status": "SUCCESS", "message": f"Đã bắt đầu lập chỉ mục {len(paths)} tài liệu chạy ngầm.", "job_id": job.job_id}

class SemanticSearchSkill(BaseSkill):
    name = "semantic_search"
    description = "Tìm kiếm ngữ nghĩa (Semantic Search) trên kho tài liệu đã index. Dùng khi cần tìm tài liệu chứa nội dung/ý nghĩa liên quan."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.READ
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["READ_FILES"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Câu hỏi hoặc từ khóa tìm kiếm"},
            "top_k": {"type": "integer", "description": "Số kết quả trả về (mặc định 5)"}
        },
        "required": ["query"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        query = kwargs["query"]
        top_k = kwargs.get("top_k", 5)
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        
        results = await search_service.search(query, workspace_id, top_k)
        return {
            "status": "SUCCESS",
            "results": [
                {
                    "title": r.title,
                    "content": r.content,
                    "score": r.score,
                    "source": r.source
                } for r in results
            ]
        }

class KnowledgeAnswerSkill(BaseSkill):
    name = "knowledge_answer"
    description = "Trả lời câu hỏi dựa trên Kho Tri Thức (RAG). BẮT BUỘC dùng khi người dùng hỏi về kiến thức, quy định, dữ liệu đã lưu trong file của họ."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.READ
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["READ_FILES"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Câu hỏi của người dùng"}
        },
        "required": ["question"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        question = kwargs["question"]
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        ai_client = context.providers.get("ai_client")
        
        answer, results = await rag_service.get_answer(question, workspace_id, ai_client)
        
        if answer == "INSUFFICIENT_EVIDENCE":
            return {
                "status": "INSUFFICIENT_EVIDENCE", 
                "message": "Không tìm thấy thông tin phù hợp trong Kho Tri Thức để trả lời câu hỏi này."
            }
            
        return {
            "status": "SUCCESS",
            "answer": answer,
            "evidence": [
                {
                    "title": r.title,
                    "source": r.source
                } for r in results
            ]
        }

class KnowledgeRemoveSkill(BaseSkill):
    name = "knowledge_remove"
    description = "Xóa một tài liệu khỏi Knowledge Base (chỉ xóa index, không xóa file gốc)."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.DESTRUCTIVE
    risk_level = RiskLevel.MEDIUM
    requires_confirmation = True
    capabilities = ["WRITE_FILES"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "source_id": {"type": "string"}
        },
        "required": ["source_id"]
    }
    output_schema = {"type": "object"}
    
    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        return {"warning": "Original file will NOT be deleted. Only search index will be removed.", "source_id": kwargs["source_id"]}
        
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        source_id = kwargs["source_id"]
        # Delete from Vector Store & Metadata in thread pool
        await asyncio.to_thread(default_vector_store.delete_source, source_id)
        await asyncio.to_thread(metadata_store.delete_source, source_id)
        return {"status": "SUCCESS", "message": "Đã xóa khỏi Knowledge Base."}

class KnowledgeListSourcesSkill(BaseSkill):
    name = "knowledge_list_sources"
    description = "Liệt kê các tài liệu đã được index trong Knowledge Base."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.READ
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["READ_FILES"]
    
    input_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        sources = await asyncio.to_thread(metadata_store.list_sources, workspace_id)
        return {
            "status": "SUCCESS", 
            "sources": [{"id": s.source_id, "title": s.title, "state": s.state} for s in sources]
        }
