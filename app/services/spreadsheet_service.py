import pandas as pd
import openpyxl
from typing import Any, Dict, List
import io
import os
import shutil
from app.core.exceptions import SkillValidationError
from app.services.file_service import file_service

class SpreadsheetService:
    
    def _atomic_write(self, wb: openpyxl.Workbook, output_path: str):
        temp_path = f"{output_path}.tmp"
        try:
            wb.save(temp_path)
            os.replace(temp_path, output_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise SkillValidationError(f"Atomic write failed: {e}")

    def read_sheet(self, filepath: str, sheet_name: str = None, offset: int = 0, limit: int = 50, columns: List[str] = None) -> Dict[str, Any]:
        """Reads a spreadsheet file (CSV or Excel) with offset and limit."""
        safe_path = file_service.get_safe_path(filepath)
        
        ext = os.path.splitext(safe_path)[1].lower()
        if ext not in [".csv", ".xlsx", ".xls"]:
            raise SkillValidationError(f"Unsupported spreadsheet format: {ext}")
            
        try:
            if ext == ".csv":
                try:
                    df = pd.read_csv(safe_path, skiprows=range(1, offset + 1) if offset > 0 else None, nrows=limit, usecols=columns)
                except UnicodeDecodeError:
                    df = pd.read_csv(safe_path, skiprows=range(1, offset + 1) if offset > 0 else None, nrows=limit, usecols=columns, encoding="cp1258")
                
                data = df.fillna("").to_dict(orient="records")
                return {
                    "workbook": os.path.basename(safe_path),
                    "sheets": [{"name": "CSV Data", "rows": data, "dimensions": {"rows_read": len(df)}}]
                }
            else:
                wb = openpyxl.load_workbook(safe_path, data_only=False)
                sheet_names = wb.sheetnames
                target_sheet = sheet_name if sheet_name in sheet_names else sheet_names[0]
                ws = wb[target_sheet]
                all_rows = list(ws.iter_rows(values_only=True))
                
                if not all_rows:
                    data = []
                else:
                    headers = [str(c) if c is not None else f"Col_{i+1}" for i, c in enumerate(all_rows[0])]
                    raw_data_rows = all_rows[1:]
                    sliced_rows = raw_data_rows[offset : offset + limit] if limit is not None else raw_data_rows[offset:]
                    data = []
                    for r in sliced_rows:
                        row_dict = {}
                        for idx, h in enumerate(headers):
                            if columns and h not in columns:
                                continue
                            val = r[idx] if idx < len(r) else None
                            row_dict[h] = "" if val is None else val
                        data.append(row_dict)

                return {
                    "workbook": os.path.basename(safe_path),
                    "sheets": [{"name": target_sheet, "rows": data, "dimensions": {"rows_read": len(data)}}],
                    "metadata": {"note": "Formulas and values are preserved."}
                }
        except Exception as e:
            raise SkillValidationError(f"Lỗi đọc file spreadsheet: {str(e)}")

    def edit_sheet(self, filepath: str, operations: list[dict], create_backup: bool = False) -> str:
        """Edit spreadsheet using openpyxl."""
        safe_path = file_service.get_safe_path(filepath)
        ext = os.path.splitext(safe_path)[1].lower()
        if ext != ".xlsx":
            raise SkillValidationError("Chỉ hỗ trợ chỉnh sửa định dạng .xlsx qua openpyxl.")
            
        if create_backup:
            backup_path = safe_path + ".backup.xlsx"
            shutil.copy2(safe_path, backup_path)
            
        try:
            wb = openpyxl.load_workbook(safe_path)
            for op in operations:
                action = op.get("action")
                sheet_name = op.get("sheet")
                ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
                
                if action == "set_cell":
                    cell = op["cell"]
                    ws[cell].value = op["value"]
                elif action == "append_row":
                    ws.append(op["values"])
                elif action == "delete_rows":
                    ws.delete_rows(op["idx"], op.get("amount", 1))
                elif action == "insert_rows":
                    ws.insert_rows(op["idx"], op.get("amount", 1))
                elif action == "add_sheet":
                    wb.create_sheet(title=op["new_sheet_name"])
                elif action == "rename_sheet":
                    ws.title = op["new_sheet_name"]
                elif action == "delete_sheet":
                    if ws.title in wb.sheetnames:
                        del wb[ws.title]
                        
            self._atomic_write(wb, safe_path)
            return safe_path
        except Exception as e:
            raise SkillValidationError(f"Error editing spreadsheet: {e}")

    def analyze_data(self, filepath: str, sheet_name: str, column: str, operation: str) -> Any:
        safe_path = file_service.get_safe_path(filepath)
        ext = os.path.splitext(safe_path)[1].lower()
        
        try:
            if ext == ".csv":
                df = pd.read_csv(safe_path)
            else:
                df = pd.read_excel(safe_path, sheet_name=sheet_name if sheet_name else 0)
                
            if column not in df.columns:
                raise SkillValidationError(f"Cột {column} không tồn tại trong dữ liệu.")
                
            col_data = df[column]
            
            if operation == "sum":
                return float(col_data.sum())
            elif operation == "mean":
                return float(col_data.mean())
            elif operation == "min":
                return float(col_data.min())
            elif operation == "max":
                return float(col_data.max())
            elif operation == "count":
                return int(col_data.count())
            elif operation == "unique":
                return int(col_data.nunique())
            else:
                raise SkillValidationError(f"Phép toán {operation} chưa được hỗ trợ.")
        except Exception as e:
            raise SkillValidationError(f"Lỗi phân tích: {str(e)}")

spreadsheet_service = SpreadsheetService()
