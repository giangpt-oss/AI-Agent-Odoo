import pytest
from app.workflows.models import Template, TemplateFormat
from app.workflows.template_render import template_renderer, TemplateError

def test_template_render_text():
    tpl = Template(
        name="Test",
        format=TemplateFormat.MD,
        schema_def={"name": {"type": "string", "required": True}},
        content="Hello {{name}}, welcome to {{project}}!",
        user_id="user1"
    )
    
    # Missing required field
    with pytest.raises(TemplateError):
        template_renderer.render(tpl, {"project": "Agent"})
        
    # Render successfully
    rendered = template_renderer.render(tpl, {"name": "Giang", "project": "Agent"})
    assert rendered == "Hello Giang, welcome to Agent!"
    
    # Render with spacing inside {{ }}
    tpl.content = "Hello {{ name }}, welcome to {{ project }}!"
    rendered2 = template_renderer.render(tpl, {"name": "Giang", "project": "Agent"})
    assert rendered2 == "Hello Giang, welcome to Agent!"

def test_template_isolation():
    from app.providers.workflows.sqlite import workflow_store
    
    tpl = Template(
        name="Private Tpl",
        format=TemplateFormat.MD,
        content="x",
        user_id="user_a",
        workspace_id="ws_1"
    )
    workflow_store.save_template(tpl)
    
    # Same user, same workspace
    assert len(workflow_store.list_templates("user_a", "ws_1")) > 0
    
    # Different user, different workspace
    assert len(workflow_store.list_templates("user_b", "ws_2")) == 0
