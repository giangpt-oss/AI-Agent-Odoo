"""Optional sale order fields between required facts and creation preview."""
import copy
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.business.client import request
from app.business.quote_fallback import plain
from app.business.workflow import session


MENU = (
    ('BÁN HÀNG', (
        ('user_id', 'Nhân viên sales'), ('team_id', 'Bộ phận sales'),
        ('require_signature', 'Chữ ký online'), ('require_payment', 'Thanh toán online'),
        ('client_order_ref', 'Tham chiếu khách hàng'), ('tag_ids', 'Thẻ'),
        ('x_studio_thoi_gian_bao_tri', 'Thời gian bảo trì'))),
    ('GIAO HÀNG', (
        ('warehouse_id', 'Kho hàng'), ('incoterm', 'Incoterm'),
        ('incoterm_location', 'Địa điểm Incoterm'), ('picking_policy', 'Chính sách vận chuyển'),
        ('commitment_date', 'Ngày giao hàng'))),
    ('XUẤT HÓA ĐƠN', (
        ('fiscal_position_id', 'Vị trí tài chính'),
        ('preferred_payment_method_line_id', 'Phương thức thanh toán'))),
    ('THEO DÕI', (
        ('origin', 'Chứng từ gốc'), ('opportunity_id', 'Cơ hội'),
        ('campaign_id', 'Chiến dịch'), ('medium_id', 'Phương tiện'),
        ('source_id', 'Nguồn'))),
)
FIELDS = tuple(item for _, group in MENU for item in group)
BY_FIELD = {field: label for field, label in FIELDS}


def menu_text():
    lines = ['Đã đủ thông tin bắt buộc. Bạn có muốn bổ sung mục nào không?', '']
    number = 1
    for group, fields in MENU:
        lines.append(group)
        for _, label in fields:
            lines.append(f'{number}. {label}')
            number += 1
        lines.append('')
    lines.extend(['Nhập số mục muốn bổ sung, ví dụ 7, 12.', 'Nhập 0 hoặc “Bỏ qua” để tiếp tục.'])
    return '\n'.join(lines)


def _save(context, draft, previous):
    from app.business.quote_collector import save
    return save(context, draft, previous)


def _param(context, field, query=None, company_id=None):
    payload = {'field': field}
    draft = session(context).get('active_draft') or {}
    company_id = company_id or (draft.get('quotation_data') or {}).get('company_id')
    if company_id:
        payload['company_id'] = company_id
    if query is not None:
        payload['query'] = query
    return payload


def _label_value(value):
    if value is True:
        return 'Có'
    if value is False:
        return 'Không'
    return str(value)


def _summary(draft):
    labels = draft.get('quotation_hints', {}).get('optional_labels', {})
    lines = ['Thông tin tùy chọn đã chọn:']
    for field, value in draft.get('quotation_data', {}).get('optional_fields', {}).items():
        lines.append(f"- {BY_FIELD[field]}: {labels.get(field) or _label_value(value)}")
    if len(lines) == 1:
        lines.append('- Chưa bổ sung mục nào.')
    lines.append('Nhập “Thêm mục khác” hoặc “Xong” để xem bản tổng hợp và xác nhận tạo báo giá.')
    return '\n'.join(lines)


def _field_request(text):
    normalized = plain(text)
    if not re.search(r'\b(?:them|bo sung|doi|sua|chinh)\b', normalized):
        return None
    matches = [field for field, label in FIELDS if re.search(r'(?<!\w)' + re.escape(plain(label)) + r'(?!\w)', normalized)]
    return matches[0] if len(matches) == 1 else None


def _numbers(text):
    normalized = plain(text)
    normalized = re.sub(r'^(?:chon|muc|so)\s+', '', normalized)
    if not re.fullmatch(r'\d+(?:\s*[,; ]\s*\d+)*', normalized):
        return None
    return [int(value) for value in re.findall(r'\d+', normalized)]


