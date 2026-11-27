"""Deterministic Vietnamese natural-language helpers for Odoo Stock / Delivery (stock.picking)."""
from __future__ import annotations

import re
from typing import Any, Optional

from app.business.purchase_local import (
    _parse_product_segments,
    fold_vi,
    is_cancel_draft_phrase,
    parse_purchase_date,
)

# Matches Odoo picking codes like WH/OUT/00018, WH/IN/00001, WH/INT/00005, KHO/OUT/00012
_PICKING_NAME_RE = re.compile(r"\b([A-Z0-9]{2,10}/(?:OUT|IN|INT|PICK|PACK|RET|POS|QC)/\d{3,})\b", re.I)


def _recent_picking_from_history(history: Optional[list[dict[str, Any]]]) -> Optional[str]:
    if not history:
        return None
    for msg in reversed(history[-8:]):
        content = str(msg.get("content") or "")
        found = _PICKING_NAME_RE.findall(content)
        if found:
            return found[-1].upper()
    return None


def interpret_stock_flow(text: str, history: Optional[list[dict[str, Any]]] = None) -> Optional[dict[str, Any]]:
    """Parse lifecycle actions on an existing Odoo delivery/receipt/transfer picking."""
    clean = (text or "").strip()
    if not clean:
        return None
    lower = clean.casefold()
    if "?" in clean or re.match(r"^(?:xem|tra cứu|kiểm tra tình trạng|kiểm tra trạng thái|liệt kê|báo cáo|cho tôi xem)\b", lower):
        return None

    names = _PICKING_NAME_RE.findall(clean)
    if len(names) > 1:
        return None
    if len(names) == 1:
        picking_name = names[0].upper()
    elif re.search(r"phiếu giao|đơn giao|phiếu nhập kho|phiếu chuyển kho|phiếu kho|giao hàng", lower):
        picking_name = _recent_picking_from_history(history)
        if not picking_name:
            return None
    else:
        return None

    if re.search(r"\b(?:đừng|chưa)\b", lower):
        return None

    action: Optional[str] = None
    if re.search(r"bỏ giữ hàng|hủy giữ hàng|unreserve", lower):
        action = "unreserve"
    elif re.search(r"giữ hàng|kiểm tra tồn|kiểm tra khả dụng|reserve|check availability", lower):
        action = "reserve"
    elif re.search(r"trả hàng|hoàn trả|return", lower):
        action = "return"
    elif re.search(r"hoàn tất|xác thực|xuất kho|nhập kho|giao xong|validate|done", lower):
        action = "validate"
    elif re.search(r"\bhủy\b|\bhuỷ\b|\bcancel\b", lower):
        action = "cancel"
    elif re.search(r"\bxác nhận\b|\bconfirm\b", lower):
        action = "confirm"

    if not action:
        return None

    result: dict[str, Any] = {"action": action, "picking_name": picking_name}
    if action == "validate":
        if re.search(r"không tạo backorder|hủy phần còn lại|no backorder|cancel backorder", lower):
            result["backorder"] = "cancel"
        else:
            result["backorder"] = "create"
    return result


def _extract_stock_optional_fields(text: str) -> tuple[str, dict[str, Any]]:
    extra: dict[str, Any] = {}
    rest = text

    m_note = re.search(r"(?:[,;.\n]\s*|\s+)ghi chú\s*[:\-]?\s*(.+)$", rest, re.I)
    if m_note:
        extra["note"] = m_note[1].strip().rstrip(".")
        rest = rest[:m_note.start()].strip()

    m_origin = re.search(r"(?:[,;.\n]\s*|\s+)(?:chứng từ gốc|nguồn|origin|từ đơn)\s*[:\-]?\s*([^,;\n.]+)", rest, re.I)
    if m_origin:
        extra["origin"] = m_origin[1].strip()
        rest = (rest[:m_origin.start()] + " " + rest[m_origin.end():]).strip()

    m_date = re.search(
        r"(?:[,;.\n]\s*|\s+)(?:ngày giao(?:\s+dự kiến)?|ngày dự kiến|ngày nhận(?:\s+dự kiến)?|giao ngày|nhận ngày)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}-\d{1,2}-\d{1,2})",
        rest, re.I,
    )
    if m_date:
        parsed_dt = parse_purchase_date(m_date[1])
        if parsed_dt:
            extra["scheduled_date"] = parsed_dt
        rest = (rest[:m_date.start()] + " " + rest[m_date.end():]).strip()

    return rest.strip(" ,;."), extra


