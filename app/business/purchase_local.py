"""Deterministic Vietnamese natural-language helpers for Odoo Purchasing (RFQ / PO)."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Optional

_PO_NAME_RE = re.compile(r"\b(P(?:O)?\d{3,})\b", re.I)

_PAYMENT_TERM_MAP = {
    "thanh toan ngay": ("Thanh toán ngay", "Immediate Payment"),
    "thanh toan ngay lap tuc": ("Thanh toán ngay", "Immediate Payment"),
    "tra ngay": ("Thanh toán ngay", "Immediate Payment"),
    "ngay lap tuc": ("Thanh toán ngay", "Immediate Payment"),
    "cuoi thang ke tiep": ("Cuối tháng kế tiếp", "End of Following Month"),
    "10 ngay sau ngay cuoi thang ke tiep": ("10 ngày sau ngày cuối tháng kế tiếp", "10 Days After End of Next Month"),
    "30% tra ngay, con lai tra trong 60 ngay": ("30% trả ngay, còn lại trả trong 60 ngày", "30% Now, Balance 60 Days"),
    "trong 30 ngay, giam 2% trong 7 ngay": ("Trong 30 ngày, giảm 2% trong 7 ngày", "2% 7 Days, Net 30"),
    "90 ngay, vao ngay 10": ("90 ngày, vào ngày 10", "90 Days, on the 10th"),
}

_ARBITRARY_QUERIES = {"bất kỳ", "bất kì", "bất kỳ ai", "gì cũng được", "nào cũng được"}

_CANCEL_DRAFT_PHRASES = {
    "hủy", "huy", "hủy bỏ", "huy bo", "hủy nháp", "huy nhap",
    "bỏ qua nháp", "thôi không tạo nữa", "thoi khong tao nua",
    "không tạo nữa", "khong tao nua", "dừng lại", "dung lai",
}


def fold_vi(text: str) -> str:
    """Fold Vietnamese accents and whitespace for robust comparison."""
    s = unicodedata.normalize("NFD", (text or "").replace("đ", "d").replace("Đ", "D"))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s.casefold()).strip()


def parse_purchase_date(raw: str) -> Optional[str]:
    """Parse DD/MM/YYYY, DD-MM-YYYY, or YYYY-MM-DD into Odoo UTC ISO datetime string."""
    s = (raw or "").strip()
    if not s:
        return None
    m_dmy = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m_dmy:
        day, month, year = int(m_dmy[1]), int(m_dmy[2]), int(m_dmy[3])
        hour, minute, sec = int(m_dmy[4] or 0), int(m_dmy[5] or 0), int(m_dmy[6] or 0)
        try:
            dt = datetime(year, month, day, hour, minute, sec)
            return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
        except ValueError:
            return None
    m_iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s](\d{1,2}):(\d{2})(?::(\d{2}))?(?:Z|[+-]\d{2}:?\d{2})?)?", s)
    if m_iso:
        year, month, day = int(m_iso[1]), int(m_iso[2]), int(m_iso[3])
        hour, minute, sec = int(m_iso[4] or 0), int(m_iso[5] or 0), int(m_iso[6] or 0)
        try:
            dt = datetime(year, month, day, hour, minute, sec)
            return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
        except ValueError:
            return None
    return None


def payment_term_candidates(query: str) -> list[str]:
    """Return ordered Vietnamese + English lookup queries for a payment term phrase."""
    folded = fold_vi(query)
    if not folded:
        return []
    if folded in _PAYMENT_TERM_MAP:
        return list(_PAYMENT_TERM_MAP[folded])
    m_days = re.fullmatch(r"(?:trong\s+)?(\d+)\s*(?:ngay|days?)", folded)
    if m_days:
        n = m_days[1]
        return [f"{n} ngày", f"{n} Days", query.strip()]
    return [query.strip()]


def parse_price_value(raw: str) -> Optional[float]:
    """Parse Vietnamese price expressions like 500000, 500.000, 500,000, 500k, 1.5tr."""
    s = (raw or "").strip().lower().replace("vnd", "").replace("vnđ", "").replace("đ", "").strip()
    if not s:
        return None
    mult = 1.0
    if s.endswith(("tr", "triệu", "trieu")):
        s = re.sub(r"(?:triệu|trieu|tr)$", "", s).strip()
        mult = 1_000_000.0
    elif s.endswith("k"):
        s = s[:-1].strip()
        mult = 1_000.0
    # Handle Vietnamese thousands separators like 500.000 or 1.200.000
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        val = float(s) * mult
        return val if val >= 0 else None
    except ValueError:
        return None


def is_cancel_draft_phrase(text: str) -> bool:
    return fold_vi(text).rstrip(".!?") in {fold_vi(p) for p in _CANCEL_DRAFT_PHRASES}


def _recent_po_from_history(history: Optional[list[dict[str, Any]]]) -> Optional[str]:
    if not history:
        return None
    for msg in reversed(history[-8:]):
        content = str(msg.get("content") or "")
        found = _PO_NAME_RE.findall(content)
        if found:
            return found[-1].upper()
    return None


def _po_from_last_read(last_read: Optional[dict[str, Any]]) -> Optional[str]:
    if not isinstance(last_read, dict):
        return None
    res = last_read.get("business_action_result")
    if isinstance(res, dict):
        inner = res.get("result") if isinstance(res.get("result"), dict) else res
        rec = inner.get("record") if isinstance(inner, dict) else None
        if isinstance(rec, dict):
            name = str(rec.get("name") or rec.get("display_name") or "").strip()
            if _PO_NAME_RE.fullmatch(name):
                return name.upper()
    return None


def interpret_purchase_flow(
    text: str,
    history: Optional[list[dict[str, Any]]] = None,
    last_read: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Parse lifecycle actions on an existing Odoo RFQ / PO (confirm, approve, reject, cancel, draft, copy, delete, send)."""
    clean = (text or "").strip()
    if not clean:
        return None
    lower = clean.casefold()
    # Never hijack read/report/question turns
    if "?" in clean or re.match(r"^(?:xem|tra cứu|kiểm tra|liệt kê|báo cáo|cho tôi xem|tình trạng|trạng thái)\b", lower):
        return None
    names = _PO_NAME_RE.findall(clean)
    if len(names) > 1:
        return None
    explicit_po_noun = bool(re.search(r"đơn mua|rfq|yêu cầu báo giá mua", lower))
    if len(names) == 1:
        po_name = names[0].upper()
    elif explicit_po_noun:
        po_name = _recent_po_from_history(history) or _po_from_last_read(last_read) or ""
        if not po_name and re.search(r"(?:hủy|huỷ)\s+(?:tạo|lập|nháp)", lower):
            return None
    else:
        return None

    # Negative guard ("không duyệt" is reject, "không hủy / đừng xác nhận" is negated)
    if re.search(r"\b(?:đừng|chưa)\b", lower):
        return None

    action: Optional[str] = None
    if re.search(r"từ chối|không duyệt|reject", lower):
        action = "reject"
    elif re.search(r"(?:đưa|chuyển|khôi phục|mở lại).*(?:về\s+)?nháp|set\s+to\s+draft", lower):
        action = "draft"
    elif re.search(r"sao chép|nhân bản|copy|duplicate", lower):
        action = "copy"
    elif re.search(r"\bxóa\b|\bxoá\b|\bdelete\b", lower):
        action = "delete"
    elif re.search(r"\bhủy\b|\bhuỷ\b|\bcancel\b", lower):
        action = "cancel"
    elif re.search(r"\b(?:gửi|send)\b", lower) and re.search(r"rfq|đơn mua|mail|email|nhà cung cấp|ncc", lower):
        action = "send"
    elif re.search(r"\b(?:phê duyệt|duyệt|approve)\b", lower):
        action = "approve"
    elif re.search(r"\b(?:xác nhận|chốt|confirm)\b", lower):
        action = "confirm"

    if not action:
        return None

    result: dict[str, Any] = {"action": action, "purchase_name": po_name}
    if action == "send":
        emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", clean)
        if emails:
            result["recipient_emails"] = emails
        m_subj = re.search(r"tiêu đề\s*[:\-]?\s*([^;\n]+)", clean, re.I)
        if m_subj:
            result["subject"] = m_subj[1].strip()
        m_body = re.search(r"nội dung\s*[:\-]?\s*(.+)$", clean, re.I)
        if m_body:
            result["body"] = m_body[1].strip()
    return result