async def _question(context, field, company_id=None):
    spec = await request(context, 'GET', 'quotations/optional-metadata', _param(context, field, company_id=company_id))
    if not spec.get('available'):
        return None, spec.get('message') or f'{BY_FIELD[field]} hiện không khả dụng trên Odoo.'
    label = BY_FIELD[field]
    kind = spec['type']
    if kind == 'boolean':
        return spec, f'{label}: bạn chọn Có hay Không? Có thể nhập “Bỏ qua”.'
    if kind == 'selection':
        options = '\n'.join(f"{i}. {item['label']}" for i, item in enumerate(spec['options'], 1))
        return spec, f'{label}: chọn một giá trị Odoo bằng số thứ tự hoặc tên:\n{options}\nCó thể nhập “Bỏ qua”.'
    if kind == 'datetime':
        return spec, f'{label}: nhập ngày và giờ theo giờ Việt Nam, ví dụ 20/12/2026 14:30. Có thể nhập “Bỏ qua”.'
    if kind in ('many2one', 'many2many'):
        return spec, f'{label}: nhập tên để tra cứu trong Odoo, sau đó chọn ứng viên theo số thứ tự. Có thể nhập “Bỏ qua”.'
    return spec, f'{label}: nhập giá trị muốn dùng. Có thể nhập “Bỏ qua”.'


async def resolve_value(context, field, text, state, company_id=None):
    """Return (value, display, prompt); value None means keep asking."""
    spec, error = await _question(context, field, company_id)
    if spec is None:
        return None, None, error
    kind = spec['type']
    normalized = plain(text)
    if kind == 'boolean':
        if normalized in ('co', 'co nhe', 'bat', 'bat len', 'yes'):
            return True, 'Có', None
        if normalized in ('khong', 'khong nhe', 'tat', 'tat di', 'no'):
            return False, 'Không', None
        return None, None, f"{BY_FIELD[field]}: vui lòng chọn Có hoặc Không."
    if kind == 'selection':
        options = spec.get('options') or []
        numbers = _numbers(text)
        if numbers and len(numbers) == 1 and 1 <= numbers[0] <= len(options):
            choice = options[numbers[0] - 1]
            return choice['value'], choice['label'], None
        matches = [item for item in options if normalized in (plain(item['label']), plain(str(item['value'])))]
        if len(matches) == 1:
            return matches[0]['value'], matches[0]['label'], None
        return None, None, f"Giá trị {BY_FIELD[field]} chưa hợp lệ.\n" + '\n'.join(f"{i}. {item['label']}" for i, item in enumerate(options, 1))
    if kind in ('many2one', 'many2many'):
        candidates = state.get('candidates') or []
        numbers = _numbers(text)
        if candidates and numbers:
            if any(number < 1 or number > len(candidates) for number in numbers):
                return None, None, f'Hãy chọn số từ 1 đến {len(candidates)} trong danh sách vừa gửi.'
            if kind == 'many2one' and len(numbers) != 1:
                return None, None, 'Mục này chỉ chọn một bản ghi Odoo.'
            chosen = [candidates[number - 1] for number in numbers]
            if len({row['id'] for row in chosen}) != len(chosen):
                return None, None, 'Bạn đã chọn trùng bản ghi. Hãy nhập mỗi số một lần.'
            return ([row['id'] for row in chosen] if kind == 'many2many' else chosen[0]['id'],
                    ', '.join(row['name'] for row in chosen), None)
        query = text.strip()
        if not query or len(query) > 150:
            return None, None, f'Nhập tên {BY_FIELD[field]} để tra cứu Odoo.'
        data = await request(context, 'GET', 'quotations/optional-candidates', _param(context, field, query, company_id))
        if data.get('status') == 'error':
            return None, None, data.get('message') or 'Chưa tra cứu được danh mục Odoo.'
        rows = data.get('items') or []
        if not rows:
            return None, None, f'Chưa thấy {BY_FIELD[field]} trong Odoo. Hãy thử tên khác.'
        if len(rows) == 1 and data.get('exact_match_unique'):
            row = rows[0]
            return ([row['id']] if kind == 'many2many' else row['id'],
                    row.get('display_name') or row.get('name') or str(row['id']), None)
        state['candidates'] = [{'id': row['id'], 'name': row.get('display_name') or row.get('name') or str(row['id'])} for row in rows]
        listing = '\n'.join(f"{i}. {row['name']}" for i, row in enumerate(state['candidates'], 1))
        return None, None, f"Chọn {BY_FIELD[field]} theo số thứ tự của danh sách này:\n{listing}"
    if kind == 'datetime':
        match = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})', text.strip())
        if not match:
            return None, None, 'Nhập ngày và giờ, ví dụ 20/12/2026 14:30.'
        try:
            local = datetime(int(match[3]), int(match[2]), int(match[1]), int(match[4]), int(match[5]), tzinfo=ZoneInfo(context.timezone or 'Asia/Ho_Chi_Minh'))
        except (ValueError, KeyError):
            return None, None, 'Ngày hoặc giờ không hợp lệ.'
        return local.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), local.strftime('%d/%m/%Y %H:%M'), None
    if not text.strip() or len(text.strip()) > 255:
        return None, None, 'Nhập văn bản không rỗng, tối đa 255 ký tự.'
    return text.strip(), text.strip(), None


