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

from app.core.config import get_settings
from app.knowledge.indexing import indexing_service
from app.knowledge.models import SearchResult, SourceType
from app.knowledge.rag import rag_service
from app.knowledge.store import sparse_terms
from app.providers.gemini_retry import GeminiRequestError
from app.services.gateway_client import GatewayServiceError, gateway_client
from app.services.turn_trace import measure_stage

FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
KINDS = {".pdf": SourceType.PDF, ".docx": SourceType.DOCX, ".xlsx": SourceType.SPREADSHEET,
         ".csv": SourceType.SPREADSHEET, ".txt": SourceType.DOCUMENT, ".md": SourceType.DOCUMENT}
STOPWORDS = set("cho tôi hỏi vui lòng hãy giúp mình biết là gì như thế nào về của trong có được không và theo từ ra ở các một những để với này đó tôi muốn đọc tệp tài liệu file drive google the a an what is how to of in for".split())
INDEXED_DRIVE_FORMAT = "indexed_drive_v1"
LINK_CHANGED_MESSAGE = "Liên kết Google Drive đã thay đổi. Hãy gửi lại câu hỏi để tra cứu trên tài khoản hiện tại."
INDEXED_STATUS_MESSAGES = {
    "no_evidence": "Chưa đủ bằng chứng để trả lời trong phạm vi tài liệu Google Drive đã tra cứu.",
    "not_indexed": "Chưa có chỉ mục Google Drive khả dụng cho tài khoản hiện tại.",
    "connection_error": "Không thể hoàn tất tra cứu Google Drive vì lỗi kết nối hoặc quyền truy cập.",
}
PARTIAL_COVERAGE_MESSAGE = "Lượt tra cứu mới bao phủ một phần tài liệu Google Drive có thể truy cập."


class DriveLinkChanged(Exception):
    """Indexed content no longer belongs to the current Drive connection."""


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


def drive_query_terms(question: str) -> tuple[str, list[str]]:
    clean = re.sub(r"https?://[^\s<>]+", "", question).strip()
    words = re.findall(r"[^\W_]+(?:[-./_][^\W_]+)*", clean.casefold())
    terms = list(dict.fromkeys(word for word in words if len(word) >= 2 and word not in STOPWORDS))[:8]
    return clean, terms


def resolve_drive_follow_up(question: str, history: list[dict]) -> str:
    """Carry a business reference explicitly supplied in a recent user turn."""
    if drive_reference(question)[1]:
        return question
    reference = re.compile(r"(?<![\w-])[A-Za-z]{2,12}[-_.]?\d{2,}[A-Za-z0-9-]*(?![\w-])")
    cues = {"mã đó", "mã này", "mục đó", "ma do", "ma nay", "muc do"}
    if reference.search(question) or not any(cue in question.casefold() for cue in cues):
        return question
    for turn in reversed(history[-6:]):
        if turn.get("role") != "user" or not isinstance(turn.get("content"), str):
            continue
        clean, _ = drive_query_terms(turn["content"])
        codes = {match.upper() for match in reference.findall(clean)}
        if len(codes) == 1:
            return f"{next(iter(codes))} — {question}"
        if codes:
            break
    return question


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
    clean_question, terms = drive_query_terms(question)
    response = await gateway_client.drive_knowledge(context, terms, file_id)
    if response.get("status") != "OK":
        return response, []
    with measure_stage("internal_retrieval_ms"):
        evidence, failed = await asyncio.to_thread(extract_evidence, response.get("files", []), clean_question,
                                                   direct=bool(file_id) or not terms)
    return {"status": "OK", "skipped": response.get("skipped", 0) + failed}, evidence


async def answer_from_drive(question, context, *, documents=None):
    if not question:
        return ["Bạn muốn tra cứu nội dung gì trên Google Drive?"]
    if documents is None and get_settings().DRIVE_INDEX_ENABLED:
        file_id, has_link = drive_reference(question)
        if has_link and not file_id:
            return ["Hãy gửi link một tệp cụ thể hoặc đặt câu hỏi để tôi tìm trên Google Drive."]
        _, terms = drive_query_terms(question)
        indexed = await gateway_client.drive_search(context, question, file_id, terms)
        try:
            return await answer_from_indexed_drive(question, context.user_id, indexed)
        except DriveLinkChanged:
            return [LINK_CHANGED_MESSAGE]
        except GatewayServiceError:
            return [INDEXED_STATUS_MESSAGES["connection_error"]]
    if documents is None:
        status, evidence = await search_drive(question, context)
    else:
        file_id, _ = drive_reference(question)
        clean_question = re.sub(r"https?://[^\s<>]+", "", question).strip()
        with measure_stage("internal_retrieval_ms"):
            evidence, failed = await asyncio.to_thread(extract_evidence, documents.get("files", []),
                                                       clean_question, direct=bool(file_id))
        status = {"status": documents.get("status"), "skipped": documents.get("skipped", 0) + failed}
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
    return await _answer_from_evidence(question, evidence)


