"""Quote conversations store user facts before resolving Odoo references."""
import copy
import secrets
import time

from app.business.client import request
from app.business.quote_schema import QuoteTurn
from app.business.workflow import session, remember, store, confirmation_service


def state_prompt(draft):
    data = draft.get('quotation_data', {})
    hints = draft.get('quotation_hints', {})
    lines = data.get('lines', [])
    missing = []
    if not data.get('partner_id'):
        missing.append('chọn khách hàng từ Odoo' if hints.get('customer_query') else 'tên khách hàng')
    if not lines:
        missing.append('tên sản phẩm và số lượng')
    for index, line in enumerate(lines, 1):
        for field, label in [('product_id', 'chọn sản phẩm từ Odoo'), ('quantity', 'số lượng'), ('price_unit', 'đơn giá'), ('tax_ids', 'thuế áp dụng')]:
            if field == 'price_unit' and line.get('use_catalog_price') is True:
                continue
            if field not in line or line[field] is None:
                missing.append(f'dòng {index}: {label}')
        if line.get('price_unit') is not None and line.get('price_confirmed') is not True and not line.get('use_catalog_price'):
            missing.append(f'dòng {index}: xác nhận đơn giá')
    details = []
    if data.get('partner_id') or hints.get('customer_query'):
        details.append('Khách hàng: ' + str(data.get('partner_id') or hints['customer_query']) + ('' if data.get('partner_id') else ' (chưa chọn bản ghi Odoo)'))
    for index, line in enumerate(lines, 1):
        product = line.get('product_id') or hints.get(f'product_query_{index}') or 'chưa chọn'
        price = 'giá niêm yết Odoo (sẽ đọc và chốt trong bản xem trước)' if line.get('use_catalog_price') else line.get('price_unit', 'chưa chốt')
        details.append(f"Dòng {index}: {product}; số lượng: {line.get('quantity', 'chưa nhập')}; đơn giá: {price}.")
        if 'customer_lead' in line:
            details.append(f"Thời gian giao hàng tùy chọn: {line['customer_lead']} ngày.")
    if data.get('validity_date'):
        details.append(f"Ngày hết hạn báo giá: {data['validity_date']}.")
    status = 'Chờ bổ sung thông tin' if missing else 'Sẵn sàng kiểm tra'
    prefix = f'Trạng thái: {status}.\nNháp báo giá đã lưu, chưa tạo và chưa có số báo giá trên Odoo.\n' + ('\n'.join(details) + '\n' if details else '')
    if missing:
        return prefix + 'Cần bổ sung: ' + '; '.join(missing) + '.'
    return prefix + 'Đã có thông tin cơ bản. Bạn có thể yêu cầu xem trước; hệ thống sẽ kiểm tra các trường và quy tắc Odoo trước khi cấp mã xác nhận.'


def save(context, draft, previous):
    saved = store.set_active_draft(context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, draft,
        database=context.database, expected_version=previous.get('version') if previous else None,
        expected_draft_id=previous.get('draft_id') if previous else None)
    if saved and previous:
        confirmation_service.cancel_pending_draft_confirmations(user_id=context.user_id, chat_id=context.chat_id,
            telegram_binding_id=context.telegram_binding_id or 'default', draft_id=previous['draft_id'])
    return saved


