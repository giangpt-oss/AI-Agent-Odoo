import pytest
import os
import shutil
from pathlib import Path
from app.models.context import SkillExecutionContext, UserSessionContext
from app.core.exceptions import SkillPermissionError
from app.services.file_service import file_service
from app.skills.system.file_skills import FileListSkill

@pytest.fixture(scope="module")
def workspace():
    test_dir = Path("test_workspace_sec")
    test_dir.mkdir(exist_ok=True)
    orig_ws = file_service.workspace_root
    file_service.workspace_root = test_dir.resolve()
    yield test_dir
    file_service.workspace_root = orig_ws
    shutil.rmtree(test_dir, ignore_errors=True)

@pytest.fixture
def context():
    return SkillExecutionContext(session=UserSessionContext(user_id="test", user_name="Test"))

@pytest.mark.asyncio
async def test_security_path_traversal(workspace, context):
    skill = FileListSkill()
    with pytest.raises(SkillPermissionError, match="ngoài workspace|bị từ chối"):
        await skill.execute(context, dir_path="../")
        
    res = await skill.execute(context, dir_path=".")
    assert isinstance(res, list)
