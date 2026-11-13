"""Conservative interpretation of explicit quote facts, independent of AI quota.

Only complete, unambiguous utterances match. Persistence, reference authorization,
preview and confirmation remain in their existing application boundaries.
"""
import re
import time
from datetime import date


def asks_delivery_or_approval_status(text):
    return bool(re.search(r'đã gửi.*chưa|đang chờ.*duyệt|đã (?:được )?duyệt.*chưa|'
                          r'(?:cấp trên|quản lý).*xác nhận.*chưa', text.casefold()))


def structured_quote(text, draft):
    """Parse a complete, unambiguous multiline quote without an AI request."""
    data = draft.get('quotation_data') or {}
    if data.get('lines') or data.get('partner_id') or data.get('pricelist_id'):
        return None
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    if len(rows) < 3:
        return None
    customer, pricelist, lines, no_tax = None, None, [], False
    for row in rows:
        price_match = re.fullmatch(r'bảng giá\s*:\s*(.+)', row, re.I)
        product_match = re.fullmatch(r'(.+?)\s+model\s*:\s*([\w-]+)\s*[,;]\s*số lượng\s*:?\s*(\d+(?:[.,]\d+)?)', row, re.I)
        if price_match and not pricelist:
            pricelist = price_match[1].strip()
        elif product_match:
            quantity = float(product_match[3].replace(',', '.'))
            if quantity <= 0:
                return None
            lines.append({'line_index': len(lines) + 1, 'product_query': product_match[2], 'quantity': quantity})
        elif re.fullmatch(r'(?:thuế\s*(?:bỏ qua|không tính)|không (?:tính |áp dụng )?thuế)', row, re.I):
            no_tax = True
        elif customer is None and not lines and not pricelist and not re.search(r'[:;]', row):
            customer = re.sub(r'^khách hàng\s*:\s*', '', row, flags=re.I).strip()
        else:
            return None
    if not customer or not pricelist or not lines:
        return None
    if no_tax:
        for line in lines:
            line['tax_ids'] = []
    return {'action': 'update', 'customer_query': customer, 'pricelist_query': pricelist, 'line_updates': lines}