async def collect(params, context):
    turn = QuoteTurn.model_validate(params)
    if turn.action == 'status' and turn.quotation_name:
        from app.business.quote_status import status_by_name
        return [await status_by_name(context, turn.quotation_name)]
    previous = session(context).get('active_draft')
    wants_preview = turn.action == 'preview'
    has_facts = any(getattr(turn, key) is not None for key in ('customer_query', 'product_query', 'selected_id', 'quantity', 'price_unit', 'price_confirmed', 'use_catalog_price', 'use_customer_addresses', 'validity_date', 'customer_lead', 'delivery_date', 'tax_ids'))
    if turn.action == 'show':
        return [state_prompt(previous)] if previous and previous.get('draft_type') == 'quotation' else ['Chưa có nháp báo giá.']
    if turn.action == 'confirm':
        return ['Xác nhận tạo báo giá phải được xử lý qua bản xem trước đang chờ xác nhận.']
    if turn.action == 'status' or (wants_preview and not has_facts):
        from app.business.workflow import prepare
        return await prepare(context, status_only=turn.action == 'status')
    if turn.action == 'explain':
        return ['Ngày hết hạn báo giá là validity_date, khác với thời gian giao hàng customer_lead. Không cần nhập thời gian giao hàng khi lập báo giá thông thường; Odoo tự tính mặc định. Ngày hết hạn nếu bạn muốn chỉ định cần một ngày cụ thể. Đơn giá và thuế cần được chốt trước bản xem trước và xác nhận.\n' + (state_prompt(previous) if previous and previous.get('draft_type') == 'quotation' else 'Chưa có nháp báo giá.')]
    if turn.action == 'cancel':
        from app.business.workflow import edit_quote
        return await edit_quote({'action': 'cancel'}, context)
    if turn.action == 'start' or not previous or previous.get('draft_type') != 'quotation':
        draft = {'draft_type': 'quotation', 'draft_id': 'quote_' + secrets.token_hex(8), 'version': 1,
                 'content': 'Báo giá nháp', 'quotation_data': {'lines': []}, 'quotation_hints': {}}
    else:
        draft = copy.deepcopy(previous)
    payload = draft['quotation_data']
    hints = draft.setdefault('quotation_hints', {})
    if turn.validity_date is not None:
        from datetime import date
        payload['validity_date'] = date.fromisoformat(turn.validity_date).isoformat()
    if turn.action == 'finish_lines':
        hints['lines_complete'] = True
        saved = save(context, draft, previous)
        return [state_prompt(saved)] if saved else ['Nháp đã thay đổi; vui lòng xem lại trước khi sửa.']
    if turn.delivery_date:
        hints['requested_delivery_date'] = turn.delivery_date
    if turn.customer_query is not None:
        if hints.get('customer_query') != turn.customer_query:
            payload.pop('partner_id', None)
            payload.pop('partner_invoice_id', None)
            payload.pop('partner_shipping_id', None)
            for line in payload['lines']:
                line.pop('tax_ids', None)
                line.pop('use_catalog_price', None)
                line['price_confirmed'] = False
        hints['customer_query'] = turn.customer_query
    if turn.product_query is not None:
        while len(payload['lines']) < turn.line_index:
            payload['lines'].append({})
        line = payload['lines'][turn.line_index - 1]
        product_key = f'product_query_{turn.line_index}'
        if hints.get(product_key) != turn.product_query:
            for key in ('product_id', 'price_unit', 'price_confirmed', 'use_catalog_price', 'tax_ids', 'uom_id', 'name'):
                line.pop(key, None)
        hints[product_key] = turn.product_query
    facts = turn.model_dump(exclude_none=True, include={'quantity', 'price_unit', 'price_confirmed', 'use_catalog_price', 'customer_lead', 'tax_ids'})
    if facts:
        while len(payload['lines']) < turn.line_index:
            payload['lines'].append({})
        line = payload['lines'][turn.line_index - 1]
        if 'price_unit' in facts and facts.get('price_confirmed') is not True:
            facts['price_confirmed'] = False
        if 'price_unit' in facts and 'use_catalog_price' not in facts:
            facts['use_catalog_price'] = False
        line.update(facts)
    pending = hints.get('selection')
    if turn.selected_id:
        if not pending or time.time() - pending.get('at', 0) > 1800 or turn.selected_id not in pending['ids']:
            return ['Hãy tra cứu/chọn lại từ danh sách hiện tại; không sử dụng ID chưa xác minh.']
        if pending['kind'] == 'partners':
            payload['partner_id'] = turn.selected_id
        else:
            payload['lines'][pending['line_index'] - 1]['product_id'] = turn.selected_id
        hints.pop('selection', None)
    if turn.use_customer_addresses is True:
        if not payload.get('partner_id'):
            return ['Hãy chọn khách hàng trước; tôi chưa tự gán địa chỉ xuất hóa đơn hoặc giao hàng.']
        payload['partner_invoice_id'] = payload['partner_id']
        payload['partner_shipping_id'] = payload['partner_id']
    saved = save(context, draft, previous)
    if not saved:
        return ['Nháp đã thay đổi; vui lòng xem lại trước khi sửa.']
    if turn.delivery_date:
        return [state_prompt(saved) + '\nBạn vừa cung cấp một ngày lịch. Trường customer_lead cần số ngày, ví dụ 10; tôi chưa tự chuyển đổi hoặc ghi ngày lịch vào trường này.']
    # Resolve one reference per turn; facts are already durable if lookup fails or times out.
    kind, query, index = None, None, turn.line_index
    if not payload.get('partner_id') and hints.get('customer_query'):
        kind, query = 'partners', hints['customer_query']
    else:
        for index, line in enumerate(payload['lines'], 1):
            if not line.get('product_id') and hints.get(f'product_query_{index}'):
                kind, query = 'products', hints[f'product_query_{index}']
                break
    if kind:
        data = await request(context, 'GET', 'quotations/lookup', {'kind': kind, 'query': query})
        if not isinstance(data.get('items'), list):
            return [state_prompt(saved) + '\nTra cứu Odoo chưa hoàn tất: ' + str(data.get('message', 'hãy thử lại.'))]
        rows = data['items']
        candidate = {'kind': kind, 'data': data, 'observed_at': time.time()}
        remember(context, f'business_candidates_{kind}', candidate)
        if not rows:
            return [state_prompt(saved) + f'\nKhông tìm thấy {query!r} trong Odoo. Hãy cung cấp tên/mã chính xác; tôi chưa tự tạo khách hàng hoặc sản phẩm.']
        draft = copy.deepcopy(saved)
        draft['quotation_hints']['selection'] = {'kind': kind, 'line_index': index, 'ids': [row['id'] for row in rows], 'at': time.time()}
        if not save(context, draft, saved):
            return ['Nháp đã thay đổi trong lúc tra cứu; vui lòng xem lại.']
        label = 'khách hàng' if kind == 'partners' else 'sản phẩm'
        options = '\n'.join(f"ID {row['id']}: {row.get('display_name') or row.get('name')}" + (f"; giá niêm yết tham khảo: {row['list_price']}" if row.get('list_price') is not None else '') for row in rows)
        return [f'Đã lưu thông tin bạn cung cấp. Kết quả {label} trên Odoo:\n{options}\nBạn chọn kết quả nào? Có thể trả lời bằng tên hoặc ID. Giá tham khảo chưa được coi là đơn giá đã chốt.']
    if wants_preview:
        from app.business.workflow import prepare
        return await prepare(context)
    return [state_prompt(saved)]


