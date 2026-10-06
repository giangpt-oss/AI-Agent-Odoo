from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType, RiskLevel
from app.models.context import SkillExecutionContext

from app.workflows.models import Template, TemplateFormat
from app.providers.workflows.sqlite import workflow_store

class TemplateCreateSkill(BaseSkill):
    name = "template_create"
    description = "Tạo một template tài liệu hoặc báo cáo mới."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.WRITE
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["WRITE_TEMPLATES"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "template_type": {"type": "string"},
            "format": {"type": "string", "enum": [TemplateFormat.DOCX, TemplateFormat.MD, TemplateFormat.EMAIL, TemplateFormat.NOTE]},
            "schema_def": {"type": "object"},
            "content": {"type": "string", "description": "Markdown text or path to DOCX file"}
        },
        "required": ["name", "format", "content"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        
        tpl = Template(
            name=kwargs["name"],
            description=kwargs.get("description", ""),
            template_type=kwargs.get("template_type", "DOCUMENT"),
            format=kwargs["format"],
            schema_def=kwargs.get("schema_def", {}),
            content=kwargs["content"],
            user_id=context.session.user_id,
            workspace_id=workspace_id
        )
        
        workflow_store.save_template(tpl)
        return {"status": "SUCCESS", "message": "Đã tạo template.", "template_id": tpl.template_id}

class TemplateListSkill(BaseSkill):
    name = "template_list"
    description = "Danh sách các template hiện có."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.READ
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["READ_TEMPLATES"]
    
    input_schema = {"type": "object", "properties": {}}
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        workspace_id = context.session.workspace_id if hasattr(context.session, 'workspace_id') else "default"
        templates = workflow_store.list_templates(context.session.user_id, workspace_id)
        return {
            "status": "SUCCESS",
            "templates": [{"id": t.template_id, "name": t.name, "format": t.format} for t in templates]
        }

from app.workflows.template_render import template_renderer

class TemplateRenderSkill(BaseSkill):
    name = "template_render"
    description = "Render một template với dữ liệu đầu vào."
    category = SkillCategory.SYSTEM
    operation_type = OperationType.READ
    risk_level = RiskLevel.LOW
    requires_confirmation = False
    capabilities = ["READ_TEMPLATES"]
    
    input_schema = {
        "type": "object",
        "properties": {
            "template_id": {"type": "string"},
            "inputs": {"type": "object"}
        },
        "required": ["template_id", "inputs"]
    }
    output_schema = {"type": "object"}
    
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        tpl = workflow_store.get_template(kwargs["template_id"])
        if not tpl:
            return {"status": "ERROR", "message": "Template không tồn tại."}
            
        try:
            rendered = template_renderer.render(tpl, kwargs["inputs"])
            return {"status": "SUCCESS", "rendered": rendered}
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}
