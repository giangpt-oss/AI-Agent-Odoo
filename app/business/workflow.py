"""Durable conversation drafts; all Odoo business validation remains at Gateway."""
import copy
import hashlib
import json
import secrets
import time
from typing import Any

from app.business.client import request
from app.services.confirmation_service import confirmation_service
from app.services.conversation_history_store import conversation_history_store as store


def session(context):
    return store.get_session(context.tenant, context.user_id, context.chat_id,
                             context.telegram_binding_id, database=context.database)


def remember(context, key: str, value: Any) -> None:
    store.set_last_read_data(context.tenant, context.user_id, context.chat_id,
                             context.telegram_binding_id, key, value, database=context.database)


def display(data: dict) -> list[str]:
    if data.get("status") in ("error", "failed", "PERMISSION_DENIED", "SERVICE_ERROR"):
        return ["⚠️ " + str(data.get("message") or data.get("error") or "Không thể hoàn tất yêu cầu.")]
    return [json.dumps(data, ensure_ascii=False, default=str)[:14000]]


def quote_preview(preview: dict, payload: dict | None = None) -> str:
    payload = payload or {}

    def render(value):
        if value is None or value == '':
            return ''
        if isinstance(value, bool):
            return 'Có' if value else 'Không'
        return str(value)

    fields = preview.get('fields')
    if fields is None:
        # Compatibility with validation jobs prepared before the new preview contract.
        labels = {'partner_id': 'Khách hàng', 'validity_date': 'Ngày hết hạn', 'payment_term_id': 'Điều khoản thanh toán',
            'note': 'Điều khoản và ghi chú', 'origin': 'Chứng từ gốc', 'client_order_ref': 'Tham chiếu khách hàng'}
        fields = [{'name': name, 'label': label, 'value': preview.get('customer') if name == 'partner_id' else payload.get(name)} for name, label in labels.items()]
    lines = ['Thông tin báo giá:']
    for field in fields:
        source = ' (theo tài khoản Odoo)' if field.get('source') == 'context' else ''
        lines.append(f"{field['label']}{source}: {render(field.get('value'))}")
    for index, row in enumerate(preview.get('lines') or [], 1):
        supplied = (payload.get('lines') or [])[index - 1] if index <= len(payload.get('lines') or []) else {}
        taxes = row.get('tax_ids')
        tax_text = None if taxes is None else ', '.join(row.get('tax_labels') or map(str, taxes)) if taxes else 'Không thuế'
        values = {'Sản phẩm': row.get('product'), 'Mô tả': row.get('name') or supplied.get('name'), 'Số lượng': row.get('quantity'),
            'Đơn vị tính': row.get('uom_name') or row.get('uom_id'), 'Đơn giá': row.get('price_unit'), 'Thuế': tax_text,
            'Chiết khấu': supplied.get('discount'), 'Thời gian giao hàng (số ngày)': supplied.get('customer_lead'), 'Thành tiền': None}
        lines.append(f'\nDòng {index}:')
        lines.extend(f'{label}: {render(value)}' for label, value in values.items())
        for field in row.get('custom_fields') or []:
            lines.append(f"{field['label']}: {render(field.get('value'))}")
    lines.extend(['\nTiền trước thuế: ', 'Tiền thuế: ', 'Tổng tiền: '])
    lines.append(preview.get("notice") or "Tổng tiền chính thức do Odoo tính sau khi tạo báo giá nháp.")
    return "\n".join(lines)


