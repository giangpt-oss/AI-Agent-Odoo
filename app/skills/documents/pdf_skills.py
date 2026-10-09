import asyncio
from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.services.pdf_service import pdf_service

class PdfReadSkill(BaseSkill):
    name = "read_pdf"
    description = "Đọc metadata, tổng số trang và văn bản từ file PDF (toàn bộ hoặc theo trang cụ thể)."
    category = SkillCategory.DOCUMENT
    capabilities = ["pdf", "read"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "page_number": {"type": "integer"}
        },
        "required": ["filepath"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return await asyncio.to_thread(pdf_service.read_pdf, kwargs["filepath"], kwargs.get("page_number"))

class PdfMergeSkill(BaseSkill):
    name = "merge_pdfs"
    description = "Gộp nhiều file PDF thành một."
    category = SkillCategory.DOCUMENT
    capabilities = ["pdf", "merge", "combine"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepaths": {"type": "array", "items": {"type": "string"}},
            "output_path": {"type": "string"}
        },
        "required": ["filepaths", "output_path"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return await asyncio.to_thread(pdf_service.merge_pdfs, kwargs["filepaths"], kwargs["output_path"])

class PdfSplitSkill(BaseSkill):
    name = "split_pdf"
    description = "Tách file PDF thành các trang rời."
    category = SkillCategory.DOCUMENT
    capabilities = ["pdf", "split"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "output_dir": {"type": "string"}
        },
        "required": ["filepath", "output_dir"]
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return await asyncio.to_thread(pdf_service.split_pdf, kwargs["filepath"], kwargs["output_dir"])

class PdfExtractSkill(BaseSkill):
    name = "extract_pdf_pages"
    description = "Trích xuất các trang cụ thể từ PDF thành file mới."
    category = SkillCategory.DOCUMENT
    capabilities = ["pdf", "extract"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "output_path": {"type": "string"},
            "pages": {"type": "array", "items": {"type": "integer"}}
        },
        "required": ["filepath", "output_path", "pages"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return await asyncio.to_thread(pdf_service.extract_pages, kwargs["filepath"], kwargs["output_path"], kwargs["pages"])

class PdfRotateSkill(BaseSkill):
    name = "rotate_pdf_pages"
    description = "Xoay trang PDF (90, 180, 270 độ)."
    category = SkillCategory.DOCUMENT
    capabilities = ["pdf", "rotate"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "output_path": {"type": "string"},
            "degrees": {"type": "integer", "enum": [90, 180, 270]},
            "pages": {"type": "array", "items": {"type": "integer"}}
        },
        "required": ["filepath", "output_path", "degrees"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return await asyncio.to_thread(pdf_service.rotate_pages, kwargs["filepath"], kwargs["output_path"], kwargs["degrees"], kwargs.get("pages"))