async def ensure(context, draft):
    state = draft.get('quotation_hints', {}).get('optional')
    if state is None:
        updated = copy.deepcopy(draft)
        updated['quotation_hints']['optional'] = {'phase': 'menu', 'queue': []}
        if not _save(context, updated, draft):
            return 'Nháp vừa thay đổi. Hãy xem lại trước khi tiếp tục.'
        return menu_text()
    if state['phase'] == 'done':
        return None
    if state['phase'] == 'menu':
        return menu_text()
    if state['phase'] == 'summary':
        return _summary(draft)
    field = state['queue'][0]
    _, question = await _question(context, field)
    return question


async def handle(text, context, session_ctx):
    active = (session_ctx or {}).get('active_draft') or {}
    if active.get('draft_type') != 'quotation':
        return None
    state = (active.get('quotation_hints') or {}).get('optional')
    if not state:
        return None
    from app.business.quote_local import requests_fresh_quote
    if requests_fresh_quote(text):
        return None
    normalized = plain(text)
    phase = state['phase']
    if phase == 'done' and normalized not in ('them muc khac', 'bo sung muc khac'):
        return None
    draft = copy.deepcopy(active)
    current = draft['quotation_hints']['optional']
    if phase == 'done':
        current.update(phase='menu', queue=[], candidates=[])
        if not _save(context, draft, active):
            return ['Nháp vừa thay đổi. Hãy thử lại.']
        return [menu_text()]
    if phase == 'menu':
        if normalized in ('0', 'bo qua', 'xong'):
            current['phase'] = 'done'
            if not _save(context, draft, active):
                return ['Nháp vừa thay đổi. Hãy thử lại.']
            from app.business.workflow import prepare
            return await prepare(context)
        numbers = _numbers(text)
        named = _field_request(text)
        if named:
            numbers = [next(index for index, item in enumerate(FIELDS, 1) if item[0] == named)]
        if numbers is None:
            return None
        if not numbers or any(number < 1 or number > len(FIELDS) for number in numbers):
            return ['Chọn số từ 1 đến 19, ví dụ 7, 12; hoặc nhập 0 để bỏ qua.']
        if len(set(numbers)) != len(numbers):
            return ['Bạn đã chọn trùng mục. Hãy nhập mỗi số một lần.']
        current['queue'] = [FIELDS[number - 1][0] for number in numbers]
        current['phase'] = 'asking'
        current['candidates'] = []
        field = current['queue'][0]
        if field == 'incoterm_location' and not draft['quotation_data'].get('optional_fields', {}).get('incoterm'):
            current['queue'].insert(0, 'incoterm')
            field = 'incoterm'
        if not _save(context, draft, active):
            return ['Nháp vừa thay đổi. Hãy thử lại.']
        _, question = await _question(context, field)
        return [question]
    if phase == 'summary':
        if normalized in ('xong', '0', 'bo qua'):
            current['phase'] = 'done'
            if not _save(context, draft, active):
                return ['Nháp vừa thay đổi. Hãy thử lại.']
            from app.business.workflow import prepare
            return await prepare(context)
        if normalized in ('them muc khac', 'bo sung muc khac'):
            current.update(phase='menu', queue=[], candidates=[])
            if not _save(context, draft, active):
                return ['Nháp vừa thay đổi. Hãy thử lại.']
            return [menu_text()]
        return ['Nhập “Thêm mục khác” hoặc “Xong”.']
    field = current['queue'][0]
    if normalized in ('bo qua', 'bo qua muc nay'):
        notice = f'Đã bỏ qua {BY_FIELD[field]}. '
    else:
        spec, error = await _question(context, field)
        if spec is None:
            notice = error + ' '
        else:
            value, display, prompt = await resolve_value(context, field, text, current)
            if prompt:
                if current != state:
                    _save(context, draft, active)
                return [prompt]
            draft['quotation_data'].setdefault('optional_fields', {})[field] = value
            draft['quotation_hints'].setdefault('optional_labels', {})[field] = display
            notice = ''
    current['queue'].pop(0)
    current['candidates'] = []
    if current['queue']:
        next_field = current['queue'][0]
        _, question = await _question(context, next_field)
        if not _save(context, draft, active):
            return ['Nháp vừa thay đổi. Hãy thử lại.']
        return [notice + question]
    current['phase'] = 'summary'
    if not _save(context, draft, active):
        return ['Nháp vừa thay đổi. Hãy thử lại.']
    return [notice + _summary(draft)]