PROMPT = '''
LUỒNG BÁO GIÁ CÓ TRẠNG THÁI:
Yêu cầu tạo báo giá và mọi câu trả lời bổ sung cho nháp báo giá phải dùng read sales.quotation.collect, không dùng reply/clarify để giả vờ ghi nhận.
parameters có action=start/update/preview/status/show/cancel/finish_lines/explain, customer_query, product_query, selected_id, line_index, quantity, price_unit, price_confirmed, customer_lead, delivery_date, tax_ids. Hỏi thông tin đang có trong nháp -> action=show.
Ngay câu 'tạo báo giá' chưa có thông tin cũng gọi collect action=start để tạo nháp thật. Tên khách hàng lưu customer_query, tên sản phẩm lưu product_query, số lượng lưu quantity. Nếu lịch sử cũ có thông tin nhưng nháp thật trống, trích lại thông tin người dùng đã cung cấp và lưu qua collect; không nói đã lưu nếu chưa gọi công cụ.
Sau tra cứu, phải hỏi người dùng chọn selected_id; không tự chọn kể cả chỉ một kết quả. Nhận giá người dùng cho thì price_unit và price_confirmed=true. Không đoán thuế hoặc dùng [] nếu người dùng chưa xác nhận không thuế.
customer_lead là số ngày không âm, không phải ngày tháng. '10 ngày' -> customer_lead=10; 'ngày 3/11/2026' -> delivery_date='2026-11-03', tuyệt đối không chuyển thành customer_lead hoặc nói đã điền số ngày.
Phủ định phải xét đối tượng: trả lời 'không' cho câu hỏi thêm dòng/tra giá nghĩa là finish_lines hoặc giữ nháp, không hủy báo giá. Chỉ action=cancel khi người dùng nói rõ hủy toàn bộ nháp.
Yêu cầu 'tạo luôn', 'không cần xem trước' vẫn là collect action=preview; không bỏ bước xác nhận. Muốn xem trước hoặc nói 'có' sau câu hỏi xem trước -> action=preview; muốn hỏi định nghĩa trường -> action=explain.
Đọc quotation_data và quotation_hints trong active_draft như nguồn dữ liệu chuẩn. Không nói 'đã đủ thông tin' khi chưa chọn khách/sản phẩm thật, giá và thuế. Thời gian giao hàng là tùy chọn, để Odoo tự tính. Ngày hết hạn thuộc validity_date. Người dùng chốt giá niêm yết thì use_catalog_price=true. Không hỏi thêm dòng tùy chọn khi còn thiếu phần bắt buộc.
Không chèn nghiệp vụ báo giá vào draft email. Không gọi sales.quotation.preview nếu chưa có nháp; dùng collect start/update trước.
'''
