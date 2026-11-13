"""Quote conversations store user facts before resolving Odoo references."""
import copy
import json
import re
import secrets
import time

from app.business.client import request
from app.business.quote_schema import QuoteTurn
from app.business.workflow import (
    confirmation_service, latest_quote_confirmation, remember, resume_quote, session, store,
)


def state_prompt(draft):
    data = draft.get('quotation_data', {})
    hints = draft.get('quotation_hints', {})
    lines = data.get('lines', [])
    missing = []
    if not data.get('partner_id'):
        missing.append('chọn khách hàng từ Odoo' if hints.get('customer_query') else 'tên khách hàng')
    if not data.get('pricelist_id'):
        missing.append('bảng giá')
    if not lines:
        missing.append('tên sản phẩm và số lượng')
    for index, line in enumerate(lines, 1):
        if not line.get('product_id'):
            missing.append(f'dòng {index}: tên hoặc mã sản phẩm')
    if missing:
        return 'Cần bổ sung: ' + ', '.join(missing) + '.\n' + quote_details(draft)
    return quote_details(draft)


def quote_details(draft):
    data = draft['quotation_data']
    hints = draft.get('quotation_hints', {})
    details = ['Khách hàng: ' + str(hints.get('partner_label') or hints.get('customer_query') or data.get('partner_id') or 'chưa nhập')]
    details.append('Bảng giá: ' + str(hints.get('pricelist_label') or 'chưa chọn'))
    for index, line in enumerate(data.get('lines', []), 1):
        product = hints.get(f'product_label_{index}') or hints.get(f'product_query_{index}') or line.get('product_id') or 'chưa nhập'
        quantity = line.get('quantity', 'theo mặc định Odoo')
        price = 'giá niêm yết Odoo' if line.get('use_catalog_price') else line.get('price_unit', 'theo bảng giá Odoo')
        taxes = line.get('tax_ids')
        tax_label = 'theo Odoo' if taxes is None else ', '.join(map(str, taxes)) if taxes else 'không thuế'
        details.append(f"Dòng {index}: {product} | Số lượng: {quantity} | Đơn giá: {price} | Thuế: {tax_label}")
        if line.get('name'):
            details.append('Mô tả: ' + line['name'])
        if line.get('uom_id'):
            details.append(f"Đơn vị tính (ID): {line['uom_id']}")
        if 'customer_lead' in line:
            details.append(f"Thời gian giao hàng: {line['customer_lead']} ngày.")
        for name, value in line.get('custom_fields', {}).items():
            details.append(f"{name}: {value}")
    labels = {'validity_date': 'Ngày hết hạn báo giá', 'note': 'Ghi chú',
              'client_order_ref': 'Tham chiếu khách hàng', 'partner_invoice_id': 'Địa chỉ hóa đơn (ID)',
              'partner_shipping_id': 'Địa chỉ giao hàng (ID)',
              'payment_term_id': 'Điều khoản thanh toán (ID)', 'fiscal_position_id': 'Vị thế thuế (ID)',
              'currency_id': 'Tiền tệ (ID)', 'origin': 'Chứng từ gốc'}
    details.extend(f"{label}: {data[name]}" for name, label in labels.items() if name in data)
    details.extend(f"{name}: {value}" for name, value in data.get('custom_fields', {}).items())
    return "\n".join(details)


def save_reference(draft, kind, index, row):
    payload, hints = draft['quotation_data'], draft['quotation_hints']
    label = row.get('display_name') or row.get('name') or str(row['id'])
    if kind == 'partners':
        payload['partner_id'] = row['id']
        hints['partner_label'] = label
    elif kind == 'pricelists':
        if payload.get('pricelist_id') and payload['pricelist_id'] != row['id']:
            for line in payload.get('lines', []):
                for field in ('price_unit', 'price_confirmed', 'use_catalog_price'):
                    line.pop(field, None)
        payload['pricelist_id'] = row['id']
        hints['pricelist_label'] = label
        hints.pop('pricelist_lookup_pending', None)
    else:
        payload['lines'][index - 1]['product_id'] = row['id']
        hints[f'product_label_{index}'] = label
    hints.pop('selection', None)