def _extract_optional_fields(text: str) -> tuple[str, dict[str, Any]]:
    """Strip and extract optional purchase fields (date_planned, payment_term, partner_ref, notes) from text."""
    extra: dict[str, Any] = {}
    rest = text

    # Notes
    m_note = re.search(r"(?:[,;.\n]\s*|\s+)ghi chú\s*[:\-]?\s*(.+)$", rest, re.I)
    if m_note:
        extra["notes"] = m_note[1].strip().rstrip(".")
        rest = rest[:m_note.start()].strip()

    # Partner ref
    m_ref = re.search(r"(?:[,;.\n]\s*|\s+)(?:mã tham chiếu(?:\s+ncc|\s+nhà cung cấp)?|tham chiếu(?:\s+ncc)?|số tham chiếu)\s*[:\-]?\s*([^,;\n.]+)", rest, re.I)
    if m_ref:
        extra["partner_ref"] = m_ref[1].strip()
        rest = (rest[:m_ref.start()] + " " + rest[m_ref.end():]).strip()

    # Payment terms (compound comma terms first)
    compound_terms = (
        r"30%\s*trả\s*ngay,\s*còn\s*lại\s*trả\s*trong\s*60\s*ngày",
        r"trong\s*30\s*ngày,\s*giảm\s*2%\s*trong\s*7\s*ngày",
        r"90\s*ngày,\s*vào\s*ngày\s*10",
        r"10\s*ngày\s*sau\s*ngày\s*cuối\s*tháng\s*kế\s*tiếp",
        r"cuối\s*tháng\s*kế\s*tiếp",
        r"thanh\s*toán\s*ngay(?:\s*lập\s*tức)?",
    )
    for pat in compound_terms:
        m_pt = re.search(rf"(?:[,;.\n]\s*|\s+)(?:điều khoản thanh toán\s*[:\-]?\s*)?({pat})", rest, re.I)
        if m_pt:
            extra["payment_term_query"] = m_pt[1].strip()
            rest = (rest[:m_pt.start()] + " " + rest[m_pt.end():]).strip()
            break
    if "payment_term_query" not in extra:
        m_pt = re.search(r"(?:[,;.\n]\s*|\s+)điều khoản thanh toán\s*[:\-]?\s*([^,;\n.]+)", rest, re.I)
        if m_pt:
            extra["payment_term_query"] = m_pt[1].strip()
            rest = (rest[:m_pt.start()] + " " + rest[m_pt.end():]).strip()

    # Planned delivery date
    m_date = re.search(
        r"(?:[,;.\n]\s*|\s+)(?:ngày nhận(?:\s+dự kiến)?|ngày giao(?:\s+dự kiến)?|giao ngày|nhận ngày)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}-\d{1,2}-\d{1,2})",
        rest, re.I,
    )
    if m_date:
        parsed_dt = parse_purchase_date(m_date[1])
        if parsed_dt:
            extra["date_planned"] = parsed_dt
        rest = (rest[:m_date.start()] + " " + rest[m_date.end():]).strip()

    return rest.strip(" ,;."), extra


