import os
import shutil
from pathlib import Path
from typing import List, Dict, Any
from app.core.exceptions import SkillValidationError, SkillPermissionError
from app.core.config import get_settings

class FileService:
    def __init__(self):
        self.workspace_root = Path(os.getcwd()).resolve()

    def get_safe_path(self, file_path: str) -> str:
        """Kiểm tra và chuẩn hóa đường dẫn, ngăn chặn path traversal."""
        path = (self.workspace_root / file_path).resolve()
        if not path.is_relative_to(self.workspace_root.resolve()):
            raise SkillPermissionError(f"Truy cập bị từ chối: Đường dẫn {file_path} nằm ngoài workspace.")
        relative = path.relative_to(self.workspace_root.resolve())
        if any(part in {'.git', '.venv', '.agent_data', '__pycache__'} or part.startswith('.env') for part in relative.parts):
            raise SkillPermissionError("Truy cập bị từ chối: tệp cấu hình hoặc dữ liệu nội bộ.")
        return str(path)
        
    def list_files(self, dir_path: str = ".") -> List[Dict[str, Any]]:
        safe_dir = self.get_safe_path(dir_path)
        if not os.path.exists(safe_dir) or not os.path.isdir(safe_dir):
            raise SkillValidationError(f"Thư mục không tồn tại: {dir_path}")
            
        results = []
        for entry in os.scandir(safe_dir):
            results.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
                "path": str(Path(entry.path).relative_to(self.workspace_root))
            })
        return results

    def copy_file(self, src: str, dest: str) -> str:
        safe_src = self.get_safe_path(src)
        safe_dest = self.get_safe_path(dest)
        if not os.path.exists(safe_src):
            raise SkillValidationError(f"Nguồn không tồn tại: {src}")
        shutil.copy2(safe_src, safe_dest)
        return safe_dest

    def move_file(self, src: str, dest: str) -> str:
        safe_src = self.get_safe_path(src)
        safe_dest = self.get_safe_path(dest)
        if not os.path.exists(safe_src):
            raise SkillValidationError(f"Nguồn không tồn tại: {src}")
        shutil.move(safe_src, safe_dest)
        return safe_dest

    def delete_file(self, path: str) -> bool:
        safe_path = self.get_safe_path(path)
        if Path(safe_path) == self.workspace_root.resolve():
            raise SkillPermissionError("Không được xóa thư mục workspace gốc.")
        if not os.path.exists(safe_path):
            raise SkillValidationError(f"File/Thư mục không tồn tại: {path}")
        if os.path.isdir(safe_path):
            shutil.rmtree(safe_path)
        else:
            os.remove(safe_path)
        return True

    def create_dir(self, path: str) -> str:
        safe_path = self.get_safe_path(path)
        os.makedirs(safe_path, exist_ok=True)
        return safe_path
        
    def copy_dir(self, src: str, dest: str) -> str:
        safe_src = self.get_safe_path(src)
        safe_dest = self.get_safe_path(dest)
        if not os.path.exists(safe_src) or not os.path.isdir(safe_src):
            raise SkillValidationError(f"Thư mục nguồn không tồn tại: {src}")
        if os.path.exists(safe_dest):
            raise SkillValidationError(f"Thư mục đích đã tồn tại: {dest}")
        shutil.copytree(safe_src, safe_dest)
        return safe_dest
        
    def move_dir(self, src: str, dest: str) -> str:
        safe_src = self.get_safe_path(src)
        safe_dest = self.get_safe_path(dest)
        if not os.path.exists(safe_src) or not os.path.isdir(safe_src):
            raise SkillValidationError(f"Thư mục nguồn không tồn tại: {src}")
        if os.path.exists(safe_dest):
            raise SkillValidationError(f"Thư mục đích đã tồn tại: {dest}")
        shutil.move(safe_src, safe_dest)
        return safe_dest

file_service = FileService()
