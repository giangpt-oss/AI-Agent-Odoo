import os
import pytest
from pathlib import Path
from app.services.file_service import file_service
from app.core.exceptions import SkillPermissionError

def test_symlink_escape(tmp_path):
    # Setup a fake workspace
    file_service.workspace_root = tmp_path / "workspace"
    file_service.workspace_root.mkdir()
    
    # Create an outside file
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret")
    
    # Create a symlink inside workspace pointing to outside
    symlink_path = file_service.workspace_root / "link.txt"
    try:
        os.symlink(outside_file, symlink_path)
    except OSError:
        # Windows requires admin for symlinks or developer mode. Skip if we can't create it.
        pytest.skip("Cannot create symlinks on this Windows setup")
        
    with pytest.raises(SkillPermissionError):
        file_service.get_safe_path("link.txt")
