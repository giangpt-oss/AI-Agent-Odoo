import io
import logging
from pathlib import Path
from typing import Any
import pandas as pd
from pypdf import PdfReader
from docx import Document

logger = logging.getLogger(__name__)


class DocumentParser:
    """Xử lý và trích xuất nội dung văn bản & bảng tính từ file người dùng gửi."""

    @staticmethod
    def parse_file(file_path: str, filename: str) -> dict[str, Any]:
        """Đọc và trích xuất dữ liệu từ file theo định dạng.
        Trả về dict gồm:
        - file_type: 'excel' | 'pdf' | 'word' | 'image' | 'text' | 'unknown'
        - summary: Tóm tắt cấu trúc hoặc nội dung
        - text_content: Nội dung chữ để nạp vào prompt cho AI
        - raw_bytes: Dữ liệu nhị phân (dành cho ảnh)
        """
        path = Path(file_path)
        ext = path.suffix.lower()
        res = {
            "filename": filename,
            "ext": ext,
            "file_type": "unknown",
            "text_content": "",
            "summary": "",
            "raw_bytes": None,
            "mime_type": "",
        }

        try:
            # 1. FILE BẢNG TÍNH EXCEL (.xlsx, .xls)
            if ext in [".xlsx", ".xls"]:
                res["file_type"] = "excel"
                xl = pd.ExcelFile(file_path)
                sheet_names = xl.sheet_names
                sheets_data = []

                for sheet in sheet_names[:5]:  # Đọc tối đa 5 sheet
                    df = pd.read_excel(file_path, sheet_name=sheet)
                    num_rows, num_cols = df.shape
                    # Lấy mẫu tối đa 50 dòng để không làm tràn context
                    preview_df = df.head(50)
                    csv_preview = preview_df.to_csv(index=False)
                    sheets_data.append(
                        f"--- Sheet: '{sheet}' ({num_rows} dòng x {num_cols} cột) ---\n"
                        f"Các cột: {', '.join(str(c) for c in df.columns)}\n"
                        f"Dữ liệu mẫu:\n{csv_preview}\n"
                    )

                res["summary"] = f"File Excel '{filename}' gồm các sheet: {', '.join(sheet_names)}"
                res["text_content"] = "\n".join(sheets_data)
                return res

            # 2. FILE CSV (.csv)
            elif ext == ".csv":
                res["file_type"] = "csv"
                try:
                    df = pd.read_csv(file_path, encoding="utf-8")
                except UnicodeDecodeError:
                    df = pd.read_csv(file_path, encoding="cp1258")

                num_rows, num_cols = df.shape
                preview_df = df.head(50)
                res["summary"] = f"File CSV '{filename}' có {num_rows} dòng x {num_cols} cột"
                res["text_content"] = (
                    f"Cấu trúc bảng CSV ({num_rows} dòng x {num_cols} cột):\n"
                    f"Cột: {', '.join(str(c) for c in df.columns)}\n\n"
                    f"Dữ liệu:\n{preview_df.to_csv(index=False)}"
                )
                return res

            # 3. FILE PDF (.pdf)
            elif ext == ".pdf":
                res["file_type"] = "pdf"
                reader = PdfReader(file_path)
                pages_text = []
                total_pages = len(reader.pages)

                for idx, page in enumerate(reader.pages[:20], 1):  # Đọc tối đa 20 trang đầu
                    extracted = page.extract_text() or ""
                    if extracted.strip():
                        pages_text.append(f"--- Trang {idx}/{total_pages} ---\n{extracted.strip()}")

                res["summary"] = f"File PDF '{filename}' gồm {total_pages} trang"
                res["text_content"] = "\n\n".join(pages_text) if pages_text else "(Không trích xuất được văn bản, file PDF có thể là dạng ảnh scan)"
                return res

            # 4. FILE WORD (.docx)
            elif ext in [".docx", ".doc"]:
                res["file_type"] = "word"
                doc = Document(file_path)
                paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
                tables_data = []

                for t_idx, table in enumerate(doc.tables[:5], 1):
                    t_rows = []
                    for row in table.rows:
                        t_rows.append([cell.text.strip() for cell in row.cells])
                    if t_rows:
                        tables_data.append(f"Bảng {t_idx}:\n" + "\n".join(" | ".join(r) for r in t_rows[:20]))

                full_text = "\n\n".join(paragraphs)
                if tables_data:
                    full_text += "\n\n--- Dữ liệu các bảng trong tài liệu ---\n" + "\n\n".join(tables_data)

                res["summary"] = f"Tài liệu Word '{filename}' có {len(paragraphs)} đoạn văn"
                res["text_content"] = full_text
                return res

            # 5. FILE HÌNH ẢNH (.jpg, .jpeg, .png)
            elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
                res["file_type"] = "image"
                mime = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"
                with open(file_path, "rb") as f:
                    raw = f.read()
                res["raw_bytes"] = raw
                res["mime_type"] = mime
                res["summary"] = f"File hình ảnh '{filename}' ({len(raw)} bytes)"
                return res

            # 6. FILE VĂN BẢN THUẦN (.txt, .json, .md)
            elif ext in [".txt", ".json", ".md", ".log"]:
                res["file_type"] = "text"
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(50000)  # Đọc tối đa 50k ký tự
                res["summary"] = f"File văn bản '{filename}' ({len(content)} ký tự)"
                res["text_content"] = content
                return res

        except Exception as e:
            logger.error(f"Lỗi khi đọc file {filename}: {e}", exc_info=True)
            res["summary"] = f"Lỗi đọc file: {str(e)}"
            res["text_content"] = f"(Lỗi trích xuất: {str(e)})"

        return res


document_parser = DocumentParser()