def _indexed_evidence(document):
    sources = document.get("sources")
    chunks = document.get("chunks")
    if not isinstance(sources, list) or not isinstance(chunks, list):
        return []
    file_ids = {
        source["file_id"] for source in sources
        if isinstance(source, dict) and isinstance(source.get("file_id"), str)
        and FILE_ID.fullmatch(source["file_id"])
    }
    evidence, seen = [], set()
    for position, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        key = chunk.get("file_id")
        content = chunk.get("content")
        if (not isinstance(key, str) or key not in file_ids
                or not isinstance(content, str) or not content.strip()):
            continue
        if len(seen) == 4 and key not in seen:
            continue
        title = " ".join(str(chunk.get("name") or "Tệp Drive").split())[:180]
        section = chunk.get("page_or_section")
        score = chunk.get("score")
        evidence.append(SearchResult(
            chunk_id=f"{key}:{position}", source_id=key, title=title, content=content,
            score=score if type(score) in (float, int) else 0,
            source={"section": section if isinstance(section, str) else None,
                    "url": f"https://drive.google.com/open?id={key}"},
        ))
        seen.add(key)
        if len(evidence) == 8:
            break
    return evidence


async def answer_from_indexed_drive(question, user_id, document):
    status = document.get("status")
    if status == "link_changed":
        raise DriveLinkChanged
    if status != "ok" or document.get("format") != INDEXED_DRIVE_FORMAT:
        message = INDEXED_STATUS_MESSAGES.get(str(status), INDEXED_STATUS_MESSAGES["connection_error"])
        if status == "no_evidence" and document.get("partial") is True:
            message += f"\n\n{PARTIAL_COVERAGE_MESSAGE}"
        return [message]
    if not question:
        return [INDEXED_STATUS_MESSAGES["connection_error"]]
    link = document.get("link")
    if not isinstance(link, dict):
        raise DriveLinkChanged
    google_sub, version = link.get("google_sub"), link.get("link_version")
    if not isinstance(google_sub, str) or not google_sub or type(version) is not int or version < 1:
        raise DriveLinkChanged
    evidence = _indexed_evidence(document)
    if not evidence:
        return [INDEXED_STATUS_MESSAGES["no_evidence"]]
    verification = document.get("verification")
    if not isinstance(verification, list):
        raise DriveLinkChanged
    files = {
        item["file_id"] for item in verification
        if isinstance(item, dict) and isinstance(item.get("file_id"), str)
        and isinstance(item.get("version"), str) and item["version"]
        and (item.get("drive_id") is None or isinstance(item["drive_id"], str))
    }
    if len(verification) != len(files) or files != {chunk.source_id for chunk in evidence}:
        raise DriveLinkChanged
    if not await gateway_client.drive_link_is_current(user_id, google_sub, version, verification):
        raise DriveLinkChanged
    messages = await _answer_from_evidence(question, evidence)
    if not await gateway_client.drive_link_is_current(user_id, google_sub, version, verification):
        raise DriveLinkChanged
    if document.get("partial") is True:
        messages[0] += f"\n\n{PARTIAL_COVERAGE_MESSAGE}"
    return messages


async def _answer_from_evidence(question, evidence):
    prompt = re.sub(r"https?://[^\s<>]+", "", question).strip() or "Tóm tắt nội dung tài liệu."
    try:
        with measure_stage("llm_synthesis_ms"):
            answer, results = await rag_service.answer_from_evidence(prompt, evidence)
    except GeminiRequestError as exc:
        if exc.category == "rate_limit":
            return ["Đã đọc được tài liệu Drive. Dịch vụ tạo câu trả lời đang giới hạn lượt, bạn hãy thử lại sau."]
        return ["Đã đọc được tài liệu Drive. Dịch vụ tạo câu trả lời đang gặp lỗi."]
    if answer == "INSUFFICIENT_EVIDENCE":
        return ["Các tài liệu Drive tìm được chưa đủ bằng chứng để trả lời câu hỏi này."]
    citations = dict.fromkeys(int(number) for number in re.findall(r"\[Nguồn\s*\[(\d+)\]", answer, re.IGNORECASE))
    links = [f"[{index}] {results[index - 1].title} – {results[index - 1].source['url']}" for index in citations]
    return [answer + "\n\nTệp nguồn trên Google Drive\n" + "\n".join(links)]
