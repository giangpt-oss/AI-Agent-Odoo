import os
from pypdf import PdfReader, PdfWriter
from app.core.exceptions import SkillValidationError
from app.services.file_service import file_service

class PdfService:
    def read_pdf(self, filepath: str, page_number: int = None) -> dict:
        safe_path = file_service.get_safe_path(filepath)
        try:
            reader = PdfReader(safe_path)
            num_pages = len(reader.pages)
            meta = reader.metadata
            
            if page_number is not None:
                if page_number < 1 or page_number > num_pages:
                    raise SkillValidationError(f"Page {page_number} out of bounds (1-{num_pages})")
                pages = [reader.pages[page_number - 1]]
            else:
                pages = reader.pages
                
            extracted = "\n".join([p.extract_text() or "" for p in pages])
            
            # Giả lập check OCR_REQUIRED
            if not extracted.strip():
                return {"status": "OCR_REQUIRED", "message": "The PDF might be a scanned image.", "page_count": num_pages}
                
            return {
                "status": "success",
                "text": extracted,
                "page_count": num_pages,
                "metadata": dict(meta) if meta else {}
            }
        except Exception as e:
            raise SkillValidationError(f"Error reading PDF: {e}")

    def _atomic_write(self, writer: PdfWriter, output_path: str):
        temp_path = f"{output_path}.tmp"
        try:
            with open(temp_path, "wb") as f:
                writer.write(f)
            os.replace(temp_path, output_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise SkillValidationError(f"Atomic write failed: {e}")

    def merge_pdfs(self, filepaths: list, output_path: str) -> str:
        safe_out = file_service.get_safe_path(output_path)
        writer = PdfWriter()
        for fp in filepaths:
            safe_in = file_service.get_safe_path(fp)
            reader = PdfReader(safe_in)
            for page in reader.pages:
                writer.add_page(page)
                
        self._atomic_write(writer, safe_out)
        return safe_out

    def split_pdf(self, filepath: str, output_dir: str) -> list:
        safe_in = file_service.get_safe_path(filepath)
        safe_dir = file_service.get_safe_path(output_dir)
        os.makedirs(safe_dir, exist_ok=True)
        
        reader = PdfReader(safe_in)
        out_paths = []
        base = os.path.splitext(os.path.basename(safe_in))[0]
        
        for idx, page in enumerate(reader.pages):
            writer = PdfWriter()
            writer.add_page(page)
            out = os.path.join(safe_dir, f"{base}_page_{idx+1}.pdf")
            self._atomic_write(writer, out)
            out_paths.append(out)
        return out_paths

    def extract_pages(self, filepath: str, output_path: str, pages: list[int]) -> str:
        safe_in = file_service.get_safe_path(filepath)
        safe_out = file_service.get_safe_path(output_path)
        
        reader = PdfReader(safe_in)
        writer = PdfWriter()
        num_pages = len(reader.pages)
        
        for p in pages:
            if p < 1 or p > num_pages:
                raise SkillValidationError(f"Page {p} out of bounds (1-{num_pages})")
            writer.add_page(reader.pages[p - 1])
            
        self._atomic_write(writer, safe_out)
        return safe_out

    def rotate_pages(self, filepath: str, output_path: str, degrees: int, pages: list[int] = None) -> str:
        if degrees not in [90, 180, 270]:
            raise SkillValidationError("Degrees must be 90, 180, or 270")
            
        safe_in = file_service.get_safe_path(filepath)
        safe_out = file_service.get_safe_path(output_path)
        
        reader = PdfReader(safe_in)
        writer = PdfWriter()
        num_pages = len(reader.pages)
        
        if not pages:
            pages = list(range(1, num_pages + 1))
            
        for i, page in enumerate(reader.pages):
            if (i + 1) in pages:
                page.rotate(degrees)
            writer.add_page(page)
            
        self._atomic_write(writer, safe_out)
        return safe_out

pdf_service = PdfService()
