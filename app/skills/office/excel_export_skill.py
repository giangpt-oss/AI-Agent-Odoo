import logging
from typing import Any
from pathlib import Path
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

logger = logging.getLogger(__name__)

class ExcelExportSkill(BaseSkill):
    name = "export_data_to_excel"
    description = "Tạo và xuất dữ liệu dạng bảng ra file Excel (.xlsx) chuẩn doanh nghiệp để gửi cho người dùng tải về."
    category = SkillCategory.SPREADSHEET
    capabilities = ["excel", "export", "report"]
    operation_type = OperationType.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Tiêu đề báo cáo hiển thị ở đầu bảng tính (ví dụ: 'Báo cáo cơ hội CRM', 'Danh sách nhân sự công ty')"},
            "headers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách tiêu đề các cột (ví dụ: ['STT', 'Tên cơ hội', 'Doanh thu (VNĐ)', 'Tỉ lệ chốt', 'Giai đoạn'])"
            },
            "rows": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {}
                },
                "description": "Mảng 2 chiều chứa các dòng dữ liệu tương ứng các cột"
            }
        },
        "required": ["title", "headers", "rows"]
    }
    output_schema = {"type": "object"}

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        title = kwargs.get("title", "Báo cáo")
        headers = kwargs.get("headers", [])
        rows = kwargs.get("rows", [])

        from app.services.excel_exporter import excel_exporter
        try:
            filepath = excel_exporter.create_excel_report(
                title=title,
                headers=headers,
                rows=rows,
                company_name="HOPITA ENTERPRISE - BÁO CÁO ĐIỀU HÀNH AI"
            )
            context.previous_outputs.setdefault("generated_excel_files", []).append(filepath)
            return {
                "status": "success",
                "filename": Path(filepath).name,
                "filepath": filepath,
                "total_rows": len(rows),
                "message": f"Đã xuất thành công file Excel '{Path(filepath).name}' gồm {len(rows)} dòng dữ liệu."
            }
        except Exception as e:
            logger.error(f"Lỗi khi xuất file Excel: {e}", exc_info=True)
            return {"status": "error", "message": f"Lỗi tạo file Excel: {str(e)}"}
