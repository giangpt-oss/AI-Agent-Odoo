"""A PDF button selects the saved Odoo quotation before conversational edits."""
import json
import re

from app.business.client import request
from app.business.quote_fallback import plain
from app.business.quote_schema import QuoteTurn


TOKEN = r'[0-9a-f]{24}'


def _pending_store(store, context, value):
    args = (context.tenant, context.user_id, context.chat_id, context.telegram_binding_id)
    if value:
        store.set_pending_clarification(*args, value, database=context.database)
    else:
        store.clear_pending_clarification(*args, database=context.database)


def _line_name(line):
    product = line.get('product_id')
    if isinstance(product, (list, tuple)) and len(product) > 1:
        return str(product[1])
    return str(line.get('name') or '')


def _number(text):
    numbers = {'mot': 1, 'hai': 2, 'ba': 3, 'bon': 4, 'nam': 5,
               'sau': 6, 'bay': 7, 'tam': 8, 'chin': 9, 'muoi': 10}
    value = plain(text)
    return int(value) if value.isdigit() else numbers.get(value)


async def handle(text, context, session, store, pipeline):
    edit = re.fullmatch(r'/quote_edit\s+(' + TOKEN + r')', text)
    approve = re.fullmatch(r'/quote_approve\s+(' + TOKEN + r')', text)
    grant = re.fullmatch(r'/quote_grant\s+(' + TOKEN + r')', text)
    feedback = re.fullmatch(r'/quote_feedback\s+(' + TOKEN + r')', text)
    waiting = session.get('pending_clarification') or {}
    if (waiting.get('kind') == 'quote_approval_feedback' and (edit or approve or grant)
            and not (feedback and feedback[1] == waiting.get('token'))):
        return [f"Bạn đang nhập góp ý cho báo giá {waiting['name']}. Gửi góp ý hoặc nhắn 'hủy' trước khi dùng nút khác."]
    if grant:
        decided = await request(context, 'POST', 'quotations/approval-decision', {'token': grant[1]})
        return [decided.get('message') or 'Chưa ghi nhận được quyết định duyệt.']
    if feedback:
        waiting = session.get('pending_clarification') or {}
        if waiting.get('kind') == 'quote_approval_feedback' and waiting.get('token') != feedback[1]:
            return [f"Bạn đang góp ý cho báo giá {waiting['name']}. Hãy gửi nội dung góp ý trước khi mở báo giá khác."]
        opened = await request(context, 'POST', 'quotations/approval-open', {'token': feedback[1]})
        if opened.get('status') != 'OK':
            return [opened.get('message') or 'Bản duyệt này đã cũ. Hãy chờ PDF mới.']
        _pending_store(store, context, {'kind': 'quote_approval_feedback',
            'token': feedback[1], 'name': opened['name']})
        return [f"Bạn muốn góp ý gì cho báo giá {opened['name']}?"]
    if edit or approve:
        token = (edit or approve)[1]
        opened = await request(context, 'POST', 'quotations/pdf-open', {'token': token})
        if opened.get('status') != 'OK':
            return [opened.get('message') or 'Nút PDF này đã cũ hoặc không thuộc tài khoản của bạn. Hãy gửi lại PDF.']
        if approve:
            submitted = await request(context, 'POST', 'quotations/approval-submit', {'token': token})
            return [submitted.get('message') or 'Chưa gửi được yêu cầu duyệt; báo giá vẫn là nháp.']
        _pending_store(store, context, {'kind': 'saved_quote_edit', 'token': token,
            'order_id': opened['order_id'], 'name': opened['name'],
            'version': opened['version'], 'company_id': opened.get('company_id'),
            'lines': opened['lines']})
        return [f"Bạn muốn sửa gì trên báo giá {opened['name']}?"]
    pending = (session.get('pending_clarification') or {})
    if pending.get('kind') == 'quote_approval_feedback':
        if plain(text) in ('huy', 'huy gop y', 'cancel'):
            _pending_store(store, context, None)
            return [f"Đã hủy nhập góp ý cho báo giá {pending['name']}."]
        if not text.strip():
            return [f"Bạn gửi nội dung cần sửa trên báo giá {pending['name']} nhé."]
        decided = await request(context, 'POST', 'quotations/approval-decision',
            {'token': pending['token'], 'feedback': text})
        if decided.get('status') in ('recorded', 'recorded_not_delivered'):
            _pending_store(store, context, None)
        return [decided.get('message') or 'Chưa chuyển được góp ý; bạn có thể gửi lại.']
    if pending.get('kind') != 'saved_quote_edit':
        return None
    named = re.search(r'\bS\d{3,}\b', text, re.I)
    if named and named.group().upper() != pending['name'].upper():
        return [f"Bạn đang sửa báo giá {pending['name']}. Hãy bấm Chỉnh sửa trên PDF của {named.group().upper()} nếu muốn đổi báo giá khác."]
    from app.business.quote_optional import handle_saved as handle_saved_optional
    optional_reply = await handle_saved_optional(text, context, pending, store)
    if optional_reply is not None:
        return optional_reply
    options = pending.get('term_options') or []
    choice = re.fullmatch(r'\s*(?:(?:chọn|lấy)\s+)?(?:số\s*)?(\d+)\s*', plain(text))
    if options and choice:
        position = int(choice[1])
        if not 1 <= position <= len(options):
            return [f"Hãy chọn số từ 1 đến {len(options)} trong danh sách điều khoản vừa gửi."]
        selected = options[position - 1]
        result = await request(context, 'POST', 'quotations/pdf-edit',
            {'token': pending['token'], 'payment_term_id': selected['id']})
        if result.get('status') in ('sent', 'saved_pdf_failed'):
            _pending_store(store, context, None)
            return ([f"Đã sửa điều khoản trên báo giá {pending['name']} và gửi PDF mới."]
                    if result['status'] == 'sent' else [result['message']])
        return [result.get('message') or 'Chưa sửa được điều khoản; hãy thử lại trên PDF mới.']
    if options and plain(text) in ('dong y', 'xac nhan', 'ok') and len(options) == 1:
        return await handle('1', context, session, store, pipeline)
    term_request = re.search(r'(?:dieu khoan|thanh toan|tra ngay|ngay lap tuc|\d+\s*ngay|immediate payment)', plain(text))
    if term_request:
        query = re.sub(r'^.*?(?:đổi|sửa|chuyển|chọn|lấy|dùng|doi|sua|chuyen|chon|lay|dung)\s+(?:(?:điều khoản|dieu khoan)(?: thanh toán| thanh toan)?\s+)?(?:thành|sang|là|thanh|la)?\s*', '', text, flags=re.I).strip()
        query = re.sub(r'^(?:điều khoản|dieu khoan)(?: thanh toán| thanh toan)?\s*[:：]?\s*', '', query, flags=re.I).strip()
        if query:
            data = await request(context, 'GET', 'quotations/lookup', {'kind': 'payment_terms', 'query': query})
            rows = data.get('items') or []
            if len(rows) == 1 and data.get('exact_match_unique') is True:
                result = await request(context, 'POST', 'quotations/pdf-edit',
                    {'token': pending['token'], 'payment_term_id': rows[0]['id']})
                if result.get('status') in ('sent', 'saved_pdf_failed'):
                    _pending_store(store, context, None)
                    return ([f"Đã sửa điều khoản trên báo giá {pending['name']} và gửi PDF mới."]
                            if result['status'] == 'sent' else [result['message']])
                return [result.get('message') or 'Chưa sửa được điều khoản; hãy thử lại trên PDF mới.']
            if rows:
                pending['term_options'] = [{'id': row['id'], 'name': row.get('name') or row.get('display_name')} for row in rows]
                _pending_store(store, context, pending)
                listing = '\n'.join(f"{i}. {row['name']}" for i, row in enumerate(pending['term_options'], 1))
                return [f"Bạn muốn dùng điều khoản nào cho báo giá {pending['name']}? Trả lời số thứ tự:\n{listing}"]
    lines = pending.get('lines') or []
    normalized = plain(text)
    amount = r'(?:\d+|mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)'
    match = re.search(r'\b(?:thanh|len|xuong|con|lay)\s+(' + amount + r')\b', normalized)
    quantity = _number(match[1]) if match else None
    target = None
    mentioned = [line for line in lines if _line_name(line) and plain(_line_name(line)) in normalized]
    if len(mentioned) == 1:
        target = mentioned[0]
    elif len(lines) == 1:
        target = lines[0]
    if quantity is None or target is None:
        try:
            prompt = json.dumps({'task': 'Extract only the requested saved quotation line quantity change.',
                'user_text': text, 'quotation_name': pending['name'],
                'lines': [{'line_index': i, 'name': _line_name(line),
                           'quantity': line.get('product_uom_qty')} for i, line in enumerate(lines, 1)]},
                ensure_ascii=False)
            raw = await pipeline._call_gemini_raw(prompt, response_schema=QuoteTurn.model_json_schema())
            turn = QuoteTurn.model_validate_json(raw)
            changes = turn.line_updates or [turn]
            if len(changes) == 1 and changes[0].quantity is not None:
                choice = changes[0]
                if 1 <= choice.line_index <= len(lines):
                    if len(lines) == 1 or choice.product_query and plain(choice.product_query) in plain(_line_name(lines[choice.line_index - 1])):
                        target = lines[choice.line_index - 1]
                        quantity = choice.quantity
        except Exception:
            pass
    if quantity is None or target is None:
        return [f"Bạn muốn sửa số lượng của sản phẩm nào trên báo giá {pending['name']}? Hãy nêu tên sản phẩm và số lượng mới."]
    result = await request(context, 'POST', 'quotations/pdf-edit', {'token': pending['token'],
        'line_id': target['id'], 'quantity': quantity})
    if result.get('status') in ('sent', 'saved_pdf_failed'):
        _pending_store(store, context, None)
        return ([f"Đã sửa báo giá {pending['name']} trên cùng bản ghi và gửi PDF mới."]
                if result['status'] == 'sent' else [result['message']])
    return [result.get('message') or 'Chưa sửa được báo giá. Hãy gửi lại PDF và chọn Chỉnh sửa.']