def interpret_stock_create(text: str) -> Optional[dict[str, Any]]:
    """Parse Vietnamese natural-language delivery / receipt / internal transfer creation requests."""
    clean = (text or "").strip()
    if not clean:
        return None
    lower = clean.casefold()
    if "?" in clean or re.match(r"^(?:xem|tra cứu|kiểm tra|liệt kê|báo cáo)\b", lower):
        return None

    prefix = re.match(
        r"^(?:(?:hãy|giúp tôi|tôi muốn|cần|muốn)\s+)?(?:tạo|lập|lên)\s+(?:(?:một|1|mới)\s+)?"
        r"(?P<kind>phiếu giao hàng|đơn giao hàng|phiếu xuất kho|lệnh giao hàng|phiếu nhập kho|phiếu chuyển kho(?:\s+nội bộ)?|phiếu kho)(?P<rest>.*)$",
        clean, re.I,
    )
    if not prefix:
        return None

    kind_text = fold_vi(prefix["kind"])
    if "nhap kho" in kind_text:
        op_type = "incoming"
    elif "chuyen kho" in kind_text:
        op_type = "internal"
    else:
        op_type = "outgoing"

    body = prefix["rest"].strip()
    body = re.sub(r"^(?:(?:cho|giúp|giùm)\s+(?:tôi|mình|em|anh|chị)\s*)+", "", body, flags=re.I).strip()
    body = re.sub(
        r"(?:\s+(?:mới|cho tôi|giúp tôi|giùm tôi|cho mình|giúp mình|nhé|nha|đi|đê|với|ạ))+[.!?]*$",
        "",
        body,
        flags=re.I,
    ).strip(" .!?")

    if not body or body.casefold() in {"mới", "hàng", "cho tôi", "giúp tôi", "nhé", "đi", "đê"}:
        return {"action": "start", "operation_type": op_type}

    body, extra = _extract_stock_optional_fields(body)

    partner: Optional[str] = None
    items_text = ""

    m_partner_first = re.match(
        r"^(?:(?:cho|tới|đến|từ|của)\s+)?(?:khách hàng|khách|đối tác|nhà cung cấp|ncc)\s+(?P<rest>.+)$",
        body, re.I,
    )
    m_partner_last = re.search(
        r"(?:[,;]\s*|\s+)(?:cho|tới|đến|từ|của)\s+(?:khách hàng|khách|đối tác|nhà cung cấp|ncc)\s+(?P<partner>[^,;\n.]+)$",
        body, re.I,
    )
    if m_partner_first:
        p_rest = m_partner_first["rest"].strip()
        m_split = re.search(r"[,;:\n]|\s+(?:sản phẩm|gồm|giao|nhập|với)\s+", p_rest, re.I)
        if m_split:
            partner = p_rest[:m_split.start()].strip(" ,;:.")
            items_text = p_rest[m_split.end():].strip(" ,;:.")
        else:
            partner = p_rest.strip(" ,;:.")
    elif m_partner_last:
        partner = m_partner_last["partner"].strip(" ,;:.")
        items_text = body[:m_partner_last.start()].strip(" ,;:.")
    else:
        items_text = body

    lines = _parse_product_segments(items_text) if items_text else []
    payload: dict[str, Any] = {"action": "start", "operation_type": op_type}
    if partner:
        payload["partner_query"] = partner
    if lines:
        payload["lines"] = [{"product_query": l["product_query"], "quantity": l["quantity"]} for l in lines if l.get("product_query")]
    payload.update(extra)
    return payload


def interpret_stock_draft_reply(text: str, active_draft: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Interpret a follow-up reply while a business.stock.create draft is active."""
    if not active_draft or active_draft.get("business_action") != "business.stock.create":
        return None
    clean = (text or "").strip()
    if not clean or clean.startswith("/"):
        return None
    if is_cancel_draft_phrase(clean):
        return {"action": "cancel"}

    saved = active_draft.get("business_data") or {}
    has_partner = bool((saved.get("values") or {}).get("partner_id"))
    has_lines = bool(saved.get("lines"))

    body, extra = _extract_stock_optional_fields(clean)
    payload: dict[str, Any] = {"action": "update", **extra}

    if "\n" in body:
        raw_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        parsed_lines: list[dict[str, Any]] = []
        non_product_lines: list[str] = []
        for ln in raw_lines:
            m_p = re.match(r"^(?:khách hàng|khách|đối tác|nhà cung cấp|ncc)\s*[:\-]?\s*(.+)$", ln, re.I)
            if m_p:
                payload["partner_query"] = m_p[1].strip(" ,;.")
                continue
            segs = _parse_product_segments(ln)
            if segs:
                parsed_lines.extend({"product_query": s["product_query"], "quantity": s["quantity"]} for s in segs if s.get("product_query"))
            else:
                non_product_lines.append(ln)
        if not has_partner and "partner_query" not in payload and non_product_lines:
            payload["partner_query"] = non_product_lines[0]
        if parsed_lines:
            payload["lines"] = parsed_lines
        if "partner_query" in payload or "lines" in payload or extra:
            return payload

    m_p = re.match(r"^(?:khách hàng|khách|đối tác|nhà cung cấp|ncc)\s*[:\-]?\s*(.+)$", body, re.I)
    if m_p:
        payload["partner_query"] = m_p[1].strip(" ,;.")
        return payload

    if body:
        segs = _parse_product_segments(body)
        if segs:
            payload["lines"] = [{"product_query": s["product_query"], "quantity": s["quantity"]} for s in segs if s.get("product_query")]
            return payload

    if not has_partner and body and not re.search(r"\b(?:xem|tra cứu|tạo|hủy|xác nhận)\b", body.casefold()):
        payload["partner_query"] = body
        return payload

    if extra and (has_partner or has_lines):
        return payload
    return None
