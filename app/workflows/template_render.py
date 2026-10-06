import re
from typing import Dict, Any
from app.workflows.models import Template, TemplateFormat
from app.services.file_service import file_service
import os

class TemplateError(Exception):
    pass

class TemplateRenderService:
    def validate_inputs(self, template: Template, inputs: Dict[str, Any]) -> None:
        missing = []
        for key, schema in template.schema_def.items():
            if schema.get("required", False) and key not in inputs:
                missing.append(key)
        if missing:
            raise TemplateError(f"TEMPLATE_INPUT_MISSING: {', '.join(missing)}")

    def render_text(self, content: str, inputs: Dict[str, Any]) -> str:
        rendered = content
        # simple replacement for {{var}}
        for key, val in inputs.items():
            pattern = re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}")
            rendered = pattern.sub(str(val), rendered)
        return rendered

    def render_docx(self, template: Template, inputs: Dict[str, Any]) -> str:
        # P2C requires using python-docx to preserve formatting instead of flattening
        # The template content points to a DOCX file in this case
        template_path = file_service.get_safe_path(template.content)
        if not os.path.exists(template_path):
            raise TemplateError("Template source file not found.")
            
        import docx
        doc = docx.Document(template_path)
        
        # Replace in paragraphs
        for p in doc.paragraphs:
            for key, val in inputs.items():
                placeholder = f"{{{{{key}}}}}"
                if placeholder in p.text:
                    p.text = p.text.replace(placeholder, str(val))
                    
        # Replace in tables
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for key, val in inputs.items():
                            placeholder = f"{{{{{key}}}}}"
                            if placeholder in p.text:
                                p.text = p.text.replace(placeholder, str(val))
                                
        # Save output
        import uuid
        out_filename = f"Rendered_{template.name}_{uuid.uuid4().hex[:6]}.docx"
        out_dir = file_service.get_safe_path("artifacts")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, out_filename)
        doc.save(out_path)
        return out_path

    def render(self, template: Template, inputs: Dict[str, Any]) -> str:
        self.validate_inputs(template, inputs)
        
        if template.format == TemplateFormat.DOCX:
            return self.render_docx(template, inputs)
        else:
            return self.render_text(template.content, inputs)

template_renderer = TemplateRenderService()