def apply_line_facts(turn, payload, hints):
    facts = turn.model_dump(exclude_none=True, include={
        'quantity', 'price_unit', 'price_confirmed', 'use_catalog_price', 'customer_lead', 'tax_ids', 'uom_id', 'name',
    })
    if turn.product_query is None and not facts and not turn.line_custom_fields:
        return
    while len(payload['lines']) < turn.line_index:
        payload['lines'].append({})
    line = payload['lines'][turn.line_index - 1]
    query_key = f'product_query_{turn.line_index}'
    if turn.product_query is not None:
        if hints.get(query_key) and hints[query_key].casefold() != turn.product_query.casefold():
            for key in ('product_id', 'price_unit', 'price_confirmed', 'use_catalog_price', 'tax_ids', 'uom_id', 'name'):
                line.pop(key, None)
            hints.pop(f'product_label_{turn.line_index}', None)
            selection = hints.get('selection') or {}
            if selection.get('kind') == 'products' and selection.get('line_index') == turn.line_index:
                hints.pop('selection', None)
        hints[query_key] = turn.product_query
    if 'price_unit' in facts and line.get('use_catalog_price') and 'use_catalog_price' not in facts:
        facts['use_catalog_price'] = False
    line.update(facts)
    if turn.line_custom_fields:
        line.setdefault('custom_fields', {}).update(turn.line_custom_fields)


def save(context, draft, previous):
    saved = store.set_active_draft(context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, draft,
        database=context.database, expected_version=previous.get('version') if previous else None,
        expected_draft_id=previous.get('draft_id') if previous else None)
    if saved and previous:
        confirmation_service.cancel_pending_draft_confirmations(user_id=context.user_id, chat_id=context.chat_id,
            telegram_binding_id=context.telegram_binding_id or 'default', draft_id=previous['draft_id'])
    return saved


async def lookup_reference(context, kind, query, payload):
    params = {'kind': kind, 'query': query, **({'company_id': payload['company_id']} if payload.get('company_id') else {})}
    data = await request(context, 'GET', 'quotations/lookup', params)
    if kind == 'pricelists' and data.get('items') == []:
        simplified = re.sub(r'^giá\s+cho\s+', '', query.strip(), flags=re.I)
        simplified = re.sub(r'\s*\([A-Z]{3}\)\s*$', '', simplified, flags=re.I).strip()
        if simplified and simplified.casefold() != query.strip().casefold():
            data = await request(context, 'GET', 'quotations/lookup', dict(params, query=simplified))
            # A shortened query is a search, not proof that the intended price list is unique.
            data.pop('exact_match_unique', None)
    return data