def missing_prompt(data: dict) -> str:
    missing = data.get("missing_fields") or []
    labels = {"partner_id": "khách hàng", "lines": "ít nhất một dòng sản phẩm",
              "quantity": "số lượng", "product_id": "sản phẩm", "price_unit_confirmed": "đơn giá bạn chốt",
              "tax_ids": "thuế áp dụng (hoặc xác nhận không thuế)", "uom_id": "đơn vị tính",
              "pricelist_id": "bảng giá/tiền tệ", "currency_id": "tiền tệ", "payment_term_id": "điều khoản thanh toán",
              "partner_invoice_id": "địa chỉ xuất hóa đơn", "partner_shipping_id": "địa chỉ giao hàng", "customer_lead": "thời gian giao hàng tính bằng số ngày (không phải ngày lịch)"}
    prompts = []
    metadata = data.get("metadata", {}).get("models", {})
    for name in missing:
        parts = name.split(".")
        field = parts[-1]
        model = "sale.order.line" if parts[0] == "lines" and len(parts) > 1 else "sale.order"
        label = labels.get(field) or metadata.get(model, {}).get("fields", {}).get(field, {}).get("string") or field
        prefix = f"Dòng {int(parts[1]) + 1}: " if model == "sale.order.line" and parts[1].isdigit() else ""
        prompts.append(prefix + label)
    return "Vui lòng bổ sung: " + "; ".join(prompts) + "." if prompts else "Đã đủ thông tin. Bạn muốn xem trước báo giá không?"


async def read(tool: str, params: dict, context) -> list[str]:
    from app.core.config import get_settings
    settings = get_settings()
    enabled = settings.ENABLE_REPORTING if tool.startswith("reports.") else settings.ENABLE_QUOTATIONS
    if not enabled:
        return ["Chức năng này chưa được bật trong cấu hình Agent."]
    if tool.startswith('reports.'):
        from app.business.tools import register
        from app.domain.tool_registry import ToolRegistry
        report_registry = ToolRegistry()
        register(report_registry, reporting=True, quotation=False)
        definition = report_registry.get_tool(tool)
        if not definition or set(params) - set(definition.parameter_schema.get('properties', {})):
            return ['Yêu cầu có chỉ tiêu hoặc bộ lọc báo cáo chưa hỗ trợ. Tôi chưa tạo hay gửi lại báo cáo để tránh bỏ sót yêu cầu. Bạn muốn báo cáo những chỉ tiêu nào trong bán hàng, mua hàng, CRM, liên hệ hoặc tồn kho?']
    if tool == "sales.quotation.collect":
        from app.business.quote_collector import collect
        return await collect(params, context)
    if tool == "sales.quotation.status":
        if params.get('quotation_name'):
            from app.business.quote_status import status_by_name
            return [await status_by_name(context, params['quotation_name'], params.get('company_id'))]
        return await prepare(context, status_only=True)
    if tool == "reports.capabilities":
        return display(await request(context, "GET", "odoo/capabilities"))
    if tool in ("reports.lookup", "sales.quotation.lookup"):
        prefix = "reports" if tool.startswith("reports.") else "quotations"
        data = await request(context, "GET", f"{prefix}/lookup", params)
        if data.get("status") not in ("error", "failed", "PERMISSION_DENIED", "SERVICE_ERROR"):
            candidate = {"kind": params.get("kind"), "data": data, "observed_at": time.time()}
            remember(context, "business_candidates", candidate)
            remember(context, f"business_candidates_{params.get('kind')}", candidate)
        if prefix == "quotations" and isinstance(data.get("items"), list):
            if not data["items"]:
                return ["Không tìm thấy kết quả phù hợp. Hãy thử tên hoặc mã khác."]
            lines = ["Các kết quả có thể chọn:"]
            for row in data["items"]:
                extra = []
                for field, label in (("default_code", "mã"), ("list_price", "giá niêm yết"), ("amount", "thuế"), ("currency_id", "tiền tệ")):
                    if row.get(field) is not None:
                        extra.append(f"{label}: {row[field]}")
                suffix = f" ({'; '.join(extra)})" if extra else ""
                lines.append(f"ID {row['id']}: {row.get('display_name') or row.get('name') or row['id']}{suffix}")
            lines.append("Vui lòng chọn ID hoặc tên cụ thể từ danh sách.")
            return ["\n".join(lines)]
        return display(data)
    if tool.startswith("reports."):
        state = session(context).get("last_read") or {}
        previous = state.get("business_report") or {}
        if tool == "reports.create":
            params = dict(params)
            reuse_previous = params.pop("reuse_previous", False)
            previous_request = previous.get("request", {}) if not previous.get("report_id") or reuse_previous else {}
            if params.get("report_type") and params["report_type"] != previous_request.get("report_type"):
                previous_request = {k: v for k, v in previous_request.items() if k == "company_ids"}
            values = {**previous_request, **params}
            if previous.get("report_id"):
                values["output_format"] = params.get("output_format", "auto")
                values["report_purpose"] = params.get("report_purpose", "auto")
            if values.get("report_type") == "inventory" and not (params.get("date_from") or params.get("date_to")):
                values.pop("date_from", None)
                values.pop("date_to", None)
            if not values.get("report_type"):
                return ["Bạn muốn báo cáo bán hàng/báo giá, mua hàng/RFQ, CRM, liên hệ hay tồn kho/nhập xuất?"]
            if values["report_type"] not in {"inventory", "crm", "contacts"} and not (values.get("date_from") and values.get("date_to")):
                remember(context, "business_report", {"request": values})
                return ["Bạn muốn báo cáo từ ngày nào đến ngày nào? Ví dụ: 01/10/2026–31/10/2026."]
            values["timezone"] = context.timezone
            data = await request(context, "POST", "reports", values)
            report_id = data.get("report_id") or data.get("id")
            if report_id:
                remember(context, "business_report", {"request": values, "report_id": report_id})
                return [str(data.get("message") or f"Đã tiếp nhận báo cáo {report_id}.") + "\nTôi sẽ thông báo khi có kết quả. Bạn có thể hỏi tiến độ báo cáo bất cứ lúc nào."]
            return display(data)
        report_id = params.get("report_id") or previous.get("report_id")
        if not report_id or not str(report_id).replace("_", "").replace("-", "").isalnum():
            return ["Vui lòng cung cấp mã báo cáo hợp lệ."]
        if tool == "reports.file":
            data = await request(context, "POST", f"reports/{report_id}/deliver", {k: v for k, v in params.items() if k != "report_id"})
            return [str(data.get("message") or display(data)[0])]
        if tool == "reports.cancel":
            return display(await request(context, "POST", f"reports/{report_id}/cancel"))
        data = await request(context, "GET", f"reports/{report_id}")
        return [f"Báo cáo {report_id}: {data.get('status', 'không rõ')}.\n{data.get('message', '')}\nGiao file: {data.get('delivery_status', 'chưa có')}."] if data.get("status") != "error" else display(data)
    return await edit_quote(params, context)


