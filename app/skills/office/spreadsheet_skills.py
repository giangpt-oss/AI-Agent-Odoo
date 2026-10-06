from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext
from app.services.spreadsheet_service import spreadsheet_service

class SpreadsheetReadSkill(BaseSkill):
    name = "read_spreadsheet"
    description = "Đọc dữ liệu từ file Excel hoặc CSV (có giới hạn số dòng để tránh quá tải)."
    category = SkillCategory.SPREADSHEET
    capabilities = ["spreadsheet", "excel", "csv", "read"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string", "description": "Đường dẫn file spreadsheet"},
            "sheet_name": {"type": "string", "description": "Tên sheet (nếu có)"},
            "offset": {"type": "integer", "description": "Bắt đầu từ dòng nào (0-indexed)", "default": 0},
            "limit": {"type": "integer", "description": "Số dòng tối đa", "default": 50},
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Chỉ lấy các cột này"}
        },
        "required": ["filepath"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return spreadsheet_service.read_sheet(
            filepath=kwargs["filepath"],
            sheet_name=kwargs.get("sheet_name"),
            offset=kwargs.get("offset", 0),
            limit=kwargs.get("limit", 50),
            columns=kwargs.get("columns")
        )

class SpreadsheetAnalysisSkill(BaseSkill):
    name = "analyze_spreadsheet"
    description = "Thực hiện phép toán phân tích dữ liệu trên file (sum, mean, max, min, count) mà không tải toàn bộ vào bộ nhớ LLM."
    category = SkillCategory.SPREADSHEET
    capabilities = ["spreadsheet", "analyze", "math", "calculate"]
    operation_type = OperationType.READ
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "sheet_name": {"type": "string"},
            "column": {"type": "string", "description": "Tên cột cần phân tích"},
            "operation": {
                "type": "string", 
                "enum": ["sum", "mean", "min", "max", "count", "unique"],
                "description": "Phép toán"
            }
        },
        "required": ["filepath", "column", "operation"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        res = spreadsheet_service.analyze_data(
            filepath=kwargs["filepath"],
            sheet_name=kwargs.get("sheet_name"),
            column=kwargs["column"],
            operation=kwargs["operation"]
        )
        return {"operation": kwargs["operation"], "column": kwargs["column"], "result": res}

class SpreadsheetEditSkill(BaseSkill):
    name = "edit_spreadsheet"
    description = "Chỉnh sửa file Excel (.xlsx). Các thao tác hỗ trợ: set_cell, append_row, delete_rows, insert_rows, add_sheet, rename_sheet, delete_sheet."
    category = SkillCategory.SPREADSHEET
    capabilities = ["spreadsheet", "excel", "edit", "write"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "filepath": {"type": "string"},
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["set_cell", "append_row", "delete_rows", "insert_rows", "add_sheet", "rename_sheet", "delete_sheet"]},
                        "sheet": {"type": "string"},
                        "cell": {"type": "string", "description": "Ví dụ: 'A1' (chỉ dùng cho set_cell)"},
                        "value": {"type": ["string", "number", "boolean"]},
                        "values": {"type": "array", "description": "Dùng cho append_row"},
                        "idx": {"type": "integer", "description": "Vị trí hàng (dùng cho delete_rows, insert_rows)"},
                        "amount": {"type": "integer", "description": "Số lượng hàng"},
                        "new_sheet_name": {"type": "string"}
                    },
                    "required": ["action"]
                }
            },
            "create_backup": {"type": "boolean", "default": False}
        },
        "required": ["filepath", "operations"]
    }
    output_schema = {"type": "string"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        return spreadsheet_service.edit_sheet(
            kwargs["filepath"],
            kwargs["operations"],
            kwargs.get("create_backup", False)
        )
