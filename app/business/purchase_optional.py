"""Optional Purchase RFQ fields step between required facts (supplier, product, qty) and confirmation preview."""
from __future__ import annotations

import copy
import re
import time
from typing import Any

from app.business.client import request
from app.business.purchase_local import (
    fold_vi,
    is_cancel_draft_phrase,
    parse_price_value,
    parse_purchase_date,
    payment_term_candidates,
)
from app.business.workflow import session
from app.services.confirmation_service import OptionButtonsMessage
from app.services.conversation_history_store import conversation_history_store as store

FIELDS: tuple[tuple[str, str, str], ...] = (
    ("partner_ref", "Mã nhà cung cấp", "text"),
    ("currency_id", "Tiền tệ", "lookup:currencies"),
    ("date_order", "Hạn đặt hàng", "datetime"),
    ("date_planned", "Ngày nhận hàng dự kiến", "datetime"),
    ("picking_type_id", "Giao hàng đến", "lookup:picking_types"),
    ("payment_term_id", "Điều khoản thanh toán", "lookup:payment_terms"),
    ("price_unit", "Đơn giá", "price"),
    ("tax_ids", "Thuế", "lookup:taxes"),
    ("notes", "Ghi chú", "text"),
)
BY_FIELD: dict[str, tuple[str, str]] = {f: (label, kind) for f, label, kind in FIELDS}

_SKIP_MENU_PHRASES = {
    "0", "bo qua", "khong", "khong can", "khong bo sung", "mac dinh",
    "dung mac dinh", "tiep tuc", "xem truoc", "ok", "skip",
}
_SKIP_FIELD_PHRASES = {"bo qua", "skip", "giu mac dinh", "mac dinh"}


def is_active(draft: dict[str, Any] | None) -> bool:
    if not draft or draft.get("business_action") not in (
        "business.purchases.create",
        "business.purchases.update",
        "business.purchases.feedback",
    ):
        return False
    opt = draft.get("optional_fields") or {}
    return opt.get("stage") in ("menu", "collect", "feedback")


def menu_text(order_name: str | None = None) -> str:
    if order_name:
        lines = [
            f"Bạn muốn chỉnh sửa trường nào trên đơn mua {order_name}?",
            "",
        ]
        fields_to_show = [item for item in FIELDS if item[0] not in ("price_unit", "tax_ids")]
        for idx, (_, label, _) in enumerate(fields_to_show, 1):
            lines.append(f"{idx}. {label}")
        lines.extend([
            "",
            "Nhập số mục muốn sửa (ví dụ: 1, 2, 4), hoặc nhập “Hủy” để giữ nguyên đơn mua.",
        ])
        return "\n".join(lines)
    lines = [
        "Đã đủ thông tin bắt buộc (Nhà cung cấp, Sản phẩm, Số lượng).",
        "Bạn có muốn nhập hoặc chọn thêm trường nào trước khi xem trước đơn mua không?",
        "",
    ]
    for idx, (_, label, _) in enumerate(FIELDS, 1):
        lines.append(f"{idx}. {label}")
    lines.extend([
        "",
        "Nhập số mục muốn bổ sung (ví dụ: 1, 2, 4).",
        "Nhập 0 hoặc “Bỏ qua” để dùng mặc định Odoo và xem trước đơn mua.",
    ])
    return "\n".join(lines)


def _save_draft(
    context,
    old_draft: dict[str, Any],
    payload: dict[str, Any],
    opt: dict[str, Any],
    *,
    business_action: str | None = None,
) -> dict[str, Any] | None:
    action_name = business_action or old_draft.get("business_action") or "business.purchases.create"
    draft = {
        "draft_type": "business",
        "business_action": action_name,
        "business_data": payload,
        "optional_fields": opt,
        "draft_id": old_draft.get("draft_id"),
        "version": 1,
        "updated_at": time.time(),
    }
    return store.set_active_draft(
        context.tenant,
        context.user_id,
        context.chat_id,
        context.telegram_binding_id,
        draft,
        database=context.database,
        expected_version=old_draft.get("version"),
        expected_draft_id=old_draft.get("draft_id"),
    )


def _parse_menu_selection(text: str, *, edit_mode: bool = False) -> list[str]:
    clean = (text or "").strip()
    folded = fold_vi(clean)
    pool = [item for item in FIELDS if item[0] not in ("price_unit", "tax_ids")] if edit_mode else list(FIELDS)
    selected: list[str] = []
    nums = re.findall(r"\b(\d+)\b", clean)
    for raw in nums:
        idx = int(raw)
        if 1 <= idx <= len(pool):
            field = pool[idx - 1][0]
            if field not in selected:
                selected.append(field)
    if not selected:
        for field, label, _ in pool:
            if fold_vi(label) in folded and field not in selected:
                selected.append(field)
    return selected