async def edit_quote(params: dict, context) -> list[str]:
    action = params.get("action", "update")
    current = session(context).get("active_draft")
    if current and current.get("draft_type") != "quotation" and action != "start":
        return ["Chưa có nháp báo giá. Bạn muốn lập báo giá cho khách hàng nào?"]
    if action == "cancel":
        if current and current.get("draft_type") == "quotation":
            confirmation_service.cancel_pending_draft_confirmations(user_id=context.user_id,
                chat_id=context.chat_id, telegram_binding_id=context.telegram_binding_id or "default", draft_id=current["draft_id"])
            store.clear_active_draft(context.tenant, context.user_id, context.chat_id,
                                      context.telegram_binding_id, database=context.database)
        return ["Đã hủy nháp báo giá cục bộ."]
    if action not in ("start", "update", "add_line", "remove_line"):
        return ["Thao tác nháp không hợp lệ."]
    old = current if current and current.get("draft_type") == "quotation" else None
    payload = copy.deepcopy(old.get("quotation_data", {})) if old and action != "start" else {}
    incoming = copy.deepcopy(params.get("data") or {})
    if not isinstance(incoming, dict):
        return ["Thông tin báo giá phải là một object gồm các trường cần sửa."]
    allowed = {"partner_id", "company_id", "partner_invoice_id", "partner_shipping_id", "currency_id", "pricelist_id", "lines", "validity_date", "payment_term_id", "fiscal_position_id", "client_order_ref", "note", "custom_fields"}
    if set(incoming) - allowed:
        return [f"Trường báo giá chưa hỗ trợ: {', '.join(sorted(set(incoming) - allowed))}"]
    # Application validates that selected partners/products came from a real recent lookup.
    recent = session(context).get("last_read") or {}

    def selected(kind, record_id):
        candidate = recent.get(f"business_candidates_{kind}") or recent.get("business_candidates") or {}
        if candidate.get("kind") != kind or time.time() - candidate.get("observed_at", 0) > 1800:
            return False
        records = (candidate.get("data") or {}).get("items") or []
        return record_id in {row.get("id") for row in records if isinstance(row, dict)}

    if incoming.get("partner_id") and incoming["partner_id"] != payload.get("partner_id"):
        if not selected("partners", incoming["partner_id"]):
            return ["Bạn cho tôi tên hoặc mã khách hàng để tra cứu và chọn đúng bản ghi trước nhé."]
    changed_customer = incoming.get("partner_id") and incoming["partner_id"] != payload.get("partner_id")
    changed_company = incoming.get("company_id") and incoming["company_id"] != payload.get("company_id")
    if changed_customer or changed_company:
        for line in payload.get("lines", []):
            line.pop("tax_ids", None)
            line.pop("use_catalog_price", None)
            line["price_confirmed"] = False
        for field in ("partner_invoice_id", "partner_shipping_id", "pricelist_id", "currency_id", "payment_term_id", "fiscal_position_id"):
            payload.pop(field, None)
    changes = incoming.pop("lines", None)
    lines = payload.setdefault("lines", [])
    if "line_index" in params and (isinstance(params["line_index"], bool) or not isinstance(params["line_index"], int)):
        return ["Số thứ tự dòng hàng phải là số nguyên bắt đầu từ 1."]
    if action == "remove_line":
        index = int(params.get("line_index") or 0) - 1
        if index < 0 or index >= len(lines):
            return ["Số thứ tự dòng hàng không hợp lệ."]
        lines.pop(index)
    if changes is not None:
        if not isinstance(changes, list) or not all(isinstance(x, dict) for x in changes):
            return ["Danh sách dòng hàng không hợp lệ."]
        if "line_index" in params and action != "add_line":
            if len(changes) != 1 or not 1 <= params["line_index"] <= len(lines):
                return ["Khi chọn số thứ tự dòng, hãy sửa đúng một dòng hiện có."]
        for index, change in enumerate(changes):
            line_index = int(params.get("line_index", index + 1)) - 1
            previous = lines[line_index] if action != "add_line" and 0 <= line_index < len(lines) else {}
            if change.get("product_id") and change["product_id"] != previous.get("product_id"):
                if not selected("products", change["product_id"]):
                    return ["Bạn cho tôi tên hoặc mã sản phẩm để tra cứu và chọn đúng bản ghi trước nhé."]
                for dependent in ("tax_ids", "uom_id", "price_unit", "price_confirmed", "use_catalog_price", "name"):
                    previous.pop(dependent, None)
            if "price_unit" in change and "price_confirmed" not in change:
                change["price_confirmed"] = False
            if action != "add_line" and 0 <= line_index < len(lines):
                if isinstance(change.get("custom_fields"), dict):
                    change["custom_fields"] = {**previous.get("custom_fields", {}), **change["custom_fields"]}
                previous.update(change)
            else:
                lines.append(change)
    if isinstance(incoming.get("custom_fields"), dict):
        incoming["custom_fields"] = {**payload.get("custom_fields", {}), **incoming["custom_fields"]}
    payload.update(incoming)
    draft = {"draft_type": "quotation", "draft_id": old["draft_id"] if old and action != "start" else "quote_" + secrets.token_hex(8),
             "version": 1, "content": "Báo giá nháp", "quotation_data": payload}
    saved = store.set_active_draft(context.tenant, context.user_id, context.chat_id,
        context.telegram_binding_id, draft, database=context.database,
        expected_version=current.get("version") if current else None,
        expected_draft_id=current.get("draft_id") if current else None)
    if not saved:
        return ["Nháp đã thay đổi trong lúc xử lý. Vui lòng xem lại nháp mới nhất."]
    if old:
        confirmation_service.cancel_pending_draft_confirmations(user_id=context.user_id,
            chat_id=context.chat_id, telegram_binding_id=context.telegram_binding_id or "default", draft_id=old["draft_id"])
    from app.business.quote_collector import state_prompt
    return [state_prompt(saved)]