def _parse_product_segments(raw_items: str, default_date_planned: Optional[str] = None) -> list[dict[str, Any]]:
    """Parse one or more Vietnamese product line segments into [{product_query, quantity, price_unit?, date_planned?}]."""
    lines: list[dict[str, Any]] = []
    # Split on comma, semicolon, newline, or " và "
    segments = [seg.strip() for seg in re.split(r"[,;\n]+|\s+và\s+", raw_items) if seg.strip()]
    for seg in segments:
        # Skip bare dates or punctuation
        if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", seg) or not re.search(r"[a-zA-ZÀ-ỹ0-9]", seg):
            continue
        price_unit: Optional[float] = None
        m_price = re.search(r"(?:\s+|^)(?:đơn\s*giá|giá)\s*[:\-]?\s*(\d[\d.,]*(?:\s*(?:k|tr|triệu|trieu|vnd|vnđ|đ))?)\b", seg, re.I)
        if m_price:
            price_unit = parse_price_value(m_price[1])
            seg = (seg[:m_price.start()] + " " + seg[m_price.end():]).strip()

        qty: Optional[float] = None
        prod: Optional[str] = None

        # Pattern 1: "sản phẩm X - số lượng N" or "X số lượng N" or "X SL N"
        m1 = re.fullmatch(
            r"(?:sản phẩm\s+)?(?P<prod>.+?)\s*(?:[-–—:,]\s*|\s+)(?:số lượng|sl|x)\s*[:\-]?\s*(?P<qty>\d+(?:[.,]\d+)?)",
            seg, re.I,
        )
        # Pattern 2: "N [cái/chiếc/bộ/hộp/thùng/kg/mét] X"
        m2 = re.fullmatch(
            r"(?P<qty>\d+(?:[.,]\d+)?)\s*(?:(?:cái|chiếc|bộ|hộp|thùng|kg|g|lít|mét|m|cây|cuộn|túi|gói|chai|lon|bịch|tấm|đơn vị|sp|sản phẩm)\s+)?(?P<prod>[a-zA-ZÀ-ỹ].+)",
            seg, re.I,
        )
        if m1:
            prod = m1["prod"].strip(" -–—:;,.")
            qty = float(m1["qty"].replace(",", "."))
        elif m2:
            prod = m2["prod"].strip(" -–—:;,.")
            qty = float(m2["qty"].replace(",", "."))

        if prod and qty and qty > 0:
            prod = re.sub(r"\s+mã\s+\S+$", "", prod, flags=re.I).strip(" ,;.")
            if prod:
                item: dict[str, Any] = {
                    "product_query": "" if prod.casefold() in _ARBITRARY_QUERIES else prod,
                    "quantity": qty,
                }
                if price_unit is not None:
                    item["price_unit"] = price_unit
                if default_date_planned:
                    item["date_planned"] = default_date_planned
                lines.append(item)
    return lines


