"""Stage 1 TurnRouter and grounded context resolver.

Separates mode classification (chitchat / context / knowledge / read / prepare_write / clarify)
from Stage 2 tool selection, while keeping strict grounding and read-only guarantees.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Optional

from app.core.config import get_settings
from app.domain.gateway_context import GatewayUserContext
from app.domain.route_schema import ContextLookupSpec, RouteDecision, RouteMode
from app.services.intent_prompt import ROUTER_PROMPT

logger = logging.getLogger(__name__)

_LIVE_STATUS_PATTERN = re.compile(
    r"mới nhất|moi nhat|hiện tại|hien tai|bây giờ|bay gio|đã duyệt chưa|da duyet chua|"
    r"trạng thái mới|trang thai moi|vừa cập nhật|vua cap nhat|đã thanh toán chưa|da thanh toan chua",
    re.IGNORECASE,
)

_NEGATION_ACTION_PATTERN = re.compile(
    r"(?:^|\b)(?:đừng|dung|không cần|khong can|thôi đừng|thoi dung|thôi không|thoi khong|chưa cần|chua can)\s+"
    r"(?:gửi|gui|tạo|tao|lập|lap|xóa|xoa|sửa|sua|đặt|dat|soạn|soan|hủy|huy)\b",
    re.IGNORECASE,
)

_THANKS_PATTERN = re.compile(r"\b(?:cảm ơn|cam on|thanks|thank you|tks|ty)\b", re.IGNORECASE)
_GREETING_PATTERN = re.compile(r"^(?:xin\s+)?(?:chào|chao|hello|hi|hey|alo)\b", re.IGNORECASE)

_BLIND_DEICTIC_PATTERN = re.compile(
    r"\b(?:cái này|cai nay|cái kia|cai kia|cái đó|cai do|mục này|muc nay|mục đó|muc do|"
    r"đơn đó|don do|đơn kia|don kia|khách đó|khach do|khách kia|khach kia|file đó|file do|mail đó|mail do)\b",
    re.IGNORECASE,
)
_EXPLICIT_ENTITY_PATTERN = re.compile(
    r"\b(?:S\d{3,}|PO\d{3,}|INV/\S+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)
_AMBIGUOUS_HD_PATTERN = re.compile(r"(?<!\w)(?:hđ|hd)(?!\w)", re.IGNORECASE)
_DISAMBIGUATED_HD_PATTERN = re.compile(r"hợp đồng|hop dong|hóa đơn|hoa don", re.IGNORECASE)


def _allowed_domains_for_context(context: GatewayUserContext) -> list[str]:
    if not context.permissions:
        return ["calendar", "gmail", "drive", "docs", "sheets", "slides", "odoo", "crm", "quotation", "reports", "tasks", "knowledge", "briefing"]
    perms = context.permissions
    domains = ["knowledge", "briefing"]
    mapping = [
        ("calendar", ("calendar.",)),
        ("gmail", ("gmail.",)),
        ("drive", ("drive.",)),
        ("docs", ("docs.", "drive.")),
        ("sheets", ("sheets.", "drive.")),
        ("slides", ("slides.", "drive.")),
        ("odoo", ("odoo.", "sales.", "inventory.", "accounting.")),
        ("crm", ("crm.",)),
        ("quotation", ("sales.", "odoo.")),
        ("reports", ("odoo.", "sales.", "crm.")),
        ("tasks", ("tasks.",)),
    ]
    for dom, prefixes in mapping:
        if any(p.startswith(prefixes) for p in perms):
            domains.append(dom)
    return domains

# Map logical field names to candidate keys in Odoo / Workspace records
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "phone": ("phone", "mobile", "phone_sanitized", "telephone", "sđt", "sdt"),
    "email": ("email", "email_from", "from", "mail", "email_normalized"),
    "name": ("name", "display_name", "summary", "subject", "title"),
    "address": ("contact_address", "street", "address", "city", "location"),
    "amount": ("amount_total", "expected_revenue", "price_unit", "amount", "total"),
    "status": ("state", "stage_id", "status"),
    "owner": ("user_id", "salesperson", "owner", "assignee"),
    "time": ("start", "date_deadline", "date_order", "date", "due_date", "created_at"),
    "id": ("id",),
}

_FIELD_LABELS_VI: dict[str, str] = {
    "phone": "Số điện thoại",
    "email": "Email",
    "name": "Tên",
    "address": "Địa chỉ",
    "amount": "Giá trị",
    "status": "Trạng thái",
    "owner": "Người phụ trách",
    "time": "Thời gian",
    "id": "Mã ID",
}

# Category prefix -> required permission prefix (if permissions are present)
_CATEGORY_PERMISSION: dict[str, str] = {
    "workspace.gmail": "gmail.read",
    "gmail": "gmail.read",
    "workspace.calendar": "calendar.read",
    "calendar": "calendar.read",
    "workspace.drive": "drive.read",
    "drive": "drive.read",
    "crm": "crm.read",
    "tasks": "tasks.read",
    "odoo": "odoo.read",
}

_IGNORED_LAST_READ_KEYS = frozenset({
    "knowledge.query",
    "odoo_last_count",
    "quotation_preparation_job",
    "quotation_chatter_target",
})


def _strip_json_fences(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    return cleaned


def _record_label(record: dict[str, Any]) -> str:
    for key in ("name", "display_name", "summary", "subject", "title", "email", "id"):
        val = record.get(key)
        if isinstance(val, (list, tuple)) and len(val) >= 2:
            val = val[1]
        if val not in (None, "", False):
            return str(val)[:120]
    return "Bản ghi"


def _format_field_value(val: Any) -> Optional[str]:
    if val in (None, "", False):
        return None
    if isinstance(val, (list, tuple)):
        if len(val) >= 2 and isinstance(val[1], str):
            return val[1]
        return ", ".join(str(v) for v in val if v not in (None, "", False)) or None
    if isinstance(val, dict):
        for k in ("dateTime", "date", "name", "email", "display_name"):
            if val.get(k):
                return str(val[k])
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def _category_allowed(category: str, context: GatewayUserContext) -> bool:
    if not context.permissions:
        return True
    cat_lower = category.lower()
    for prefix, perm in _CATEGORY_PERMISSION.items():
        if cat_lower == prefix or cat_lower.startswith(prefix + "."):
            if perm == "odoo.read":
                return any(p.startswith(("odoo.", "crm.", "sales.", "inventory.", "accounting.")) for p in context.permissions)
            return perm in context.permissions or any(p.startswith(prefix.split(".")[-1] + ".") for p in context.permissions)
    return True


LAST_READ_TTL_SECONDS = 900.0  # 15 phút


def _record_matches_account(record: dict[str, Any], context: GatewayUserContext) -> bool:
    """Reject records that are expired (TTL > 15m) or belong to a different account/owner."""
    fetched_at = record.get("_fetched_at")
    if isinstance(fetched_at, (int, float)) and (time.time() - fetched_at) > LAST_READ_TTL_SECONDS:
        return False
    rec_owner = record.get("_owner_key") or record.get("owner_key")
    if rec_owner and context.owner_key and str(rec_owner) != str(context.owner_key):
        return False
    rec_gacc = record.get("_google_account_id") or record.get("google_account_id")
    if (
        rec_gacc is not None
        and context.google_account_id is not None
        and str(rec_gacc) != str(context.google_account_id)
    ):
        return False
    return True


def _extract_valid_lists(
    session_context: Optional[dict[str, Any]],
    context: GatewayUserContext,
) -> dict[str, list[dict[str, Any]]]:
    """Return valid, permission-checked, same-account displayed lists from last_read."""
    if not session_context:
        return {}
    last_read = session_context.get("last_read") or session_context.get("last_read_data")
    if not isinstance(last_read, dict):
        return {}
    valid: dict[str, list[dict[str, Any]]] = {}
    for cat, val in last_read.items():
        if cat in _IGNORED_LAST_READ_KEYS or cat.startswith("business_candidates_"):
            continue
        if not _category_allowed(cat, context):
            continue
        records: list[dict[str, Any]] = []
        if isinstance(val, list):
            records = [r for r in val if isinstance(r, dict) and _record_matches_account(r, context)]
        elif isinstance(val, dict):
            # Support wrapper dicts like {"items": [...], "google_account_id": ...}
            if not _record_matches_account(val, context):
                continue
            inner = val.get("items") or val.get("records") or val.get("messages")
            if isinstance(inner, list):
                records = [r for r in inner if isinstance(r, dict) and _record_matches_account(r, context)]
        if records:
            valid[cat] = records
    return valid


def _canonical_field(field_name: Optional[str]) -> Optional[str]:
    if not field_name:
        return None
    low = field_name.strip().lower()
    for canonical, aliases in _FIELD_ALIASES.items():
        if low == canonical or low in aliases:
            return canonical
    return low


def chitchat_reply(raw_text: str) -> str:
    """Concise template replies for chitchat / negation acknowledgements without hallucinating facts."""
    low = (raw_text or "").strip().lower()
    if _NEGATION_ACTION_PATTERN.search(low):
        return "Đã ghi nhận, tôi sẽ không thực hiện thao tác đó."
    if _THANKS_PATTERN.search(low):
        return "Không có gì! Bạn cần tôi hỗ trợ thêm gì cứ nhắn nhé."
    if _GREETING_PATTERN.search(low):
        return "Xin chào! Tôi có thể giúp gì cho bạn hôm nay?"
    return "Đã rõ! Bạn cần tôi hỗ trợ thêm gì không?"


def resolve_grounded_context(
    decision: RouteDecision,
    raw_text: str,
    context: GatewayUserContext,
    session_context: Optional[dict[str, Any]],
) -> tuple[str, Any]:
    """Deterministically resolve a `context` decision against displayed `last_read` records.

    Returns one of:
    - ("reply", grounded_text)
    - ("clarify", (clarification_prompt, candidate_options))
    - ("requery", enriched_search_hint)  # caller must fall through to READ with read_only=True
    """
    spec: ContextLookupSpec = decision.context_lookup or ContextLookupSpec()

    # 1. Live-status questions MUST re-read, never serve cached last_read
    if spec.asks_live_status or _LIVE_STATUS_PATTERN.search(raw_text or ""):
        return ("requery", decision.normalized_text)

    valid_lists = _extract_valid_lists(session_context, context)
    if not valid_lists:
        return ("requery", decision.normalized_text)

    # 2. Pick target category (ask to clarify if 2+ ambiguous lists exist)
    chosen_cat: Optional[str] = None
    if spec.category:
        for cat in valid_lists:
            if cat.lower() == spec.category.lower() or spec.category.lower() in cat.lower():
                chosen_cat = cat
                break
    if not chosen_cat:
        if len(valid_lists) == 1:
            chosen_cat = next(iter(valid_lists))
        else:
            # Try narrowing by router domains
            domain_vals = {d.value for d in decision.domains}
            matched_cats = [
                cat for cat in valid_lists
                if any(dom in cat.lower() for dom in domain_vals)
            ]
            if len(matched_cats) == 1:
                chosen_cat = matched_cats[0]
            else:
                options = [f"Danh sách {cat} ({len(recs)} mục)" for cat, recs in list(valid_lists.items())[:3]]
                return (
                    "clarify",
                    (
                        "Trong phiên hiện tại có nhiều danh sách vừa xem. Bạn đang hỏi về danh sách nào?",
                        options,
                    ),
                )

    records = valid_lists[chosen_cat]

    # 3. Resolve exact item by 1-based displayed order
    idx = spec.item_index
    if idx is None:
        if len(records) == 1:
            idx = 1
        else:
            # Summarize top displayed records deterministically
            lines = [f"Danh sách {chosen_cat} vừa xem ({len(records)} mục):"]
            for i, rec in enumerate(records[:5], 1):
                lines.append(f"{i}. {_record_label(rec)}")
            return ("reply", "\n".join(lines))

    if idx < 1 or idx > len(records):
        return (
            "clarify",
            (
                f"Danh sách {chosen_cat} vừa xem chỉ có {len(records)} mục. Bạn muốn xem mục số mấy?",
                [f"{i}. {_record_label(r)}" for i, r in enumerate(records[:3], 1)],
            ),
        )

    record = records[idx - 1]
    label = _record_label(record)

    # 4. Extract specific field if requested
    canonical_field = _canonical_field(spec.field_name)
    if canonical_field:
        candidate_keys = _FIELD_ALIASES.get(canonical_field, (canonical_field,))
        for key in candidate_keys:
            if key in record:
                formatted = _format_field_value(record[key])
                if formatted is not None:
                    field_vi = _FIELD_LABELS_VI.get(canonical_field, canonical_field)
                    return ("reply", f"{field_vi} của {label} (mục #{idx}): {formatted}")
        # Record is identified, but requested field is missing -> re-query that exact record
        rec_id = record.get("id")
        hint = f"{decision.normalized_text} (đối tượng: {label}" + (f", id={rec_id})" if rec_id else ")")
        return ("requery", hint)

    # 5. No specific field -> deterministic summary of displayed fields
    summary_parts = [f"Mục #{idx}: {label}"]
    for cname in ("phone", "email", "address", "amount", "status", "owner", "time"):
        for key in _FIELD_ALIASES[cname]:
            if key in record:
                val = _format_field_value(record[key])
                if val is not None:
                    summary_parts.append(f"• {_FIELD_LABELS_VI[cname]}: {val}")
                    break
    return ("reply", "\n".join(summary_parts))


class TurnRouter:
    """Stage 1 lightweight intent mode classifier with strict timeout & single fallback."""

    def __init__(
        self,
        llm_call: Callable[..., Awaitable[str]],
    ) -> None:
        self._llm_call = llm_call

    def _build_compact_context(
        self,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]],
    ) -> str:
        blocks: list[str] = []
        if not session_context:
            return ""

        history = session_context.get("history") or []
        if history:
            recent = history[-6:]
            lines: list[str] = []
            total = 0
            for msg in reversed(recent):
                line = f"{msg.get('role', 'user')}: {str(msg.get('content', ''))[:400]}"
                if total + len(line) > 2000:
                    break
                lines.insert(0, line)
                total += len(line)
            if lines:
                blocks.append("<recent_history>\n" + "\n".join(lines) + "\n</recent_history>")

        valid_lists = _extract_valid_lists(session_context, context)
        if valid_lists:
            summary: dict[str, Any] = {}
            for cat, recs in valid_lists.items():
                fields = sorted({k for r in recs[:3] for k in r.keys() if not k.startswith("_")})[:10]
                summary[cat] = {
                    "count": len(recs),
                    "top_items": [f"{i}. {_record_label(r)}" for i, r in enumerate(recs[:3], 1)],
                    "fields": fields,
                }
            blocks.append(
                "<recent_read_summary>\n"
                + json.dumps(summary, ensure_ascii=False)
                + "\n</recent_read_summary>"
            )

        active_draft = session_context.get("active_draft")
        if isinstance(active_draft, dict) and active_draft:
            draft_meta = {
                "draft_type": active_draft.get("draft_type"),
                "business_action": active_draft.get("business_action"),
            }
            blocks.append("<active_draft>\n" + json.dumps(draft_meta, ensure_ascii=False) + "\n</active_draft>")

        pending_clarify = session_context.get("pending_clarification")
        if isinstance(pending_clarify, dict) and pending_clarify:
            clarify_meta = {
                "prompt": str(pending_clarify.get("prompt") or "")[:300],
                "missing_fields": pending_clarify.get("missing_fields") or [],
                "candidate_options": pending_clarify.get("candidate_options") or [],
                "tool_name": pending_clarify.get("tool_name"),
            }
            blocks.append(
                "<pending_clarification>\n"
                + json.dumps(clarify_meta, ensure_ascii=False)
                + "\n</pending_clarification>"
            )

        replied_msg = session_context.get("reply_to_text")
        if isinstance(replied_msg, str) and replied_msg.strip():
            blocks.append(f"<replied_message>\n{replied_msg.strip()[:600]}\n</replied_message>")

        return "\n\n".join(blocks)

    async def route(
        self,
        text: str,
        context: GatewayUserContext,
        session_context: Optional[dict[str, Any]] = None,
        *,
        deadline: Optional[float] = None,
    ) -> Optional[RouteDecision]:
        settings = get_settings()
        timeout = float(getattr(settings, "ROUTER_TIMEOUT_SECONDS", 12.0) or 12.0)
        if deadline is not None:
            remaining = deadline - time.time() - 1.0
            if remaining <= 1.0:
                logger.warning("router_skipped reason=deadline_low remaining=%.2f", remaining)
                return None
            timeout = min(timeout, remaining)

        from datetime import datetime
        from zoneinfo import ZoneInfo

        try:
            tz = ZoneInfo(context.timezone or "Asia/Bangkok")
        except Exception:
            tz = ZoneInfo("Asia/Ho_Chi_Minh")
        now = datetime.now(tz)
        weekday_vn = {
            0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm",
            4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật",
        }.get(now.weekday(), "")
        now_str = f"{weekday_vn}, ngày {now.day:02d}/{now.month:02d}/{now.year}, {now.strftime('%H:%M')} ({context.timezone})"
        allowed_domains = ", ".join(_allowed_domains_for_context(context))

        compact_ctx = self._build_compact_context(context, session_context)
        prompt = (
            f"{ROUTER_PROMPT}\n\n"
            f"Thời điểm hiện tại: {now_str}\n"
            f"Domain khả dụng theo quyền tài khoản: {allowed_domains}\n\n"
            f"{compact_ctx}\n\n"
            f"Tin nhắn người dùng: \"{text}\"\n"
            f"Trả về JSON RouteDecision:"
        )
        model_override = getattr(settings, "ROUTER_LLM_MODEL", None)

        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    prompt,
                    response_schema=RouteDecision.model_json_schema(),
                    model_override=model_override,
                    timeout_seconds=timeout,
                ),
                timeout=timeout,
            )
            cleaned = _strip_json_fences(raw)
            decision = RouteDecision.model_validate_json(cleaned)
            # Safety guard 1: explicit negation of a write action must never be routed as prepare_write
            if decision.mode == RouteMode.PREPARE_WRITE and _NEGATION_ACTION_PATTERN.search(text or ""):
                logger.info("router_negation_guard downgraded prepare_write to chitchat")
                return RouteDecision(
                    mode=RouteMode.CHITCHAT,
                    confidence=0.95,
                    normalized_text=decision.normalized_text,
                    domains=[],
                )
            # Safety guard 2: ambiguous standalone "HĐ"/"hd" without disambiguation
            valid_lists = _extract_valid_lists(session_context, context)
            has_anchor = bool(
                valid_lists
                or (session_context or {}).get("reply_to_text")
                or (session_context or {}).get("pending_clarification")
                or (session_context or {}).get("active_draft")
            )
            if (
                decision.mode in (RouteMode.READ, RouteMode.PREPARE_WRITE, RouteMode.CONTEXT)
                and _AMBIGUOUS_HD_PATTERN.search(text or "")
                and not _DISAMBIGUATED_HD_PATTERN.search(text or "")
                and not has_anchor
            ):
                logger.info("router_ambiguity_guard caught standalone HD abbreviation")
                return RouteDecision(
                    mode=RouteMode.CLARIFY,
                    confidence=0.45,
                    normalized_text=decision.normalized_text,
                    domains=decision.domains,
                    clarify_reason="Bạn đang muốn tra cứu hợp đồng hay hóa đơn?",
                    clarify_options=["Tra cứu hợp đồng", "Tra cứu hóa đơn"],
                )
            # Safety guard 3: blind deictic pronoun ("cái đó", "đơn kia"...) with zero context anchor
            if (
                decision.mode in (RouteMode.READ, RouteMode.PREPARE_WRITE, RouteMode.CONTEXT)
                and not has_anchor
                and _BLIND_DEICTIC_PATTERN.search(text or "")
                and not _EXPLICIT_ENTITY_PATTERN.search(text or "")
            ):
                logger.info("router_deictic_guard caught unresolved pronoun without context")
                return RouteDecision(
                    mode=RouteMode.CLARIFY,
                    confidence=0.45,
                    normalized_text=decision.normalized_text,
                    domains=decision.domains,
                    clarify_reason=decision.clarify_reason or "Chưa rõ bạn đang nhắc đến bản ghi hay nội dung nào (chưa có dữ liệu vừa xem trong phiên). Bạn nêu rõ tên/mã hoặc reply trực tiếp vào tin nhắn liên quan giúp tôi nhé?",
                    clarify_options=decision.clarify_options or ["Tra cứu dữ liệu Odoo", "Xem lịch hoặc email"],
                )
            return decision
        except Exception as exc:
            logger.warning("router_fallback reason=%s detail=%s", type(exc).__name__, exc)
            return None
