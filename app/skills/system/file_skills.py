from typing import Any
from pathlib import Path
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.services.file_service import file_service

class FileListSkill(BaseSkill):
    name = "list_files"
    description = "Liệt kê danh sách file và thư mục trong một thư mục cho trước."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "list", "read", "directory"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "dir_path": {"type": "string", "description": "Đường dẫn thư mục (mặc định '.')", "default": "."}
        }
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return file_service.list_files(kwargs.get("dir_path", "."))

class FileCopySkill(BaseSkill):
    name = "copy_file"
    description = "Sao chép file từ nguồn tới đích."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "copy"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "Đường dẫn nguồn"},
            "dest": {"type": "string", "description": "Đường dẫn đích"}
        },
        "required": ["src", "dest"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return file_service.copy_file(kwargs["src"], kwargs["dest"])

class FileMoveSkill(BaseSkill):
    name = "move_file"
    description = "Di chuyển hoặc đổi tên file."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "move", "rename"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "Đường dẫn nguồn"},
            "dest": {"type": "string", "description": "Đường dẫn đích"}
        },
        "required": ["src", "dest"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return file_service.move_file(kwargs["src"], kwargs["dest"])

class FileDeleteSkill(BaseSkill):
    name = "delete_file"
    description = "Xóa file hoặc thư mục. YÊU CẦU XÁC NHẬN."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "delete", "remove"]
    operation_type = OperationType.DESTRUCTIVE
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn cần xóa"}
        },
        "required": ["path"]
    }
    output_schema = {"type": "boolean"}

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        safe_path = file_service.get_safe_path(kwargs["path"])
        return f"CẢNH BÁO: Bạn đang yêu cầu xóa vĩnh viễn '{safe_path}'. Hành động này không thể hoàn tác."

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return file_service.delete_file(kwargs["path"])

class FileCreateDirectorySkill(BaseSkill):
    name = "create_directory"
    description = "Tạo thư mục mới."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "mkdir", "directory", "create"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn thư mục cần tạo"}
        },
        "required": ["path"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return file_service.create_dir(kwargs["path"])

class CopyDirectorySkill(BaseSkill):
    name = "copy_directory"
    description = "Sao chép toàn bộ một thư mục và các thư mục con."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "directory", "copy", "bulk"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "dest": {"type": "string"}
        },
        "required": ["src", "dest"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return file_service.copy_dir(kwargs["src"], kwargs["dest"])

class MoveDirectorySkill(BaseSkill):
    name = "move_directory"
    description = "Di chuyển hoặc đổi tên toàn bộ thư mục."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "directory", "move", "rename"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "dest": {"type": "string"}
        },
        "required": ["src", "dest"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return file_service.move_dir(kwargs["src"], kwargs["dest"])

class SearchFilesSkill(BaseSkill):
    name = "search_files"
    description = "Tìm kiếm file theo tên, phần mở rộng, thư mục và ngày sửa đổi (mtime)."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "search", "find"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Chuỗi tìm kiếm trong tên file"},
            "extension": {"type": "string", "description": "Phần mở rộng, vd: .txt"},
            "directory": {"type": "string", "description": "Thư mục cần tìm kiếm (mặc định: '.')", "default": "."},
            "mtime_days": {"type": "integer", "description": "Tìm file được sửa đổi trong X ngày qua"}
        },
        "required": []
    }
    output_schema = {"type": "array"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        import os
        import time
        from pathlib import Path
        
        query = kwargs.get("query", "").lower()
        extension = kwargs.get("extension", "").lower()
        directory = kwargs.get("directory", ".")
        mtime_days = kwargs.get("mtime_days")
        
        safe_dir = file_service.get_safe_path(directory)
        results = []
        current_time = time.time()
        
        for root, dirs, files in os.walk(safe_dir):
            for file in files:
                if extension and not file.lower().endswith(extension):
                    continue
                if query and query not in file.lower():
                    continue
                    
                path = os.path.join(root, file)
                if mtime_days is not None:
                    try:
                        mtime = os.path.getmtime(path)
                        if (current_time - mtime) > (mtime_days * 86400):
                            continue
                    except:
                        pass
                        
                results.append(str(Path(path).relative_to(file_service.workspace_root)))
                
        return results

class RenameFileSkill(BaseSkill):
    name = "rename_file"
    description = "Đổi tên một file."
    category = SkillCategory.SYSTEM
    capabilities = ["file", "rename"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "new_name": {"type": "string", "description": "Tên mới (chỉ tên file, không bao gồm đường dẫn thư mục)"}
        },
        "required": ["src", "new_name"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        import os
        src = kwargs["src"]
        new_name = kwargs["new_name"]
        
        safe_src = file_service.get_safe_path(src)
        dir_name = os.path.dirname(safe_src)
        dest = os.path.join(dir_name, new_name)
        
        # We reuse move_file for atomic rename
        return file_service.move_file(src, dest)