def interpret_purchase_create(text: str) -> Optional[dict[str, Any]]:
    """Parse Vietnamese natural-language RFQ / purchase order creation requests."""
    clean = (text or "").strip()
    if not clean:
        return None
    lower = clean.casefold()
    if "?" in clean or re.match(r"^(?:xem|tra cứu|kiểm tra|liệt kê|báo cáo)\b", lower):
        return None

    # Exact legacy template first (preserves 100% compatibility with existing tests)
    legacy = re.fullmatch(
        r"(?:(?:hãy|giúp tôi|tôi muốn)\s+)?(?:tạo|lập)\s+(?:(?:một|1)\s+)?"
        r"(?:rfq(?: mua hàng)?|đơn mua(?: hàng)?)\s+(?:cho\s+)?nhà cung cấp\s+(?P<supplier>[^,.\n]+)"
        r"[,;]\s*sản phẩm\s+(?P<product>[^,.\n]+)[,;]\s*số lượng\s+(?P<quantity>\d+(?:[.,]\d+)?)"
        r"(?:\.\s*Dùng giá và thuế Odoo)?[.!]?",
        clean, re.I,
    )
    if legacy:
        supplier = legacy["supplier"].strip()
        product = re.sub(r"\s+mã\s+\S+$", "", legacy["product"].strip(), flags=re.I)
        return {
            "action": "start",
            "supplier_query": "" if supplier.casefold() in _ARBITRARY_QUERIES else supplier,
            "choose_first": supplier.casefold() in _ARBITRARY_QUERIES or product.casefold() in _ARBITRARY_QUERIES,
            "lines": [{
                "product_query": "" if product.casefold() in _ARBITRARY_QUERIES else product,
                "quantity": float(legacy["quantity"].replace(",", ".")),
            }],
        }

    # Must clearly indicate creating a new RFQ / purchase order
    prefix = re.match(
        r"^(?:(?:hãy|giúp tôi|tôi muốn|cần|muốn)\s+)?(?:(?:tạo|lập|đặt|lên)\s+(?:(?:một|1|mới)\s+)?(?:rfq(?:\s+mua hàng)?|yêu cầu báo giá mua(?:\s+hàng)?|đơn mua(?:\s+hàng)?|đơn đặt hàng(?:\s+mua)?)|mua\s+(?=\d))(?P<rest>.*)$",
        clean, re.I,
    )
    if not prefix:
        return None

    body = prefix["rest"].strip()
    body = re.sub(
        r"^(?:(?:cho|giúp|giùm)\s+(?:tôi|mình|em|anh|chị)\s*)+",
        "",
        body,
        flags=re.I,
    ).strip()
    body = re.sub(
        r"(?:\s+(?:mới|cho tôi|giúp tôi|giùm tôi|cho mình|giúp mình|nhé|nha|đi|đê|với|ạ))+[.!?]*$",
        "",
        body,
        flags=re.I,
    ).strip(" .!?")
    if not body or body.casefold() in {"mới", "hàng", "hàng mới", "giúp tôi", "cho tôi", "nhé", "đi", "đê"}:
        return {"action": "start"}

    body, extra = _extract_optional_fields(body)
    body = re.sub(r"\.\s*Dùng giá và thuế Odoo[.!]?$", "", body, flags=re.I).strip()

    supplier: Optional[str] = None
    items_text = ""

    # Form A: "... (cho/từ/của) (nhà cung cấp|NCC) <supplier> [,;:] <items>"
    m_sup_first = re.match(
        r"^(?:(?:cho|từ|của|với)\s+)?(?:nhà cung cấp|ncc)\s+(?P<sup_and_rest>.+)$",
        body, re.I,
    )
    # Form B: "<items> (từ|của|cho) (nhà cung cấp|NCC) <supplier>"
    m_sup_last = re.search(
        r"(?:[,;]\s*|\s+)(?:từ|của|cho)\s+(?:nhà cung cấp|ncc)\s+(?P<supplier>[^,;\n.]+)$",
        body, re.I,
    )

    if m_sup_first:
        sup_rest = m_sup_first["sup_and_rest"].strip()
        # Split supplier from product clauses by ", ; : \n" or " sản phẩm " or " mua "
        m_split = re.search(r"[,;:\n]|\s+(?:sản phẩm|gồm|mua|với)\s+", sup_rest, re.I)
        if m_split:
            supplier = sup_rest[:m_split.start()].strip(" ,;:.")
            items_text = sup_rest[m_split.end():].strip(" ,;:.")
            if m_split.group(0).strip().lower() == "sản phẩm":
                items_text = "sản phẩm " + items_text
        else:
            supplier = sup_rest.strip(" ,;:.")
    elif m_sup_last:
        supplier = m_sup_last["supplier"].strip(" ,;:.")
        items_text = body[:m_sup_last.start()].strip(" ,;:.")
    else:
        # Only product lines provided (no supplier yet)
        items_text = body

    # Handle "sản phẩm X, số lượng N" as a single line before comma-splitting
    lines: list[dict[str, Any]] = []
    if items_text:
        m_single = re.fullmatch(
            r"(?:sản phẩm\s+)?(?P<prod>[^,;\n]+?)[,;]\s*(?:số lượng|sl)\s*[:\-]?\s*(?P<qty>\d+(?:[.,]\d+)?)(?:[,;\s]+(?:đơn\s*giá|giá)\s*[:\-]?\s*(?P<price>\d[\d.,]*(?:\s*(?:k|tr|triệu|trieu|vnd|vnđ|đ))?))?",
            items_text, re.I,
        )
        if m_single:
            prod = re.sub(r"\s+mã\s+\S+$", "", m_single["prod"].strip(), flags=re.I)
            item: dict[str, Any] = {
                "product_query": "" if prod.casefold() in _ARBITRARY_QUERIES else prod,
                "quantity": float(m_single["qty"].replace(",", ".")),
            }
            if m_single["price"]:
                p = parse_price_value(m_single["price"])
                if p is not None:
                    item["price_unit"] = p
            if extra.get("date_planned"):
                item["date_planned"] = extra["date_planned"]
            lines = [item]
        else:
            lines = _parse_product_segments(items_text, default_date_planned=extra.get("date_planned"))

    if not supplier and not lines and not extra:
        return None

    choose_first = bool(
        (supplier and supplier.casefold() in _ARBITRARY_QUERIES)
        or any(not line.get("product_query") for line in lines)
    )
    payload: dict[str, Any] = {"action": "start"}
    if supplier is not None:
        payload["supplier_query"] = "" if supplier.casefold() in _ARBITRARY_QUERIES else supplier
    if choose_first:
        payload["choose_first"] = True
    if lines:
        payload["lines"] = lines
    payload.update(extra)
    return payload