def interpret(text, draft, session=None):
    text = text.strip().rstrip('.!?').strip()
    lower = text.casefold()
    names = re.findall(r'\bS\d{3,}\b', text, re.I)
    if asks_delivery_or_approval_status(text):
        if len(names) == 1:
            return {'action': 'status', 'quotation_name': names[0].upper()}
        if not names and draft.get('draft_type') == 'quotation':
            return {'action': 'status'}
    if len(names) == 1 and not re.search(r'\b(?:hủy|sửa|xóa|đổi|tạo|gửi|xác nhận)\b', lower):
        if re.search(r'trạng thái|tiến độ|đến đâu|xem|kiểm tra|thế nào|xong chưa|báo giá', lower) or text.upper() == names[0].upper():
            return {'action': 'status', 'quotation_name': names[0].upper()}
    if re.fullmatch(r'(?:hãy |giúp tôi |tôi muốn )?(?:tạo|lập)(?: cho tôi)? (?:một |1 )?báo giá(?: mới)?(?: đi)?', lower):
        return {'action': 'start'} if draft.get('draft_type') != 'quotation' or 'mới' in lower else {'action': 'preview'}
    if draft.get('draft_type') != 'quotation':
        return None
    structured = structured_quote(text, draft)
    if structured:
        return structured
    if re.fullmatch(r'(?:dùng|lấy)(?: chính)? địa chỉ khách hàng(?: đã chọn)? cho (?:cả )?xuất hóa đơn và giao hàng', lower):
        return {'action': 'update', 'use_customer_addresses': True}
    session = session or {}
    selection = draft.get('quotation_hints', {}).get('selection') or {}
    if selection and time.time() - selection.get('at', 0) <= 1800:
        candidate = (session.get('last_read') or {}).get('business_candidates_' + selection.get('kind', '')) or {}
        requested = re.sub(r'^(?:chọn|lấy)\s+', '', text, flags=re.I).casefold()
        matches = [row['id'] for row in candidate.get('data', {}).get('items', [])
                   if row.get('id') in selection.get('ids', []) and requested == str(row.get('display_name') or row.get('name', '')).casefold()]
        if len(matches) == 1:
            return {'action': 'update', 'selected_id': matches[0]}
    if re.fullmatch(r'(?:tôi )?(?:xác nhận|đồng ý)(?: tạo| thực hiện)?(?: báo giá)?(?: theo bản vừa xem| theo bản xem trước| này)?', lower):
        return {'action': 'confirm'}
    if re.fullmatch(r'(?:cho |tôi muốn |cho tôi )?(?:xem trước|xem bản|xem lại|xem)(?: bản)?(?: nháp)? báo giá(?: trước| này| trước khi tạo trên odoo)?', lower):
        return {'action': 'preview'} if 'trước' in lower else {'action': 'show'}
    if re.fullmatch(r'(?:không cần xem trước,? )?(?:tôi muốn )?tạo(?: luôn)?(?: báo giá)?(?: này)?(?: luôn| đi| trên odoo)?', lower):
        return {'action': 'preview'}
    if re.fullmatch(r'(?:báo giá )?(?:kiểm tra xong chưa|đến đâu rồi|đang ở trạng thái nào|trạng thái|tiến độ)(?: báo giá)?', lower):
        return {'action': 'status'}
    if re.fullmatch(r'không thêm(?: dòng| sản phẩm)?(?: nào)?(?: nữa)?', lower):
        return {'action': 'finish_lines'}
    if re.fullmatch(r'(?:hủy|bỏ)(?: toàn bộ)? (?:nháp )?báo giá(?: này)?', lower):
        return {'action': 'cancel'}
    for pattern, field in [(r'(?:khách hàng|khách)(?: là|:)?\s+(.+)', 'customer_query'), (r'sản phẩm(?: là|:)?\s+(.+)', 'product_query')]:
        match = re.fullmatch(pattern, text, re.I)
        if match and not re.search(r'[,;\n]|\s(?:và|hay|số lượng|không)\s|\bID\b', match[1], re.I):
            return {'action': 'update', field: match[1].strip()}
    match = re.fullmatch(r'(?:(?:chọn|lấy)(?: khách hàng| sản phẩm)?\s+)?(?:id\s*)?(\d+)', lower)
    if match and selection:
        selected = int(match[1])
        if time.time() - selection.get('at', 0) <= 1800 and selected in selection.get('ids', []):
            return {'action': 'update', 'selected_id': selected}
        return None
    match = re.fullmatch(r'số lượng(?: là|:)?\s+(\d+(?:[.,]\d+)?|một|hai|ba)', lower)
    if not match and re.fullmatch(r'\d+(?:[.,]\d+)?', lower):
        lines = draft.get('quotation_data', {}).get('lines', [])
        if len(lines) == 1 and lines[0].get('product_id') and 'quantity' not in lines[0]:
            match = re.fullmatch(r'(\d+(?:[.,]\d+)?)', lower)
    if match:
        number = {'một': '1', 'hai': '2', 'ba': '3'}.get(match[1], match[1])
        value = float(number.replace(',', '.'))
        return {'action': 'update', 'quantity': value} if value > 0 else None
    match = re.fullmatch(r'đơn giá(?: là|:)?\s+(\d+(?:\.\d{3})*|\d+)(?: đồng| đ| vnd)?', lower)
    if match:
        return {'action': 'update', 'price_unit': float(match[1].replace('.', '')), 'price_confirmed': True}
    match = re.fullmatch(r'(?:giao sau |thời gian giao hàng(?: là)? |nhập(?: là)? )?(\d+) ngày', lower)
    if match:
        return {'action': 'update', 'customer_lead': float(match[1])}
    match = re.fullmatch(r'(?:ngày )?hết hạn(?: báo giá)?(?: là| ngày|:)?\s+(\d{1,2})/(\d{1,2})/(\d{4})', lower)
    if match:
        try:
            value = date(int(match[3]), int(match[2]), int(match[1])).isoformat()
        except ValueError:
            return None
        return {'action': 'update', 'validity_date': value}
    clauses = [part.strip() for part in re.split(r'[,;]|\s+và\s+', lower)]
    facts = {}
    for clause in clauses:
        if re.fullmatch(r'(?:xác nhận )?(?:không thuế|không áp dụng thuế|không tính thuế)', clause):
            facts['tax_ids'] = []
        elif re.fullmatch(r'(?:sử dụng|dùng|lấy|áp dụng)(?: giá bán)?(?: giá)?(?: đang)? niêm yết(?: trên odoo)?(?: đi)?', clause):
            facts['use_catalog_price'] = True
        else:
            break
    else:
        return {'action': 'update', **facts} if facts else None
    parts = re.split(r'[,;]|\s+và\s+', text, flags=re.I)
    if len(parts) > 1:
        combined = {}
        for part in parts:
            update = interpret(part, draft, session)
            if not update or update.get('action') != 'update':
                return None
            for field, value in update.items():
                if field != 'action':
                    if field in combined and combined[field] != value:
                        return None
                    combined[field] = value
        return {'action': 'update', **combined}
    history = session.get('history') or []
    last_question = next((row.get('content', '') for row in reversed(history) if row.get('role') == 'assistant'), '').casefold()
    # Bare names are lookup queries only, never selected Odoo records.
    if not selection and re.fullmatch(r'[^\W_][\w\s\-\'’]{1,150}', text) and not re.search(r'\b(?:không|có|hủy|tạo|lập|xem|báo|ngày|giá|thuế|sao|gì|đâu|đồng ý|xác nhận)\b', lower):
        data, hints = draft.get('quotation_data', {}), draft.get('quotation_hints', {})
        if not data.get('partner_id') and not hints.get('customer_query') and 'tên khách hàng' in last_question:
            return {'action': 'update', 'customer_query': text}
        if data.get('partner_id') and not data.get('lines') and 'tên sản phẩm' in last_question:
            return {'action': 'update', 'product_query': text}
    return None
