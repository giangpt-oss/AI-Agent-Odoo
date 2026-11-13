"""On-demand retrieval from Drive. Downloaded content lives only for the current question."""
import asyncio
import base64
import io
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import BadZipFile, ZipFile

from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError
from openpyxl.utils.exceptions import InvalidFileException
from pypdf.errors import PyPdfError

from app.knowledge.indexing import indexing_service
from app.knowledge.models import SearchResult, SourceType
from app.knowledge.rag import rag_service
from app.knowledge.store import sparse_terms
from app.providers.gemini_retry import GeminiRequestError
from app.services.gateway_client import gateway_client
from app.services.turn_trace import measure_stage

FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
KINDS = {".pdf": SourceType.PDF, ".docx": SourceType.DOCX, ".xlsx": SourceType.SPREADSHEET,
         ".csv": SourceType.SPREADSHEET, ".txt": SourceType.DOCUMENT, ".md": SourceType.DOCUMENT}
STOPWORDS = set("cho tôi hỏi vui lòng hãy giúp mình biết là gì như thế nào về của trong có được không và theo từ ra ở các một những để với này đó tôi muốn đọc tệp tài liệu file drive google the a an what is how to of in for".split())


def drive_reference(question):
    for link in re.findall(r"https?://[^\s<>]+", question):
        try:
            parsed = urlsplit(link.rstrip(").,;]"))
        except ValueError:
            continue
        if parsed.hostname not in {"drive.google.com", "docs.google.com"}:
            continue
        parts = parsed.path.split("/")
        if "folders" in parts:
            return None, True
        candidates = parse_qs(parsed.query).get("id", [])
        if "d" in parts and parts.index("d") + 1 < len(parts):
            candidates.insert(0, parts[parts.index("d") + 1])
        return next((value for value in candidates if FILE_ID.fullmatch(value)), None), True
    return None, False


def extract_evidence(files, question, *, direct=False):
    query_terms = set(sparse_terms(question))
    query_terms -= set(sparse_terms(" ".join(STOPWORDS)))
    evidence, failed = [], 0
    with tempfile.TemporaryDirectory(prefix="agent-drive-") as directory:
        for file in files:
            suffix, key = file.get("suffix"), file.get("id", "")
            if suffix not in KINDS or not FILE_ID.fullmatch(key):
                failed += 1
                continue
            try:
                raw = base64.b64decode(file["content_base64"], validate=True)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("File too large")
                if suffix in {".docx", ".xlsx"}:
                    with ZipFile(io.BytesIO(raw)) as archive:
                        if sum(entry.file_size for entry in archive.infolist()) > 40 * 1024 * 1024:
                            raise ValueError("Expanded document too large")
                path = Path(directory) / (key + suffix)
                path.write_bytes(raw)
                chunks = indexing_service._sync_parse_and_chunk(str(path), KINDS[suffix], key)
            except (ValueError, OSError, KeyError, BadZipFile, PyPdfError,
                    PackageNotFoundError, InvalidFileException, XMLSyntaxError):
                failed += 1
                continue
            if not chunks:
                failed += 1
            title = " ".join(str(file.get("name") or "Tệp Drive").split())[:180]
            title_terms = set(sparse_terms(title))
            for chunk in chunks:
                words = set(sparse_terms(chunk.text))
                score = sum(3 if term.startswith("id") else 1 for term in query_terms & words)
                score += len(query_terms & title_terms)
                if score or direct:
                    evidence.append(SearchResult(chunk_id=chunk.chunk_id, source_id=key, title=title,
                        content=chunk.text, score=score, source={"page": chunk.page, "sheet": chunk.sheet,
                            "section": chunk.section, "url": f"https://drive.google.com/open?id={key}"}))
    evidence.sort(key=lambda item: -item.score)
    # Give each matching document a chance before filling the remaining context.
    first, remaining, seen = [], [], set()
    for item in evidence:
        (remaining if item.source_id in seen else first).append(item)
        seen.add(item.source_id)
    return (first + remaining)[:8], failed


async def search_drive(question, context):
    file_id, has_link = drive_reference(question)
    if has_link and not file_id:
        return {"status": "FILE_LINK_REQUIRED"}, []
    clean_question = re.sub(r"https?://[^\s<>]+", "", question).strip()
    words = re.findall(r"[^\W_]+(?:[-./_][^\W_]+)*", clean_question.casefold())
    terms = list(dict.fromkeys(word for word in words if len(word) >= 2 and word not in STOPWORDS))[:8]
    response = await gateway_client.drive_knowledge(context, terms, file_id)
    if response.get("status") != "OK":
        return response, []
    with measure_stage("internal_retrieval_ms"):
        evidence, failed = await asyncio.to_thread(extract_evidence, response.get("files", []), clean_question,
                                                   direct=bool(file_id) or not terms)
    return {"status": "OK", "skipped": response.get("skipped", 0) + failed}, evidence


async def answer_from_drive(question, context):
    if not question:
        return ["Bạn muốn tra cứu nội dung gì trên Google Drive?"]
    status, evidence = await search_drive(question, context)
    code = status.get("status")
    if code != "OK":
        messages = {
            "FILE_LINK_REQUIRED": "Hãy gửi link một tệp cụ thể hoặc đặt câu hỏi để tôi tìm trên Google Drive.",
            "NOT_CONNECTED": "Tài khoản chưa kết nối Google. Hãy mở Portal để liên kết Drive.",
            "RECONNECT_REQUIRED": "Kết nối Google hoặc Telegram cần được xác nhận lại trên Portal.",
            "PERMISSION_DENIED": "Tài khoản chưa có quyền đọc nội dung này trên Google Drive.",
            "NOT_FOUND": "Không tìm thấy tệp trong phạm vi tài khoản Google đã kết nối.",
            "RATE_LIMIT": "Google Drive đang giới hạn lượt đọc. Hãy thử lại sau.",
        }
        return [messages.get(code, "Tạm thời không thể tra cứu Google Drive.")]
    if not evidence:
        if status.get("skipped"):
            return ["Chưa lấy được nội dung phù hợp. Một số tệp bị giới hạn quyền tải, dung lượng hoặc định dạng. Hãy thử gửi link tệp khác."]
        return ["Chưa tìm được tài liệu Drive có nội dung phù hợp. Bạn có thể gửi tên hoặc link tệp để tôi đọc trực tiếp."]
    prompt = re.sub(r"https?://[^\s<>]+", "", question).strip() or "Tóm tắt nội dung tài liệu."
    try:
        with measure_stage("llm_synthesis_ms"):
            answer, results = await rag_service.answer_from_evidence(prompt, evidence)
    except GeminiRequestError as exc:
        if exc.category == "rate_limit":
            return ["Đã đọc được tài liệu Drive. Gemini đang giới hạn lượt trả lời, bạn hãy thử lại sau."]
        return ["Đã đọc được tài liệu Drive. Dịch vụ tạo câu trả lời đang gặp lỗi."]
    if answer == "INSUFFICIENT_EVIDENCE":
        return ["Các tài liệu Drive tìm được chưa đủ bằng chứng để trả lời câu hỏi này."]
    citations = dict.fromkeys(int(number) for number in re.findall(r"\[Nguồn\s*\[(\d+)\]", answer, re.IGNORECASE))
    links = [f"[{index}] {results[index - 1].title} – {results[index - 1].source['url']}" for index in citations]
    return [answer + "\n\nTệp nguồn trên Google Drive\n" + "\n".join(links)]