def interpret_purchase_draft_reply(text: str, active_draft: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Interpret a follow-up reply while a business.purchases.create draft is active."""
    if not active_draft or active_draft.get("business_action") != "business.purchases.create":
        return None
    clean = (text or "").strip()
    if not clean or clean.startswith("/"):
        return None
    if is_cancel_draft_phrase(clean):
        return {"action": "cancel"}

    saved = active_draft.get("business_data") or {}
    has_supplier = bool((saved.get("values") or {}).get("partner_id"))
    has_lines = bool(saved.get("lines"))

    body, extra = _extract_optional_fields(clean)
    payload: dict[str, Any] = {"action": "update", **extra}

    # Multi-line reply: e.g. line 1 = supplier name ("GOLD GULF INT'L TRADE CO., LTD - NA"),
    # line 2+ = product lines ("Buồng oxy cao áp... - số lượng 2")
    if "\n" in body:
        raw_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        parsed_lines: list[dict[str, Any]] = []
        non_product_lines: list[str] = []
        for ln in raw_lines:
            m_sup_ln = re.match(r"^(?:nhà cung cấp|ncc)\s*[:\-]?\s*(.+)$", ln, re.I)
            if m_sup_ln:
                payload["supplier_query"] = m_sup_ln[1].strip(" ,;.")
                continue
            m_single_ln = re.fullmatch(
                r"(?:sản phẩm\s+)?(?P<prod>.+?)\s*(?:[-–—:,;]\s*|\s+)(?:số lượng|sl)\s*[:\-]?\s*(?P<qty>\d+(?:[.,]\d+)?)",
                ln, re.I,
            )
            if m_single_ln:
                item: dict[str, Any] = {
                    "product_query": m_single_ln["prod"].strip(" -–—:;,."),
                    "quantity": float(m_single_ln["qty"].replace(",", ".")),
                }
                if extra.get("date_planned"):
                    item["date_planned"] = extra["date_planned"]
                parsed_lines.append(item)
                continue
            segs = _parse_product_segments(ln, default_date_planned=extra.get("date_planned"))
            if segs:
                parsed_lines.extend(segs)
            else:
                non_product_lines.append(ln)
        if not has_supplier and "supplier_query" not in payload and non_product_lines:
            payload["supplier_query"] = non_product_lines[0]
        if parsed_lines:
            payload["lines"] = parsed_lines
        if "supplier_query" in payload or "lines" in payload or extra:
            return payload

    # Explicit supplier update ("nhà cung cấp X" / "NCC X")
    m_sup = re.match(r"^(?:nhà cung cấp|ncc)\s*[:\-]?\s*(.+)$", body, re.I)
    if m_sup:
        payload["supplier_query"] = m_sup[1].strip(" ,;.")
        return payload

    # Try parsing product lines
    if body:
        m_single = re.fullmatch(
            r"(?:sản phẩm\s+)?(?P<prod>[^,;\n]+?)\s*(?:[-–—:,;]\s*|\s+)(?:số lượng|sl)\s*[:\-]?\s*(?P<qty>\d+(?:[.,]\d+)?)",
            body, re.I,
        )
        if m_single:
            line: dict[str, Any] = {
                "product_query": m_single["prod"].strip(),
                "quantity": float(m_single["qty"].replace(",", ".")),
            }
            if extra.get("date_planned"):
                line["date_planned"] = extra["date_planned"]
            payload["lines"] = [line]
            return payload
        lines = _parse_product_segments(body, default_date_planned=extra.get("date_planned"))
        if lines:
            payload["lines"] = lines
            return payload

    # If supplier is still missing and user replied with a plain name (no digits/action verbs)
    if not has_supplier and body and not re.search(r"\b(?:xem|tra cứu|tạo|hủy|xác nhận)\b", body.casefold()):
        payload["supplier_query"] = body
        return payload

    if extra and (has_supplier or has_lines):
        return payload
    return None
