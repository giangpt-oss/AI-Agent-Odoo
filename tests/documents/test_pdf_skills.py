import pytest
import os
import shutil
from pathlib import Path
from app.models.context import SkillExecutionContext, UserSessionContext
from app.skills.documents.pdf_skills import PdfExtractSkill, PdfRotateSkill
from app.services.file_service import file_service
from pypdf import PdfWriter

@pytest.fixture(scope="module")
def workspace():
    test_dir = Path("test_workspace_pdf")
    test_dir.mkdir(exist_ok=True)
    orig_ws = file_service.workspace_root
    file_service.workspace_root = test_dir.resolve()
    
    # Create dummy PDF
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    
    dummy_pdf = test_dir / "dummy.pdf"
    with open(dummy_pdf, "wb") as f:
        writer.write(f)
        
    yield test_dir
    file_service.workspace_root = orig_ws
    shutil.rmtree(test_dir, ignore_errors=True)

@pytest.fixture
def context():
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test"))

@pytest.mark.asyncio
async def test_pdf_extract(workspace, context):
    skill = PdfExtractSkill()
    res = await skill.execute(context, filepath="dummy.pdf", output_path="extracted.pdf", pages=[1, 3])
    
    # Check if exists
    extracted_path = workspace / "extracted.pdf"
    assert extracted_path.exists()
    
    from pypdf import PdfReader
    reader = PdfReader(extracted_path)
    assert len(reader.pages) == 2

@pytest.mark.asyncio
async def test_pdf_rotate(workspace, context):
    skill = PdfRotateSkill()
    res = await skill.execute(context, filepath="dummy.pdf", output_path="rotated.pdf", degrees=90, pages=[1])
    
    rotated_path = workspace / "rotated.pdf"
    assert rotated_path.exists()