async def collect(params, context):
    from app.business.workflow import prepare

    turn = QuoteTurn.model_validate(params)
    if turn.action == 'status' and turn.quotation_name:
        from app.business.quote_status import status_by_name
        return [await status_by_name(context, turn.quotation_name)]
    previous = session(context).get('active_draft')
    previous = previous if previous and previous.get('draft_type') == 'quotation' else None
    prior = latest_quote_confirmation(context, previous) if previous else None
    if turn.action == 'show':
        return [state_prompt(previous)] if previous else ['Chưa có nháp báo giá.']
    if turn.action == 'confirm':
        return ['Chọn Xác nhận trên bản xem trước để tạo báo giá.']
    if turn.action == 'status':
        return await prepare(context, status_only=True)
    if turn.action == 'explain':
        return ['Ngày hết hạn là ngày lịch. Thời gian giao hàng là số ngày và Odoo tự tính khi bạn để trống.']
    if turn.action == 'cancel':
        from app.business.workflow import edit_quote
        return await edit_quote({'action': 'cancel'}, context)
    if prior and prior['status'] in ('executing', 'unknown_result'):
        from app.business.quote_status import refresh_confirmation
        prior, remote = await refresh_confirmation(context, confirmation_service, prior)
        if prior['status'] in ('executing', 'unknown_result'):
            if remote == 'in_progress':
                return ['Đang tạo báo giá trên Odoo. Tôi sẽ gửi kết quả khi hoàn tất.']
            return ['Đang đối soát kết quả tạo báo giá trên Odoo.']
    if prior and prior['status'] == 'completed' and turn.action != 'start':
        return await prepare(context, status_only=True)
    if turn.action == 'start' or not previous:
        draft = {'draft_type': 'quotation', 'draft_id': 'quote_' + secrets.token_hex(8), 'version': 1,
                 'content': 'Báo giá nháp', 'quotation_data': {'lines': []}, 'quotation_hints': {}}
        prior = None
    else:
        draft = copy.deepcopy(previous)
    payload = draft['quotation_data']
    hints = draft.setdefault('quotation_hints', {})
    if turn.action == 'finish_lines' and prior and prior['status'] == 'pending':
        return await prepare(context)
    if turn.action == 'finish_lines':
        hints['lines_complete'] = True
    if turn.delivery_date:
        hints['requested_delivery_date'] = turn.delivery_date
    if turn.customer_query is not None:
        if hints.get('customer_query', '').casefold() != turn.customer_query.casefold():
            for field in ('partner_id', 'partner_invoice_id', 'partner_shipping_id'):
                payload.pop(field, None)
            hints.pop('partner_label', None)
            if (hints.get('selection') or {}).get('kind') == 'partners':
                hints.pop('selection', None)
        hints['customer_query'] = turn.customer_query
    if turn.pricelist_query is not None:
        if hints.get('pricelist_query', '').casefold() != turn.pricelist_query.casefold():
            hints.pop('pricelist_label', None)
            hints['pricelist_lookup_pending'] = True
        hints['pricelist_query'] = turn.pricelist_query
    order_facts = turn.model_dump(exclude_none=True, include={
        'validity_date', 'partner_invoice_id', 'partner_shipping_id', 'pricelist_id',
        'payment_term_id', 'fiscal_position_id', 'currency_id', 'note', 'client_order_ref', 'origin',
    })
    if turn.validity_date is not None:
        from datetime import date
        order_facts['validity_date'] = date.fromisoformat(turn.validity_date).isoformat()
    payload.update(order_facts)
    if turn.custom_fields:
        payload.setdefault('custom_fields', {}).update(turn.custom_fields)
    for line in [turn, *(turn.line_updates or [])]:
        apply_line_facts(line, payload, hints)
    pending = hints.get('selection')
    if turn.selected_id:
        if not pending or time.time() - pending.get('at', 0) > 1800 or turn.selected_id not in pending['ids']:
            return ['Hãy tra cứu hoặc chọn lại từ danh sách hiện tại. ID này chưa xác minh.']
        candidates = ((session(context).get('last_read') or {}).get('business_candidates_' + pending['kind']) or {})
        row = next((row for row in candidates.get('data', {}).get('items', []) if row['id'] == turn.selected_id), None)
        save_reference(draft, pending['kind'], pending['line_index'], row or {'id': turn.selected_id})
    if turn.use_customer_addresses is True:
        if not payload.get('partner_id'):
            return ['Hãy chọn khách hàng trước để dùng địa chỉ của khách hàng đó.']
        address_fields = {'partner_invoice_id', 'partner_shipping_id'}
        correction = json.loads(prior.get('result_json') or '{}') if prior and prior['status'] == 'failed' else {}
        requested = address_fields.intersection(correction.get('missing_fields') or [])
        for field in requested or address_fields:
            payload[field] = payload['partner_id']
    saved = previous if draft == previous else save(context, draft, previous)
    if not saved:
        return ['Nháp đã thay đổi. Vui lòng xem lại trước khi sửa.']
    if turn.delivery_date:
        return ['Bạn vừa cung cấp một ngày lịch. Thời gian giao hàng cần số ngày, ví dụ 10 ngày.']

    references = []
    if not payload.get('partner_id') and hints.get('customer_query'):
        references.append(('partners', hints['customer_query'], 1))
    references.extend(('products', hints[f'product_query_{index}'], index)
                      for index, line in enumerate(payload['lines'], 1)
                      if not line.get('product_id') and hints.get(f'product_query_{index}'))
    if hints.get('pricelist_query') and (not payload.get('pricelist_id') or hints.get('pricelist_lookup_pending')):
        references.append(('pricelists', hints['pricelist_query'], 1))
    for kind, query, index in references:
        data = await lookup_reference(context, kind, query, payload)
        if not isinstance(data.get('items'), list):
            return ['Đã lưu nháp. Tra cứu Odoo chưa hoàn tất: ' + str(data.get('message', 'hãy thử lại.'))]
        rows = data['items']
        remember(context, f'business_candidates_{kind}', {'kind': kind, 'data': data, 'observed_at': time.time()})
        if not rows:
            return [f'Không tìm thấy {query!r} trong Odoo. Hãy cung cấp tên hoặc mã chính xác.']
        exact = [row for row in rows if query.strip().casefold() in {
            str(row.get(key) or '').strip().casefold() for key in ('name', 'display_name', 'default_code')
        }]
        draft = copy.deepcopy(saved)
        if data.get('company_id'):
            draft['quotation_data'].setdefault('company_id', data['company_id'])
        if len(exact) == 1 and data.get('exact_match_unique') is True:
            save_reference(draft, kind, index, exact[0])
        else:
            draft['quotation_hints']['selection'] = {'kind': kind, 'line_index': index,
                'ids': [row['id'] for row in rows], 'at': time.time()}
        saved = save(context, draft, saved)
        if not saved:
            return ['Nháp đã thay đổi trong lúc tra cứu. Vui lòng xem lại.']
        if draft['quotation_hints'].get('selection'):
            label = 'khách hàng' if kind == 'partners' else 'bảng giá' if kind == 'pricelists' else f'sản phẩm dòng {index}'
            options = '\n'.join(f"ID {row['id']}: {row.get('display_name') or row.get('name')}" for row in rows)
            return [f'Bạn muốn chọn {label} nào trong Odoo?\n{options}']
    if not saved['quotation_data'].get('pricelist_id') and saved['quotation_data'].get('partner_id') and saved['quotation_data'].get('lines') and all(line.get('product_id') for line in saved['quotation_data']['lines']):
        data = await request(context, 'GET', 'quotations/lookup', {'kind': 'pricelists', 'query': '', **(
            {'company_id': saved['quotation_data']['company_id']} if saved['quotation_data'].get('company_id') else {})})
        rows = data.get('items')
        if not isinstance(rows, list) or not rows:
            return ['Hãy cho biết tên bảng giá muốn dùng; tôi sẽ tìm trong Odoo.']
        remember(context, 'business_candidates_pricelists', {'kind': 'pricelists', 'data': data, 'observed_at': time.time()})
        draft = copy.deepcopy(saved)
        draft['quotation_hints']['selection'] = {'kind': 'pricelists', 'line_index': 1, 'ids': [row['id'] for row in rows], 'at': time.time()}
        if not save(context, draft, saved):
            return ['Nháp đã thay đổi trong lúc tra cứu. Vui lòng xem lại.']
        options = '\n'.join(f"ID {row['id']}: {row.get('display_name') or row.get('name')}" for row in rows)
        return ['Bạn muốn chọn bảng giá nào? Bạn có thể trả lời bằng tên hoặc ID; nếu không thấy, hãy cho biết tên bảng giá.\n' + options]
    if saved != previous:
        resumed = await resume_quote(context, saved, prior)
        if resumed:
            return resumed
    return await prepare(context)