def _saved_value_text(text, field):
    from app.business.quote_fallback import normalized_with_map
    normalized, offsets = normalized_with_map(text)
    marker = plain(BY_FIELD[field])
    match = re.search(r'(?<!\w)' + re.escape(marker) + r'(?!\w)', normalized)
    if not match:
        return ''
    end = offsets[match.end() - 1] + 1
    return re.sub(r'^\s*(?:thành|sang|là|thanh|la|:|=)\s*', '', text[end:], flags=re.I).strip()


async def handle_saved(text, context, pending, store):
    field = pending.get('optional_field') or _field_request(text)
    if field is None:
        return None
    from app.business.quote_saved_interaction import _pending_store
    if plain(text) in ('bo qua', 'bo qua muc nay'):
        pending.pop('optional_field', None)
        pending.pop('optional_candidates', None)
        _pending_store(store, context, pending)
        return [f"Đã bỏ qua {BY_FIELD[field]}. Bạn muốn sửa gì khác trên báo giá {pending['name']}?"]
    company_id = pending.get('company_id')
    spec, question = await _question(context, field, company_id)
    if spec is None:
        return [question]
    value_text = text if pending.get('optional_field') else _saved_value_text(text, field)
    pending['optional_field'] = field
    if not value_text:
        _pending_store(store, context, pending)
        return [question]
    state = {'candidates': pending.get('optional_candidates') or []}
    value, _display, prompt = await resolve_value(context, field, value_text, state, company_id)
    if prompt:
        pending['optional_candidates'] = state.get('candidates') or []
        _pending_store(store, context, pending)
        return [prompt]
    result = await request(context, 'POST', 'quotations/pdf-edit',
        {'token': pending['token'], 'optional_fields': {field: value}})
    if result.get('status') in ('sent', 'saved_pdf_failed'):
        _pending_store(store, context, None)
        return ([f"Đã sửa {BY_FIELD[field]} trên cùng báo giá {pending['name']} và gửi PDF mới."]
                if result['status'] == 'sent' else [result['message']])
    return [result.get('message') or 'Chưa sửa được báo giá; hãy thử lại trên PDF mới.']