async def prepare(context, status_only=False) -> list[str]:
    from app.core.config import get_settings
    if not get_settings().ENABLE_QUOTATIONS:
        return ["Chức năng báo giá chưa được bật trong cấu hình Agent."]
    draft = session(context).get("active_draft") or {}
    if draft.get("draft_type") != "quotation":
        return ["Chưa có nháp báo giá. Bạn muốn lập báo giá cho khách hàng nào?"]
    from app.business.quote_status import status_for
    lifecycle = await status_for(context, draft, confirmation_service)
    latest = confirmation_service.latest_draft_confirmation(user_id=context.user_id, chat_id=context.chat_id,
        telegram_binding_id=context.telegram_binding_id or 'default', draft_id=draft['draft_id'])
    if lifecycle and (status_only or (latest and latest['status'] in ('completed', 'executing', 'unknown_result'))):
        return [lifecycle]
    payload = draft["quotation_data"]
    fingerprint = hashlib.sha256(('explicit_fields_v2:' + json.dumps(payload, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
    last = (session(context).get("last_read") or {}).get("quotation_preparation_job") or {}
    same = last.get("draft_id") == draft["draft_id"] and last.get("draft_version") == draft["version"] and last.get("fingerprint") == fingerprint
    if not same:
        if status_only:
            from app.business.quote_collector import state_prompt
            return [state_prompt(draft) + '\nBạn muốn xem trước báo giá không?']
        job = await request(context, "POST", "quotations/prepare-jobs", {"payload": payload})
        if not job.get("job_id"):
            return display(job)
        remember(context, "quotation_preparation_job", {"job_id": job["job_id"], "draft_id": draft["draft_id"], "draft_version": draft["version"], "fingerprint": fingerprint})
        return ["Trạng thái: Đang kiểm tra báo giá trên Odoo. Chưa tạo chứng từ. Tôi sẽ thông báo khi xong; bạn có thể hỏi tiến độ bất cứ lúc nào. Không cần nhập lại thông tin."]
    job = await request(context, "GET", "quotations/prepare-jobs/" + last["job_id"])
    if job.get("status") in ("queued", "running"):
        return ["Nháp vẫn đang được kiểm tra trên Odoo. Thông tin đã lưu; tôi sẽ thông báo khi xong. Bạn có thể hỏi tiến độ để xem lại trạng thái."]
    data = job.get("result") or job

    remember(context, "quotation_validation", data)
    if data.get("status") != "ready":
        if data.get("status") == "needs_information":
            return ['Trạng thái: Chờ bổ sung thông tin.\n' + quote_preview(data.get('preview') or {}, data.get('payload')) + '\n' + missing_prompt(data)]
        return ["Trạng thái: Lỗi kiểm tra báo giá.\n" + display(data)[0] + '\nNháp được giữ. Bạn có thể hỏi tiến độ hoặc sửa thông tin rồi yêu cầu kiểm tra lại.']
    preview_text = quote_preview(data.get("preview") or {}, data["payload"])
    if len(preview_text) > 18000:
        return ["Bản xem trước quá dài để kiểm tra đầy đủ trong chat. Hãy giảm số dòng hoặc rút ngắn ghi chú/trường tùy chỉnh; chưa tạo mã xác nhận."]
    confirmation_service.cancel_pending_draft_confirmations(user_id=context.user_id, chat_id=context.chat_id, telegram_binding_id=context.telegram_binding_id or "default", draft_id=draft["draft_id"])
    _, preview = confirmation_service.create_confirmation(user_id=context.user_id,
        chat_id=context.chat_id, telegram_binding_id=context.telegram_binding_id or "default",
        action="sales.quotation.create", arguments=data["payload"],
        preview_text="Trạng thái: Chờ xác nhận tạo báo giá.\nBản xem trước báo giá nháp — tổng tạm tính; Odoo tính tổng chính thức.\n" + preview_text,
        owner_key=context.owner_key, draft_id=draft["draft_id"], draft_version=draft["version"],
        telegram_user_id=context.telegram_user_id)
    return [preview]