async def _fetch_lookup_options(context, lookup_kind: str, company_id: int | None, query: str = "") -> list[dict[str, Any]]:
    body: dict[str, Any] = {"kind": lookup_kind, "query": query}
    if company_id:
        body["company_id"] = company_id
    res = await request(context, "POST", "business/lookup", body)
    if res.get("status") != "ok":
        return []
    items = res.get("items") or []
    if lookup_kind == "picking_types":
        incoming = [r for r in items if r.get("code") == "incoming"]
        if incoming:
            items = incoming
    options: list[dict[str, Any]] = []
    for row in items[:10]:
        label = str(row.get("display_name") or row.get("name") or "").strip()
        if row.get("id") and label:
            options.append({"id": row["id"], "label": label})
    return options


async def _prompt_field(context, field: str, company_id: int | None) -> tuple[Any, list[dict[str, Any]]]:
    label, kind = BY_FIELD[field]
    if kind.startswith("lookup:"):
        lookup_kind = kind.split(":", 1)[1]
        options = await _fetch_lookup_options(context, lookup_kind, company_id)
        extra_zero = "\n0. Không thuế" if field == "tax_ids" else ""
        if options:
            listing = "\n".join(f"{i}. {opt['label']}" for i, opt in enumerate(options, 1))
            prompt = f"{label}: chọn theo số thứ tự hoặc nhập tên từ Odoo:{extra_zero}\n{listing}\nCó thể nhập “Bỏ qua”."
            if len(options) <= 6 and field != "tax_ids":
                return OptionButtonsMessage(prompt, [opt["label"] for opt in options]), options
            return prompt, options
        return f"{label}: nhập tên cần tìm trên Odoo, hoặc nhập “Bỏ qua”.", []
    if kind == "datetime":
        return f"{label}: nhập ngày theo định dạng DD/MM/YYYY (vd: 30/12/2026 hoặc 30/12/2026 14:30), hoặc nhập “Bỏ qua”.", []
    if kind == "price":
        return f"{label}: nhập đơn giá (vd: 500000, 1.5tr), hoặc nhập “Bỏ qua”.", []
    return f"{label}: nhập nội dung muốn điền, hoặc nhập “Bỏ qua”.", []


async def open_menu(context, old_draft: dict[str, Any], payload: dict[str, Any]) -> list[Any]:
    opt = {"stage": "menu", "queue": [], "current": None, "options": []}
    _save_draft(context, old_draft, payload, opt, business_action="business.purchases.create")
    return [menu_text()]


async def open_edit_menu(
    context,
    *,
    order_id: int,
    order_name: str,
    company_id: int | None = None,
) -> list[Any]:
    payload: dict[str, Any] = {"target_id": order_id, "values": {}}
    if company_id:
        payload["company_id"] = company_id
    opt = {"stage": "menu", "queue": [], "current": None, "options": [], "order_name": order_name}
    _save_draft(context, {}, payload, opt, business_action="business.purchases.update")
    return [menu_text(order_name=order_name)]


def open_feedback_prompt(context, *, token: str, order_name: str) -> list[Any]:
    opt = {"stage": "feedback", "token": token, "order_name": order_name}
    _save_draft(context, {}, {}, opt, business_action="business.purchases.feedback")
    return [f"Nhập nội dung yêu cầu chỉnh sửa cho đơn mua {order_name} (hoặc nhắn “Hủy” để bỏ qua):"]


async def handle_reply(text: str, context) -> list[Any]:
    from app.business.operations import prepare, read

    draft = session(context).get("active_draft") or {}
    if not is_active(draft):
        return ["Chưa có bước chọn trường tùy chọn đang mở cho đơn mua."]

    business_action = draft.get("business_action") or "business.purchases.create"
    edit_mode = business_action == "business.purchases.update"
    clean = (text or "").strip()
    opt = copy.deepcopy(draft.get("optional_fields") or {})

    if opt.get("stage") == "feedback":
        order_name = opt.get("order_name") or "đơn mua"
        if is_cancel_draft_phrase(clean) or fold_vi(clean).rstrip(".!?") in {"huy", "cancel", "bo qua"}:
            store.clear_active_draft(
                context.tenant, context.user_id, context.chat_id, context.telegram_binding_id,
                database=context.database,
            )
            return [f"Đã hủy nhập góp ý chỉnh sửa cho đơn mua {order_name}."]
        res = await request(
            context, "POST", "business/purchases/approval-feedback",
            {"token": opt.get("token", ""), "feedback": clean},
        )
        store.clear_active_draft(
            context.tenant, context.user_id, context.chat_id, context.telegram_binding_id,
            database=context.database,
        )
        return [str(res.get("message") or f"Đã gửi yêu cầu chỉnh sửa đơn mua {order_name}.")]

    if is_cancel_draft_phrase(clean):
        return await read("business.draft", {"action": "cancel", "business_action": business_action}, context)

    payload = copy.deepcopy(draft.get("business_data") or {})
    company_id = payload.get("company_id")
    folded = fold_vi(clean).rstrip(".!?")

    if opt.get("stage") == "menu":
        if folded in _SKIP_MENU_PHRASES:
            if edit_mode:
                return await read("business.draft", {"action": "cancel", "business_action": business_action}, context)
            opt = {"stage": "done", "queue": [], "current": None, "options": []}
            _save_draft(context, draft, payload, opt)
            return await prepare(context)
        selected = _parse_menu_selection(clean, edit_mode=edit_mode)
        if not selected:
            max_idx = 7 if edit_mode else len(FIELDS)
            return [f"Nhập số mục từ 1 đến {max_idx} (ví dụ: 1, 2, 4), hoặc nhập 0 / “Hủy” để bỏ qua."]
        first, rest = selected[0], selected[1:]
        prompt, options = await _prompt_field(context, first, company_id)
        opt = {"stage": "collect", "queue": rest, "current": first, "options": options, "order_name": opt.get("order_name")}
        _save_draft(context, draft, payload, opt)
        return [prompt]

    # stage == "collect"
    field = opt.get("current")
    if field not in BY_FIELD:
        opt = {"stage": "done", "queue": [], "current": None, "options": []}
        _save_draft(context, draft, payload, opt)
        return await prepare(context)

    label, kind = BY_FIELD[field]
    notice = ""

    if folded in _SKIP_FIELD_PHRASES:
        notice = f"Đã bỏ qua {label}. "
    else:
        values = dict(payload.get("values") or {})
        lines = copy.deepcopy(payload.get("lines") or [])

        if kind == "text":
            values[field] = clean
            payload["values"] = values
        elif kind == "datetime":
            parsed = parse_purchase_date(clean)
            if not parsed:
                return [f"{label} chưa đúng định dạng. Vui lòng nhập DD/MM/YYYY (vd: 30/12/2026) hoặc nhập “Bỏ qua”."]
            if field == "date_planned":
                values["date_planned"] = parsed
                for ln in lines:
                    ln["date_planned"] = parsed
                payload["lines"] = lines
            else:
                values[field] = parsed
            payload["values"] = values
        elif kind == "price":
            val = parse_price_value(clean)
            if val is None:
                return [f"{label} không hợp lệ. Vui lòng nhập số (vd: 500000 hoặc 1.5tr) hoặc nhập “Bỏ qua”."]
            for ln in lines:
                ln["price_unit"] = val
            payload["lines"] = lines
        elif kind.startswith("lookup:"):
            lookup_kind = kind.split(":", 1)[1]
            cached_options: list[dict[str, Any]] = opt.get("options") or []

            if field == "tax_ids" and folded in {"0", "khong thue", "khong tinh thue", "khong"}:
                for ln in lines:
                    ln["tax_ids"] = []
                payload["lines"] = lines
            else:
                chosen: dict[str, Any] | None = None
                if re.fullmatch(r"\d+", clean):
                    idx = int(clean)
                    if 1 <= idx <= len(cached_options):
                        chosen = cached_options[idx - 1]
                if not chosen:
                    exact = [o for o in cached_options if fold_vi(o["label"]) == folded]
                    if len(exact) == 1:
                        chosen = exact[0]
                if not chosen:
                    partial = [o for o in cached_options if folded and folded in fold_vi(o["label"])]
                    if len(partial) == 1:
                        chosen = partial[0]
                if not chosen:
                    queries = payment_term_candidates(clean) if field == "payment_term_id" else [clean]
                    found: list[dict[str, Any]] = []
                    for q in queries:
                        found = await _fetch_lookup_options(context, lookup_kind, company_id, query=q)
                        if found:
                            break
                    if len(found) == 1:
                        chosen = found[0]
                    elif len(found) > 1:
                        opt["options"] = found
                        _save_draft(context, draft, payload, opt)
                        listing = "\n".join(f"{i}. {o['label']}" for i, o in enumerate(found, 1))
                        return [f"Có nhiều kết quả cho {label}, chọn số thứ tự:\n{listing}"]
                    else:
                        return [f"Không tìm thấy {label} “{clean}” trên Odoo. Hãy nhập lại tên khác hoặc nhập “Bỏ qua”."]

                if field == "tax_ids":
                    for ln in lines:
                        ln["tax_ids"] = [chosen["id"]]
                    payload["lines"] = lines
                else:
                    values[field] = chosen["id"]
                    payload["values"] = values

    queue = list(opt.get("queue") or [])
    if queue:
        nxt, rest = queue[0], queue[1:]
        prompt, options = await _prompt_field(context, nxt, company_id)
        opt = {"stage": "collect", "queue": rest, "current": nxt, "options": options}
        _save_draft(context, draft, payload, opt)
        if notice and isinstance(prompt, str):
            return [notice + "\n" + prompt]
        if notice and isinstance(prompt, OptionButtonsMessage):
            return [OptionButtonsMessage(notice + "\n" + prompt.text, prompt.options)]
        return [prompt]

    opt = {"stage": "done", "queue": [], "current": None, "options": []}
    _save_draft(context, draft, payload, opt)
    if edit_mode and not payload.get("values"):
        await read("business.draft", {"action": "cancel", "business_action": business_action}, context)
        return ["Chưa có trường nào thay đổi. Đã giữ nguyên đơn mua trên Odoo."]
    return await prepare(context)
