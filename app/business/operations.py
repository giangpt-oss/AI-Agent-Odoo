"""Local business drafts and Gateway-issued preparations; never arbitrary RPC."""
import asyncio
import copy
import json
import re
import time

from app.business.client import request
from app.business.purchase_local import (
    fold_vi,
    interpret_purchase_create,
    interpret_purchase_draft_reply,
    interpret_purchase_flow,
    parse_purchase_date,
    payment_term_candidates,
)
from app.business.stock_local import (
    interpret_stock_create,
    interpret_stock_draft_reply,
    interpret_stock_flow,
)
from app.business.workflow import remember, session
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.services.confirmation_service import OptionButtonsMessage, confirmation_service
from app.services.conversation_history_store import conversation_history_store as store

OPS = {
    'contacts': 'create update archive restore delete', 'products': 'create update archive restore',
    'crm': 'create update assign stage won lost reopen archive restore delete convert',
    'sales': 'create update copy confirm cancel draft lock unlock delete send export request_approval grant_approval reject_approval message note activity meeting',
    'purchases': 'create update copy confirm approve reject cancel draft delete send',
    'stock': 'create update confirm reserve unreserve validate cancel return inventory backorder lot_create',
    'activities': 'create update done', 'notes': 'create',
}
ACTIONS = [f'business.{module}.{op}' for module, operations in OPS.items() for op in operations.split()]
QUOTE_FLOW_ACTIONS = {'business.sales.request_approval', 'business.sales.grant_approval',
                      'business.sales.reject_approval', 'business.sales.send', 'business.sales.confirm'}
CHATTER_ACTIONS = {f'business.sales.{action}' for action in ('message', 'note', 'activity', 'meeting')}
PURCHASE_FLOW_ACTIONS = {
    f'business.purchases.{action}'
    for action in ('create', 'update', 'confirm', 'approve', 'reject', 'cancel', 'draft', 'copy', 'delete', 'send')
}
STOCK_FLOW_ACTIONS = {
    f'business.stock.{action}'
    for action in ('create', 'update', 'confirm', 'reserve', 'unreserve', 'validate', 'cancel', 'return', 'backorder', 'inventory', 'lot_create')
}


def looks_like_quote_chatter(text, active):
    lower = text.casefold()
    if re.match(r'^(?:(?:hãy|giúp tôi|cho tôi)\s+)?(?:xem|tra cứu|liệt kê|kiểm tra)\b', lower):
        return False
    if re.search(r'gửi\s+(?:email\s+|mail\s+)?(?:file\s+)?báo giá\b', lower) and not re.search(r'nội dung|tin nhắn|lời nhắn', lower):
        return False
    new_quote = re.match(r'^(?:(?:hãy|giúp tôi|tôi muốn)\s+)?(?:tạo|lập)(?:\s+cho tôi)?\s+(?:(?:một|1)\s+)?báo giá\b', lower)
    request = re.search(r'ghi chú|gửi tin|gửi email|gửi mail|nhắn tin|tin nhắn|lên lịch|lịch họp|hẹn gọi|cuộc họp|việc cần làm|hoạt động|nhắc tôi|lịch nhắc|nhắc nhở', lower)
    quote = re.search(r'\bbáo giá\b|\bS\d{3,}\b', text, re.I) or active.get('draft_type') == 'quotation'
    quote = quote or active.get('business_action') in CHATTER_ACTIONS
    return bool(request and quote and not new_quote)


def allowed_action(settings, action=None):
    if action == 'business.sales.request_approval':
        return False
    return getattr(settings, 'ENABLE_BUSINESS_ACTIONS', False) or (getattr(settings, 'ENABLE_QUOTATIONS', False) and (action is None or action in CHATTER_ACTIONS)) or (
        getattr(settings, 'ENABLE_QUOTATION_APPROVAL_FLOW', False) and (action is None or action in QUOTE_FLOW_ACTIONS))


def explicit_customer_acceptance(text):
    """Accept a past assertion, never a question, condition or negated statement."""
    lower = text.lower().strip()
    if re.search(r'\b(?:không|chưa|đừng|nếu|giả sử|khi|liệu)\b|\?', lower):
        return False
    return bool(re.search(r'khách.*đã.*(?:chấp thuận|đồng ý)|khách.*(?:chấp thuận|đồng ý) rồi', lower))


def needs_customer_acceptance_clarification(text):
    lower = text.lower()
    return bool(re.search(r'\bS\d{3,}\b', text, re.I)
                and re.search(r'chuyển.*đơn bán|xác nhận.*đơn', lower)
                and not explicit_customer_acceptance(text))


def interpret_quotation_flow(text, default_name=None):
    from app.business.quote_local import asks_delivery_or_approval_status

    if asks_delivery_or_approval_status(text):
        return None
    lower = text.lower().strip()
    names = re.findall(r'\bS\d{3,}\b', text, re.I)
    request_confirmation = bool(re.search(
        r'(?:gửi|xin|yêu cầu|nhờ|trình).*(?:yêu cầu\s+)?(?:xác nhận|phê duyệt|duyệt)', lower
    ) and re.search(r'yêu cầu|nội bộ|sếp|cấp trên|quản lý|trưởng', lower))
    if not names and request_confirmation and default_name:
        names = [default_name]
    if len(names) != 1:
        return None
    action = None
    if request_confirmation or re.search(r'(?:xin|yêu cầu|trình|gửi.*cấp trên).*duyệt|request approval', lower):
        action = 'request_approval'
    elif re.search(r'từ chối.*duyệt|không duyệt', lower):
        action = 'reject_approval'
    elif re.search(r'^(?:hãy |giúp tôi |tôi muốn )?(?:duyệt|phê duyệt|grant approval)\b', lower):
        action = 'grant_approval'
    elif explicit_customer_acceptance(text) and re.search(r'chuyển.*đơn bán|xác nhận.*đơn', lower):
        action = 'confirm'
    elif re.search(r'^(?:hãy |giúp tôi |tôi muốn )?gửi\b', lower) and 'báo giá' in lower:
        action = 'send'
    if not action or re.search(r'không (?:gửi|yêu cầu|xin|chuyển|xác nhận)|chưa (?:đồng ý|chấp thuận|duyệt)', lower):
        return None
    options = {'customer_accepted': True} if action == 'confirm' else {}
    return {'quotation_name': names[0].upper(), 'action': action, 'options': options}


def register(registry, tool_type):
    from app.core.config import get_settings
    settings = get_settings()
    available_actions = [action for action in ACTIONS if allowed_action(settings, action)]
    common = {'type': 'object', 'additionalProperties': False}
    definitions = [
        ('business.quotation_chatter', 'read', 'Chuẩn bị gửi tin, ghi chú, hoạt động hoặc cuộc họp gắn với báo giá.', {
            'quotation_name': {'type': 'string'}, 'action': {'enum': ['message', 'note', 'activity', 'meeting']},
            'options': {'type': 'object'}, 'company_id': {'type': 'integer'}}),
        ('business.quotation_flow', 'read', 'Xin duyệt/duyệt/từ chối/gửi báo giá theo số Odoo. Chỉ chuẩn bị bản xem trước, mọi thực thi phải xác nhận riêng. Không gọi để tạo báo giá mới.', {
            'quotation_name': {'type': 'string'},
            'action': {'enum': ['grant_approval', 'reject_approval', 'send', 'confirm']},
            'options': {'type': 'object'}, 'company_id': {'type': 'integer'}}),
        ('business.capabilities', 'read', 'Xem thao tác Odoo được hỗ trợ và giới hạn quyền hiện tại', {}),
        ('business.lookup', 'read', 'Tra cứu bản ghi thật cho thao tác trong năm phân hệ; không đoán ID', {
            'kind': {'enum': ['contacts', 'suppliers', 'products', 'purchase_products', 'sales', 'purchases', 'crm', 'stock', 'quants', 'activities', 'warehouses', 'locations', 'picking_types', 'lots', 'users', 'stages', 'teams', 'lost_reasons', 'taxes', 'uoms', 'currencies', 'pricelists', 'payment_terms', 'activity_types', 'countries', 'states', 'categories', 'models', 'attachments', 'move_lines', 'moves', 'product_templates', 'variant_values']},
            'query': {'type': 'string'}, 'company_id': {'type': 'integer'}}),
        ('business.draft', 'read', 'Thu thập/sửa nháp nghiệp vụ cục bộ. Không thay đổi Odoo; action=start/update/cancel, business_action là thao tác, data là payload.', {
            'action': {'enum': ['start', 'update', 'cancel']}, 'business_action': {'enum': available_actions}, 'data': {'type': 'object'}}),
        ('business.preview', 'write', 'Kiểm tra nháp trên Gateway và tạo bản xem trước có mã xác nhận; chưa ghi Odoo.', {}),
    ]
    if settings.ENABLE_BUSINESS_ACTIONS:
        definitions.append(('business.purchase_flow', 'read', 'Chuẩn bị thao tác vòng đời RFQ/đơn mua đúng số chứng từ Odoo (xác nhận, duyệt, từ chối, hủy, đưa về nháp, sao chép, xóa, gửi email).', {
            'purchase_name': {'type': 'string'},
            'action': {'enum': ['confirm', 'approve', 'reject', 'cancel', 'draft', 'copy', 'delete', 'send']},
            'company_id': {'type': 'integer'},
            'recipient_emails': {'type': 'array', 'items': {'type': 'string'}},
            'subject': {'type': 'string'},
            'body': {'type': 'string'},
        }))
        definitions.append(('business.purchase_create', 'read', 'Tìm nhà cung cấp và sản phẩm Odoo rồi chuẩn bị RFQ (hỗ trợ điều khoản thanh toán, ngày nhận dự kiến, mã tham chiếu NCC, ghi chú). Chỉ tạo sau khi người dùng xác nhận bản xem trước.', {
            'action': {'enum': ['start', 'update', 'cancel']}, 'company_id': {'type': 'integer'},
            'supplier_query': {'type': 'string'}, 'choose_first': {'type': 'boolean'},
            'partner_ref': {'type': 'string'}, 'payment_term_query': {'type': 'string'},
            'date_order': {'type': 'string'}, 'date_planned': {'type': 'string'}, 'notes': {'type': 'string'},
            'lines': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'product_query': {'type': 'string'}, 'quantity': {'type': 'number'},
                'price_unit': {'type': 'number'}, 'tax_ids': {'type': 'array', 'items': {'type': 'integer'}},
                'uom_id': {'type': 'integer'}, 'date_planned': {'type': 'string'},
            }}}}))
        definitions.append(('business.purchase_update', 'read', 'Chuẩn bị cập nhật RFQ nháp/đã gửi theo số chứng từ Odoo (sửa ghi chú, mã tham chiếu NCC, điều khoản thanh toán, ngày nhận, hoặc cập nhật/xóa dòng hàng).', {
            'purchase_name': {'type': 'string'}, 'company_id': {'type': 'integer'},
            'partner_ref': {'type': 'string'}, 'payment_term_query': {'type': 'string'},
            'date_order': {'type': 'string'}, 'date_planned': {'type': 'string'}, 'notes': {'type': 'string'},
            'lines': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'product_query': {'type': 'string'}, 'quantity': {'type': 'number'},
                'price_unit': {'type': 'number'}, 'date_planned': {'type': 'string'},
                'remove': {'type': 'boolean'},
            }}}}))
        definitions.append(('business.purchase_approval_button', 'read', 'Xử lý nút bấm gửi duyệt/duyệt/từ chối đơn mua trên Telegram.', {
            'action': {'enum': ['submit', 'approve', 'reject']},
            'token': {'type': 'string'},
        }))
        definitions.append(('business.stock_flow', 'read', 'Chuẩn bị thao tác vòng đời phiếu giao hàng / nhập kho / chuyển kho đúng mã phiếu Odoo (xác nhận, giữ hàng, bỏ giữ hàng, hoàn tất giao/nhận, trả hàng, hủy).', {
            'picking_name': {'type': 'string'},
            'action': {'enum': ['confirm', 'reserve', 'unreserve', 'validate', 'return', 'cancel']},
            'backorder': {'enum': ['create', 'cancel']},
            'company_id': {'type': 'integer'},
        }))
        definitions.append(('business.stock_create', 'read', 'Tạo phiếu giao hàng (outgoing), phiếu nhập kho (incoming) hoặc chuyển kho nội bộ (internal) trên Odoo. Chỉ tạo sau khi người dùng xác nhận bản xem trước.', {
            'action': {'enum': ['start', 'update', 'cancel']},
            'operation_type': {'enum': ['outgoing', 'incoming', 'internal']},
            'company_id': {'type': 'integer'},
            'partner_query': {'type': 'string'},
            'scheduled_date': {'type': 'string'},
            'origin': {'type': 'string'},
            'note': {'type': 'string'},
            'lines': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'product_query': {'type': 'string'}, 'quantity': {'type': 'number'},
            }}},
        }))
    for name, kind, description, props in definitions:
        registry.register(tool_type(name=name, category='business', action_type=kind, description=description,
                                    parameter_schema=dict(common, properties=props)))


def parse_command(text):
    words = text.split(maxsplit=2)
    operation = words[1] if len(words) > 1 else 'capabilities'
    raw = json.loads(words[2]) if len(words) > 2 else {}
    if not isinstance(raw, dict):
        raise ValueError('Cần JSON object.')
    if operation == 'preview':
        return IntentAnalysisResult(intent_type=IntentType.PREPARE_WRITE, tool_name='business.preview')
    if operation in ('capabilities', 'lookup'):
        return IntentAnalysisResult(intent_type=IntentType.READ, tool_name=f'business.{operation}', parameters=raw)
    if operation not in ('start', 'update', 'cancel'):
        raise ValueError('Bạn muốn tra cứu, tạo mới hay thay đổi chứng từ nào?')
    return IntentAnalysisResult(intent_type=IntentType.READ, tool_name='business.draft', parameters=dict(raw, action=operation))


def format_result(data):
    result = data.get('result') or data
    if not isinstance(result, dict):
        return [str(result)]
    output = [str(result.get('message') or data.get('message') or 'Kết quả thao tác Odoo:')]
    if result.get('approval_status'):
        states = {'pending': 'Chờ cấp trên duyệt', 'approved': 'Đã duyệt nội bộ', 'rejected': 'Đã từ chối duyệt'}
        output.append('Duyệt nội bộ: ' + states.get(result['approval_status'], 'Cần kiểm tra'))
    if result.get('status') == 'awaiting_input':
        output.append('Chưa hoàn tất toàn bộ quy trình. Không tự gửi lại bước đã làm.')
    if result.get('continuation_token'):
        output.append('Bước tiếp theo: chuẩn bị business.stock.backorder với options.continuation_token=' + result['continuation_token'])
    record = result.get('record') or {}
    labels = {'id': 'ID', 'name': 'Chứng từ', 'state': 'Trạng thái', 'active': 'Hoạt động', 'partner_id': 'Đối tác', 'user_id': 'Phụ trách', 'amount_untaxed': 'Trước thuế', 'amount_tax': 'Thuế', 'amount_total': 'Tổng', 'currency_id': 'Tiền tệ', 'url': 'Odoo'}
    for key, label in labels.items():
        if key in record:
            value = record[key]
            if key == 'state' and str(result.get('action', '')).startswith('business.sales.'):
                value = {'draft': 'Báo giá nháp', 'sent': 'Báo giá đã gửi; chờ khách chấp thuận',
                         'sale': 'Đơn bán đã xác nhận', 'cancel': 'Đã hủy'}.get(value, value)
            if key == 'state' and str(result.get('action', '')).startswith('business.purchases.'):
                value = {'draft': 'RFQ nháp', 'sent': 'RFQ đã gửi', 'to approve': 'Chờ phê duyệt',
                         'purchase': 'Đơn mua đã xác nhận', 'done': 'Đơn mua đã khóa', 'cancel': 'Đã hủy'}.get(value, value)
            if key == 'state' and str(result.get('action', '')).startswith('business.stock.'):
                value = {'draft': 'Phiếu nháp', 'waiting': 'Đang chờ hàng', 'confirmed': 'Đã xác nhận',
                         'assigned': 'Sẵn sàng thực hiện', 'done': 'Đã hoàn tất', 'cancel': 'Đã hủy'}.get(value, value)
            if isinstance(value, list) and len(value) == 2:
                value = value[1]
            output.append(f'{label}: {value}')
    if result.get('download_url'):
        output.append('Tải PDF: ' + result['download_url'])
    if result.get('delivery_status'):
        states = {'queued': 'Odoo đã xếp email vào hàng đợi gửi', 'sent': 'Odoo ghi nhận email đã gửi',
                  'unknown': 'Chưa xác minh được trạng thái gửi email trên Odoo',
                  'submitted': 'Odoo đã tiếp nhận yêu cầu gửi email'}
        output.append('Email: ' + states.get(result['delivery_status'], result['delivery_status']))
    if data.get('steps'):
        output.append('Các bước đã ghi nhận: ' + ', '.join(item['stage'] for item in data['steps']))
    return ['\n'.join(output)]


async def read(tool, params, context):
    from app.core.config import get_settings
    if not allowed_action(get_settings()):
        return ['Thao tác nghiệp vụ chưa được bật để nghiệm thu.']
    if tool == 'business.purchase_create':
        if not allowed_action(get_settings(), 'business.purchases.create'):
            return ['Thao tác mua hàng chưa được bật.']
        return await prepare_purchase_draft(params, context)
    if tool == 'business.purchase_update':
        if not allowed_action(get_settings(), 'business.purchases.update'):
            return ['Thao tác mua hàng chưa được bật.']
        return await prepare_purchase_update(params, context)
    if tool == 'business.purchase_approval_button':
        btn_action = str(params.get('action') or '').strip()
        token = str(params.get('token') or '').strip()
        if btn_action == 'edit':
            from app.business import purchase_optional
            res = await request(context, 'POST', 'business/purchases/edit-context', {'token': token})
            if res.get('status') != 'ok':
                return [str(res.get('message') or 'Không mở được chế độ chỉnh sửa đơn mua.')]
            return await purchase_optional.open_edit_menu(
                context,
                order_id=res['order_id'],
                order_name=res.get('order_name') or f"PO #{res['order_id']}",
                company_id=res.get('company_id'),
            )
        if btn_action == 'feedback':
            from app.business import purchase_optional
            res = await request(context, 'POST', 'business/purchases/approval-feedback', {'token': token})
            if res.get('status') != 'ok':
                return [str(res.get('message') or 'Yêu cầu duyệt đơn mua không còn hợp lệ.')]
            return purchase_optional.open_feedback_prompt(
                context,
                token=token,
                order_name=res.get('order_name') or f"PO #{res.get('order_id', '')}",
            )
        if btn_action == 'submit':
            res = await request(context, 'POST', 'business/purchases/approval-submit', {'token': token})
        elif btn_action in ('approve', 'reject'):
            res = await request(context, 'POST', 'business/purchases/approval-decision', {'token': token, 'decision': btn_action})
        else:
            return ['Thao tác duyệt đơn mua không hợp lệ.']
        return [str(res.get('message') or 'Đã xử lý yêu cầu phê duyệt đơn mua.')]
    if tool == 'business.stock_create':
        if not allowed_action(get_settings(), 'business.stock.create'):
            return ['Thao tác kho / giao hàng chưa được bật.']
        return await prepare_stock_draft(params, context)
    if tool == 'business.stock_flow':
        name = str(params.get('picking_name') or '').strip().upper()
        op = str(params.get('action') or '').strip()
        action = 'business.stock.' + op
        valid_stock = {
            'business.stock.confirm', 'business.stock.reserve',
            'business.stock.unreserve', 'business.stock.validate',
            'business.stock.return', 'business.stock.cancel',
        }
        if not name or len(name) > 100 or action not in valid_stock:
            return ['Hãy cung cấp mã phiếu kho/giao hàng (ví dụ WH/OUT/00018) và thao tác (xác nhận, giữ hàng, bỏ giữ hàng, hoàn tất, trả hàng hoặc hủy).']
        if not allowed_action(get_settings(), action):
            return ['Thao tác kho / giao hàng chưa được bật.']
        lookup = {'kind': 'stock', 'query': name}
        if params.get('company_id'):
            lookup['company_id'] = params['company_id']
        found = await request(context, 'POST', 'business/lookup', lookup)
        matches = [row for row in found.get('items', []) if str(row.get('name') or '').upper() == name]
        if len(matches) != 1 or found.get('has_more'):
            return [found.get('message') or f'Chưa xác định duy nhất phiếu kho {name}. Hãy kiểm tra mã phiếu trên Odoo.']
        row = matches[0]
        data: dict = {'target_id': row['id']}
        if isinstance(row.get('company_id'), list) and row['company_id']:
            data['company_id'] = row['company_id'][0]
        if action == 'business.stock.validate':
            move_lines = row.get('move_lines') or []
            moves = row.get('moves') or []
            exec_lines = []
            if move_lines:
                for ml in move_lines:
                    qty = ml.get('quantity')
                    if not qty:
                        qty = ml.get('qty_done') or 0
                    if not qty:
                        # Fallback to parent move demand quantity
                        m_ref = ml.get('move_id')
                        mid = m_ref[0] if isinstance(m_ref, list) and m_ref else m_ref
                        parent_m = next((m for m in moves if m.get('id') == mid), None)
                        qty = (parent_m or {}).get('product_uom_qty') or 0
                    entry: dict = {'id': ml['id'], 'quantity': float(qty)}
                    lot = ml.get('lot_id')
                    if isinstance(lot, list) and lot:
                        entry['lot_id'] = lot[0]
                    exec_lines.append(entry)
            else:
                for mv in moves:
                    if mv.get('state') in ('done', 'cancel'):
                        continue
                    qty = mv.get('product_uom_qty') or mv.get('quantity') or 0
                    exec_lines.append({'move_id': mv['id'], 'quantity': float(qty)})
            if not exec_lines:
                return [f'Phiếu {name} không có dòng hàng cần thực hiện.']
            data['lines'] = exec_lines
            data['options'] = {'backorder': params.get('backorder') or 'create'}
        elif action == 'business.stock.return':
            moves = [mv for mv in (row.get('moves') or []) if mv.get('state') == 'done']
            if not moves:
                return [f'Phiếu {name} chưa hoàn tất hoặc không có dòng hàng để trả.']
            loc = row.get('location_id')
            loc_id = loc[0] if isinstance(loc, list) and loc else None
            if not loc_id:
                return [f'Không xác định được vị trí trả hàng cho phiếu {name}.']
            ret_lines = []
            for mv in moves:
                qty = mv.get('quantity') or mv.get('product_uom_qty') or 0
                if qty > 0:
                    ret_lines.append({'move_id': mv['id'], 'quantity': float(qty)})
            if not ret_lines:
                return [f'Phiếu {name} không còn số lượng có thể trả hàng.']
            data['lines'] = ret_lines
            data['options'] = {'location_id': loc_id}
        return await read('business.draft', {'action': 'start', 'business_action': action, 'data': data}, context)
    if tool == 'business.purchase_flow':
        name = str(params.get('purchase_name') or '').strip().upper()
        op = str(params.get('action') or '').strip()
        action = 'business.purchases.' + op
        valid_flow = {
            'business.purchases.confirm', 'business.purchases.approve',
            'business.purchases.reject', 'business.purchases.cancel',
            'business.purchases.draft', 'business.purchases.copy',
            'business.purchases.delete', 'business.purchases.send',
        }
        if not name or len(name) > 100 or action not in valid_flow:
            return ['Hãy cung cấp số RFQ/đơn mua (ví dụ P00012) và thao tác muốn thực hiện (xác nhận, duyệt, từ chối, hủy, đưa về nháp, sao chép, xóa hoặc gửi email).']
        if not allowed_action(get_settings(), action):
            return ['Thao tác mua hàng chưa được bật.']
        lookup = {'kind': 'purchases', 'query': name}
        if params.get('company_id'):
            lookup['company_id'] = params['company_id']
        found = await request(context, 'POST', 'business/lookup', lookup)
        matches = [row for row in found.get('items', []) if str(row.get('name') or '').upper() == name]
        if len(matches) != 1 or found.get('has_more'):
            return [found.get('message') or f'Chưa xác định duy nhất đơn mua {name}. Hãy kiểm tra số chứng từ và công ty trên Odoo.']
        row = matches[0]
        data: dict = {'target_id': row['id']}
        if row.get('company_id'):
            data['company_id'] = row['company_id'][0]
        if action == 'business.purchases.send':
            recipient_ids: list[int] = []
            for email in params.get('recipient_emails') or []:
                c_res = await request(context, 'POST', 'business/lookup', {
                    'kind': 'contacts', 'query': str(email),
                    **({'company_id': data['company_id']} if data.get('company_id') else {}),
                })
                c_matches = [
                    item for item in c_res.get('items', [])
                    if str(item.get('email') or '').casefold() == str(email).casefold()
                ]
                if len(c_matches) != 1:
                    return [f'Không tìm thấy duy nhất liên hệ có email {email} trên Odoo.']
                recipient_ids.append(c_matches[0]['id'])
            partner = row.get('partner_id')
            partner_name = partner[1] if isinstance(partner, list) and len(partner) > 1 else 'Quý đối tác'
            if not recipient_ids and isinstance(partner, list) and partner:
                recipient_ids = [partner[0]]
            if not recipient_ids:
                return [f'Đơn mua {name} chưa có nhà cung cấp để gửi email. Hãy cung cấp email người nhận.']
            subject = str(params.get('subject') or f'Yêu cầu báo giá mua hàng {name}').strip()
            body = str(
                params.get('body')
                or f'Kính gửi {partner_name},\n\nVui lòng xem yêu cầu báo giá mua hàng {name} và phản hồi báo giá cho chúng tôi.\n\nTrân trọng.'
            ).strip()
            data['options'] = {'recipient_ids': recipient_ids, 'subject': subject, 'body': body}
        return await read('business.draft', {'action': 'start', 'business_action': action, 'data': data}, context)
    if tool in ('business.quotation_flow', 'business.quotation_chatter'):
        name = str(params.get('quotation_name') or '').upper().strip()
        action = 'business.sales.' + str(params.get('action') or '')
        if action == 'business.sales.request_approval':
            return ['Hãy gửi lại PDF của báo giá và bấm “Xác nhận gửi duyệt” trên đúng bản PDF đó.']
        permitted = QUOTE_FLOW_ACTIONS if tool == 'business.quotation_flow' else CHATTER_ACTIONS
        if action not in permitted or not name or len(name) > 100 or not allowed_action(get_settings(), action):
            return ['Bạn muốn xử lý bước nào và báo giá có số nào trên Odoo?']
        lookup = {'kind': 'sales', 'query': name}
        if params.get('company_id'):
            lookup['company_id'] = params['company_id']
        found = await request(context, 'POST', 'business/lookup', lookup)
        matches = [item for item in found.get('items', []) if str(item.get('name') or '').upper() == name]
        if len(matches) != 1:
            return [found.get('message') or 'Chưa xác định duy nhất báo giá trong công ty tài khoản hiện tại. Hãy cung cấp số báo giá và công ty chính xác.']
        row = matches[0]
        if tool == 'business.quotation_chatter':
            remember(context, 'quotation_chatter_target', {'name': name, 'company_id': row.get('company_id')})
        options = dict(params.get('options') or {})
        payload = {'target_id': row['id'], 'options': options}
        if row.get('company_id'):
            payload['company_id'] = row['company_id'][0]
        messages = await read('business.draft', {'action': 'start', 'business_action': action, 'data': payload}, context)
        draft = session(context).get('active_draft') or {}
        if draft.get('business_action') != action or draft.get('business_data', {}).get('target_id') != row['id']:
            return messages
        return messages
    if tool == 'business.capabilities':
        data = await request(context, 'GET', 'business/capabilities')
        if 'actions' not in data:
            return format_result(data)
        lines = ['Khả năng thao tác Odoo; mọi thay đổi cần xác nhận:']
        for item in data['actions']:
            lines.append(f"{item['action']}: {item['status']} — {item.get('reason') or item['title']}")
        return ['\n'.join(lines)]
    if tool == 'business.lookup':
        if not get_settings().ENABLE_BUSINESS_ACTIONS and params.get('kind') not in ('sales', 'contacts', 'attachments'):
            return ['Luồng duyệt báo giá chỉ hỗ trợ tra cứu báo giá, người nhận và file đính kèm.']
        data = await request(context, 'POST', 'business/lookup', params)
        remember(context, 'operation_candidates_' + str(params.get('kind')), {'at': time.time(), 'items': data.get('items', [])})
        return [json.dumps(data, ensure_ascii=False, default=str)]
    if tool != 'business.draft':
        return ['Công cụ nghiệp vụ không hợp lệ.']
    old = session(context).get('active_draft')
    action = params.get('action', 'update')
    if action == 'cancel':
        if old and old.get('draft_type') == 'business':
            invalidate(context, old)
            store.clear_active_draft(context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, database=context.database)
        return ['Đã hủy nháp nghiệp vụ cục bộ; không hủy chứng từ Odoo.']
    if action not in ('start', 'update'):
        return ['Bạn muốn lập nháp mới, sửa thông tin hay hủy nháp đang có?']
    if action == 'update' and (not old or old.get('draft_type') != 'business'):
        return ['Chưa có nháp nghiệp vụ. Hãy xác định thao tác muốn thực hiện trước.']
    business_action = params.get('business_action') or (old or {}).get('business_action')
    if not allowed_action(get_settings(), business_action):
        return ['Thao tác này chưa được bật; hiện chỉ bật luồng duyệt và gửi báo giá.']
    if business_action not in ACTIONS:
        return ['Thao tác chưa có trong catalog. Dùng business.capabilities để kiểm tra.']
    data = copy.deepcopy(params.get('data') or {})
    if not isinstance(data, dict) or set(data) - {'company_id', 'target_id', 'target_kind', 'values', 'lines', 'options'}:
        return ['Nháp chỉ nhận company_id, target_id, target_kind, values, lines, options.']
    if business_action in CHATTER_ACTIONS:
        opts = data.get('options') or {}
        if not isinstance(opts, dict):
            return ['Thông tin hoạt động không hợp lệ.']
        queries = []
        if opts.get('user_query'):
            queries.append(('users', opts.pop('user_query')))
        queries.extend(('contacts', query) for query in opts.pop('attendee_queries', []))
        attendees = list(opts.get('attendee_ids', []))
        for kind, query in queries:
            if not isinstance(query, str) or not query.strip():
                return ['Tên người được chọn không hợp lệ.']
            found = await request(context, 'POST', 'business/lookup', {'kind': kind, 'query': query, **(
                {'company_id': data['company_id']} if data.get('company_id') else {})})
            matches = [item for item in found.get('items', []) if query.casefold() in {
                str(item.get('name') or '').casefold(), str(item.get('display_name') or '').casefold(),
                str(item.get('email') or '').casefold()}]
            if len(matches) != 1 or found.get('has_more'):
                return [f'Không xác định duy nhất người dùng {query}. Vui lòng cung cấp tên chính xác hoặc ID Odoo.']
            if kind == 'users':
                opts['user_id'] = matches[0]['id']
            else:
                attendees.append(matches[0]['id'])
        if attendees:
            opts['attendee_ids'] = list(dict.fromkeys(attendees))
        data['options'] = opts
    if len(json.dumps(data, ensure_ascii=False)) > 150000:
        return ['Nháp quá lớn; hãy chia thành từng chứng từ.']
    payload = copy.deepcopy(old.get('business_data', {})) if action == 'update' else {}
    if action == 'update' and business_action != old['business_action']:
        return ['Đổi thao tác cần bắt đầu nháp mới để tránh giữ nhầm dữ liệu.']
    # A changed business dependency invalidates earlier prices/taxes/line selections.
    incoming_values = data.get('values', {})
    previous_values = payload.get('values', {})
    if not isinstance(incoming_values, dict):
        return ['values phải là object.']
    changed = any(key in incoming_values and incoming_values[key] != previous_values.get(key) for key in ('partner_id', 'pricelist_id', 'currency_id', 'fiscal_position_id', 'picking_type_id', 'location_id', 'location_dest_id'))
    company_changed = 'company_id' in data and data['company_id'] != payload.get('company_id')
    if company_changed or (changed and business_action != 'business.purchases.create'):
        payload.pop('lines', None)
    for field in ('values', 'options'):
        if field in data:
            if not isinstance(data[field], dict):
                return [field + ' phải là object.']
            payload[field] = {**payload.get(field, {}), **data.pop(field)}
    payload.update(data)
    draft = {'draft_type': 'business', 'business_action': business_action, 'business_data': payload, 'draft_id': (old or {}).get('draft_id') if action == 'update' else None, 'version': 1, 'updated_at': time.time()}
    saved = store.set_active_draft(context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, draft,
            database=context.database, expected_version=old.get('version') if old else None, expected_draft_id=old.get('draft_id') if old else None)
    if not saved:
        return ['Nháp đã thay đổi ở tác vụ khác; hãy đọc lại trước khi sửa.']
    if old:
        invalidate(context, old)
    if business_action in QUOTE_FLOW_ACTIONS | CHATTER_ACTIONS | PURCHASE_FLOW_ACTIONS | STOCK_FLOW_ACTIONS:
        return await prepare(context)
    return [f"Đã lưu thông tin, phiên bản {saved['version']}. Chưa thay đổi Odoo. Bạn có thể yêu cầu xem trước để kiểm tra trước khi thực hiện."]


def _lookup_fallback_queries(query: str) -> list[str]:
    """Generate progressively relaxed fallback search queries when exact Odoo ilike returns 0 rows."""
    q = (query or "").strip()
    if not q:
        return []
    candidates: list[str] = []
    # 1. Strip trailing " - NA" / " - XX" UI tag
    if re.search(r'\s+-\s+[^-]{1,12}$', q):
        candidates.append(re.sub(r'\s+-\s+[^-]{1,12}$', '', q).strip())
    # 2. Extract [CODE] and name without [CODE]
    m_code = re.match(r'^\[([^\]]+)\]\s*(.*)$', q)
    if m_code:
        if m_code[1].strip():
            candidates.append(m_code[1].strip())
        if m_code[2].strip():
            candidates.append(m_code[2].strip())
    # 3. Extract "Model: X" or "Model X"
    m_model = re.search(r'model\s*[:\-]?\s*([a-zA-Z0-9][a-zA-Z0-9._ -]{1,24})', q, re.I)
    if m_model:
        candidates.append(m_model[1].strip())
    # 4. Distinctive tokens (longest / alphanumeric words) for multi-word queries
    words = [w for w in re.findall(r'[a-zA-Z0-9À-ỹ]+', q) if len(w) >= 3]
    if len(words) >= 2:
        alnum = [w for w in words if re.search(r'[a-zA-Z]', w) and re.search(r'\d', w)]
        candidates.extend(alnum[:2])
        candidates.append(" ".join(words[:2]))
        longest = max(words, key=len)
        if len(longest) >= 4:
            candidates.append(longest)
    seen = {q.casefold()}
    unique = []
    for c in candidates:
        c = c.strip()
        if c and c.casefold() not in seen:
            seen.add(c.casefold())
            unique.append(c)
    return unique[:4]


async def _resolve_purchase_lookup(context, kind, query, label, company=None, choose_first=False):
    async def _do_lookup(q):
        lookup = {'kind': kind, 'query': q}
        if company:
            lookup['company_id'] = company
        return await request(context, 'POST', 'business/lookup', lookup)

    found = await _do_lookup(query)
    if found.get('status') != 'ok':
        return None, found.get('message') or f'Không tra cứu được {label}.'
    items = found.get('items', [])
    effective_query = query
    # Smart self-correcting lookup: try relaxed fallback queries before giving up
    if not items and query:
        for alt_q in _lookup_fallback_queries(query):
            retry = await _do_lookup(alt_q)
            if retry.get('status') == 'ok' and retry.get('items'):
                found = retry
                items = retry['items']
                effective_query = alt_q
                break
    # If purchase_products still returns empty, check products to give a helpful diagnostic when purchase_ok=False
    if not items and kind == 'purchase_products' and effective_query:
        for check_q in [effective_query, *_lookup_fallback_queries(query)[:2]]:
            alt = await request(context, 'POST', 'business/lookup', {
                'kind': 'products', 'query': check_q,
                **({'company_id': company} if company else {}),
            })
            if alt.get('status') == 'ok' and alt.get('items'):
                alt_names = ', '.join(str(r.get('display_name') or r.get('name')) for r in alt['items'][:3])
                return None, f'Sản phẩm “{query}” có trên Odoo ({alt_names}) nhưng chưa bật tùy chọn “Có thể mua” (purchase_ok). Hãy bật “Có thể mua” trên Odoo hoặc chọn sản phẩm mua hàng khác.'

    normalized = effective_query.casefold().strip()
    folded = fold_vi(effective_query)
    orig_folded = fold_vi(query)
    exact = [row for row in items if normalized and normalized in {
        str(row.get(key) or '').casefold() for key in ('name', 'display_name', 'default_code')}]
    if not exact and folded:
        exact = [row for row in items if folded in {
            fold_vi(str(row.get(key) or '')) for key in ('name', 'display_name', 'default_code')}]
    if not exact and orig_folded and len(items) > 1:
        q_tokens = set(orig_folded.split())
        scored = [
            (len(q_tokens & set(fold_vi(str(row.get('display_name') or row.get('name') or '')).split())), row)
            for row in items
        ]
        best_score = max(s for s, _ in scored)
        if best_score >= 2:
            top = [r for s, r in scored if s == best_score]
            if len(top) == 1:
                exact = top
    if items and not normalized and choose_first:
        return items[0]['id'], None
    if not found.get('has_more') and len(exact or items) == 1:
        return (exact or items)[0]['id'], None
    pool = (exact or items)[:4]
    names_list = [str(row.get('display_name') or row.get('name')) for row in pool if row.get('display_name') or row.get('name')]
    if names_list:
        prompt = f'Có nhiều {label} khớp trên Odoo, bạn chọn 1 mục bên dưới:\n' + '\n'.join(f'• {n}' for n in names_list)
        return None, OptionButtonsMessage(prompt, names_list)
    return None, f'Không tìm thấy {label} “{query}” trên Odoo.'


async def _resolve_payment_term(context, query, company=None):
    for candidate in payment_term_candidates(query):
        term_id, err = await _resolve_purchase_lookup(context, 'payment_terms', candidate, 'điều khoản thanh toán', company=company)
        if term_id is not None:
            return term_id, None
    return None, f'Không tìm thấy điều khoản thanh toán “{query}” trên Odoo.'


def _normalize_date_field(value):
    if not isinstance(value, str) or not value.strip():
        return value
    return parse_purchase_date(value) or value


async def prepare_purchase_draft(params, context):
    from app.business import purchase_optional

    mode = params.get('action', 'start')
    if mode == 'cancel':
        return await read('business.draft', {'action': 'cancel', 'business_action': 'business.purchases.create'}, context)
    if mode == 'optional_reply':
        return await purchase_optional.handle_reply(str(params.get('text') or ''), context)
    current = session(context).get('active_draft') or {}
    saved = current.get('business_data', {}) if mode == 'update' and current.get('business_action') == 'business.purchases.create' else {}
    if mode not in ('start', 'update') or (mode == 'update' and not saved and current.get('business_action') != 'business.purchases.create'):
        return ['Hãy bắt đầu RFQ mới trước khi bổ sung thông tin.']
    company = params.get('company_id') or saved.get('company_id')
    choose_first = params.get('choose_first') is True
    supplier = params.get('supplier_query')
    lines = params.get('lines')
    payment_term_query = params.get('payment_term_query')
    header_values = {}
    for field in ('partner_ref', 'notes'):
        if params.get(field):
            header_values[field] = str(params[field]).strip()
    for dfield in ('date_order', 'date_planned'):
        if params.get(dfield):
            header_values[dfield] = _normalize_date_field(params[dfield])

    data = {'company_id': company} if company else {}
    if lines is not None and (not isinstance(lines, list) or len(lines) > 100 or any(
            not isinstance(line, dict) or not isinstance(line.get('product_query', ''), str)
            or type(line.get('quantity')) not in (int, float) or not line['quantity'] > 0 for line in lines)):
        return ['Hãy cung cấp sản phẩm và số lượng lớn hơn 0 cho từng dòng RFQ.']
    if supplier is not None and not isinstance(supplier, str):
        return ['Hãy cung cấp tên nhà cung cấp trên Odoo.']
    if not choose_first and any(not line.get('product_query', '').strip() for line in lines or []):
        return ['Hãy cung cấp tên hoặc mã sản phẩm trên Odoo.']

    # Empty start ("tạo đơn mua mới") -> open local draft and guide user clearly
    if mode == 'start' and supplier is None and not lines and not choose_first and not header_values and not payment_term_query:
        draft = {'draft_type': 'business', 'business_action': 'business.purchases.create', 'business_data': data, 'draft_id': None, 'version': 1, 'updated_at': time.time()}
        store.set_active_draft(context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, draft, database=context.database)
        return ['Để tạo RFQ mua hàng, bạn vui lòng cung cấp:\n• Nhà cung cấp\n• Sản phẩm và số lượng (có thể kèm đơn giá, ngày nhận dự kiến, điều khoản thanh toán hoặc ghi chú)\nChưa thay đổi Odoo.']

    jobs = []
    if supplier or (choose_first and not saved.get('values', {}).get('partner_id')):
        jobs.append(('supplier', _resolve_purchase_lookup(context, 'suppliers', supplier or '', 'nhà cung cấp', company=company, choose_first=choose_first)))
    if payment_term_query:
        jobs.append(('payment_term', _resolve_payment_term(context, payment_term_query, company=company)))
    for index, line in enumerate(lines or []):
        query = line.get('product_query', '').strip()
        jobs.append((index, _resolve_purchase_lookup(context, 'purchase_products', query, 'sản phẩm', company=company, choose_first=choose_first)))
    results = dict(zip((key for key, _ in jobs), await asyncio.gather(*(job for _, job in jobs)), strict=True))

    if 'supplier' in results and results['supplier'][0] is not None:
        header_values['partner_id'] = results['supplier'][0]
    if 'payment_term' in results and results['payment_term'][0] is not None:
        header_values['payment_term_id'] = results['payment_term'][0]
    if header_values:
        data['values'] = header_values
    if lines is not None and all(results[i][0] is not None for i in range(len(lines))):
        resolved_lines = []
        for index, line in enumerate(lines):
            item = {key: value for key, value in line.items() if key != 'product_query'}
            if item.get('date_planned'):
                item['date_planned'] = _normalize_date_field(item['date_planned'])
            elif header_values.get('date_planned'):
                item['date_planned'] = header_values['date_planned']
            item['product_id'] = results[index][0]
            resolved_lines.append(item)
        data['lines'] = resolved_lines

    messages = [error for _, error in results.values() if error]
    if messages:
        # Always persist the purchase draft on start (replacing any stale quotation draft!)
        # and preserve any already-resolved fields so the user doesn't have to re-enter them.
        if mode == 'start' or data.get('values') or data.get('lines'):
            merged = copy.deepcopy(saved) if mode == 'update' else {}
            if data.get('company_id'):
                merged['company_id'] = data['company_id']
            if data.get('values'):
                merged['values'] = {**merged.get('values', {}), **data['values']}
            if data.get('lines'):
                merged['lines'] = data['lines']
            draft = {
                'draft_type': 'business', 'business_action': 'business.purchases.create',
                'business_data': merged, 'draft_id': current.get('draft_id') if mode == 'update' else None,
                'version': 1, 'updated_at': time.time(),
            }
            store.set_active_draft(
                context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, draft,
                database=context.database,
                expected_version=current.get('version') if mode == 'update' else None,
                expected_draft_id=current.get('draft_id') if mode == 'update' else None,
            )
        if len(messages) == 1 and isinstance(messages[0], OptionButtonsMessage):
            return [messages[0]]
        return ['\n'.join(messages)]

    merged_payload = copy.deepcopy(saved) if mode == 'update' else {}
    if data.get('company_id'):
        merged_payload['company_id'] = data['company_id']
    if data.get('values'):
        merged_payload['values'] = {**merged_payload.get('values', {}), **data['values']}
    if data.get('lines'):
        merged_payload['lines'] = data['lines']
    has_required = bool((merged_payload.get('values') or {}).get('partner_id')) and bool(merged_payload.get('lines'))
    opt_done = (current.get('optional_fields') or {}).get('stage') == 'done'
    if has_required and not opt_done and not choose_first:
        return await purchase_optional.open_menu(context, current if mode == 'update' else {}, merged_payload)
    return await read('business.draft', {'action': mode, 'business_action': 'business.purchases.create', 'data': data}, context)


async def prepare_purchase_update(params, context):
    name = str(params.get('purchase_name') or '').strip().upper()
    if not name or len(name) > 100:
        return ['Hãy cung cấp số RFQ/đơn mua cần cập nhật (ví dụ P00012).']
    company = params.get('company_id')
    lookup = {'kind': 'purchases', 'query': name}
    if company:
        lookup['company_id'] = company
    found = await request(context, 'POST', 'business/lookup', lookup)
    matches = [row for row in found.get('items', []) if str(row.get('name') or '').upper() == name]
    if len(matches) != 1 or found.get('has_more'):
        return [found.get('message') or f'Chưa xác định duy nhất đơn mua {name}. Hãy kiểm tra số chứng từ trên Odoo.']
    row = matches[0]
    po_company = company or (row['company_id'][0] if isinstance(row.get('company_id'), list) and row['company_id'] else None)
    data: dict = {'target_id': row['id']}
    if po_company:
        data['company_id'] = po_company

    values: dict = {}
    for field in ('partner_ref', 'notes'):
        if params.get(field) is not None:
            values[field] = str(params[field]).strip()
    for dfield in ('date_order', 'date_planned'):
        if params.get(dfield):
            values[dfield] = _normalize_date_field(params[dfield])
    if params.get('payment_term_query'):
        term_id, err = await _resolve_payment_term(context, params['payment_term_query'], company=po_company)
        if err:
            return [err]
        values['payment_term_id'] = term_id
    if values:
        data['values'] = values

    incoming_lines = params.get('lines')
    if incoming_lines:
        existing_lines = row.get('lines') or []
        updated_lines = []
        for req_line in incoming_lines:
            prod_query = str(req_line.get('product_query') or '').strip()
            if not prod_query:
                return ['Hãy cung cấp tên hoặc mã sản phẩm cần cập nhật trên RFQ.']
            prod_id, err = await _resolve_purchase_lookup(context, 'purchase_products', prod_query, 'sản phẩm', company=po_company)
            if err:
                return [err]
            matched_existing = next(
                (el for el in existing_lines if isinstance(el.get('product_id'), list) and el['product_id'] and el['product_id'][0] == prod_id),
                None,
            )
            if req_line.get('remove'):
                if not matched_existing:
                    return [f'Không tìm thấy dòng sản phẩm “{prod_query}” trên {name} để xóa.']
                updated_lines.append({'id': matched_existing['id'], 'remove': True})
                continue
            qty = req_line.get('quantity')
            if qty is None and matched_existing:
                qty = matched_existing.get('product_qty')
            if type(qty) not in (int, float) or qty <= 0:
                return [f'Số lượng của sản phẩm “{prod_query}” phải lớn hơn 0.']
            price = req_line.get('price_unit')
            if price is None and matched_existing:
                price = matched_existing.get('price_unit', 0)
            if price is None:
                return [f'Hãy cung cấp đơn giá cho dòng mới “{prod_query}” khi cập nhật {name}.']
            raw_taxes = (matched_existing or {}).get('tax_ids') or (matched_existing or {}).get('taxes_id') or []
            uom = (matched_existing or {}).get('product_uom_id') or (matched_existing or {}).get('product_uom')
            uom_id = uom[0] if isinstance(uom, list) and uom else None
            planned = _normalize_date_field(req_line.get('date_planned')) or (matched_existing or {}).get('date_planned') or values.get('date_planned')
            if planned and isinstance(planned, str) and '+' not in planned and 'Z' not in planned:
                planned = _normalize_date_field(planned)
            line_payload: dict = {
                'product_id': prod_id,
                'quantity': qty,
                'price_unit': price,
                'price_confirmed': True,
                'tax_ids': list(raw_taxes) if isinstance(raw_taxes, list) else [],
            }
            if matched_existing:
                line_payload['id'] = matched_existing['id']
            if uom_id:
                line_payload['uom_id'] = uom_id
            if planned:
                line_payload['date_planned'] = planned
            updated_lines.append(line_payload)
        data['lines'] = updated_lines

    if not data.get('values') and not data.get('lines'):
        return [f'Bạn muốn sửa thông tin gì trên {name} (số lượng, đơn giá, ngày nhận, điều khoản thanh toán, mã tham chiếu NCC hay ghi chú)?']
    return await read('business.draft', {'action': 'start', 'business_action': 'business.purchases.update', 'data': data}, context)


async def prepare_stock_draft(params, context):
    mode = params.get('action', 'start')
    if mode == 'cancel':
        return await read('business.draft', {'action': 'cancel', 'business_action': 'business.stock.create'}, context)
    current = session(context).get('active_draft') or {}
    saved = current.get('business_data', {}) if mode == 'update' and current.get('business_action') == 'business.stock.create' else {}
    if mode not in ('start', 'update') or (mode == 'update' and current.get('business_action') != 'business.stock.create'):
        return ['Hãy bắt đầu tạo phiếu giao hàng/phiếu kho mới trước khi bổ sung thông tin.']

    company = params.get('company_id') or saved.get('company_id')
    op_type = params.get('operation_type') or (saved.get('_operation_type') if isinstance(saved, dict) else None) or 'outgoing'
    data: dict = {'company_id': company} if company else {}
    header_values: dict = {}

    # Resolve picking_type_id + default locations on start (or if not yet in saved draft)
    saved_vals = saved.get('values') or {}
    if not saved_vals.get('picking_type_id'):
        pt_res = await request(context, 'POST', 'business/lookup', {
            'kind': 'picking_types', 'query': '',
            **({'company_id': company} if company else {}),
        })
        pt_items = pt_res.get('items', []) if pt_res.get('status') == 'ok' else []
        chosen_pt = next((pt for pt in pt_items if pt.get('code') == op_type), None)
        if not chosen_pt:
            return [f'Không tìm thấy loại phiếu kho ({op_type}) trên Odoo.']
        header_values['picking_type_id'] = chosen_pt['id']
        src = chosen_pt.get('default_location_src_id')
        dst = chosen_pt.get('default_location_dest_id')
        if isinstance(src, list) and src:
            header_values['location_id'] = src[0]
        if isinstance(dst, list) and dst:
            header_values['location_dest_id'] = dst[0]
    if not saved_vals.get('move_type'):
        header_values['move_type'] = params.get('move_type') or 'direct'

    for field in ('origin', 'note'):
        if params.get(field):
            header_values[field] = str(params[field]).strip()
    if params.get('scheduled_date'):
        header_values['scheduled_date'] = _normalize_date_field(params['scheduled_date'])

    partner_query = params.get('partner_query')
    lines = params.get('lines')

    if lines is not None and (not isinstance(lines, list) or len(lines) > 100 or any(
            not isinstance(line, dict) or not str(line.get('product_query', '')).strip()
            or type(line.get('quantity')) not in (int, float) or not line['quantity'] > 0 for line in lines)):
        return ['Hãy cung cấp tên/mã sản phẩm và số lượng lớn hơn 0 cho từng dòng phiếu kho.']

    op_label = {'outgoing': 'phiếu giao hàng', 'incoming': 'phiếu nhập kho', 'internal': 'phiếu chuyển kho'}.get(op_type, 'phiếu kho')
    if mode == 'start' and not partner_query and not lines:
        draft_data = {**data, 'values': header_values}
        draft = {'draft_type': 'business', 'business_action': 'business.stock.create', 'business_data': draft_data, 'draft_id': None, 'version': 1, 'updated_at': time.time()}
        store.set_active_draft(context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, draft, database=context.database)
        return [f'Để tạo {op_label}, bạn vui lòng cung cấp:\n• Đối tác (khách hàng / nhà cung cấp)\n• Sản phẩm và số lượng (có thể kèm ngày giao dự kiến, chứng từ gốc hoặc ghi chú)\nChưa thay đổi Odoo.']

    jobs = []
    if partner_query:
        jobs.append(('partner', _resolve_purchase_lookup(context, 'contacts', partner_query, 'đối tác', company=company)))
    for index, line in enumerate(lines or []):
        jobs.append((index, _resolve_purchase_lookup(context, 'products', line['product_query'].strip(), 'sản phẩm', company=company)))
    results = dict(zip((key for key, _ in jobs), await asyncio.gather(*(job for _, job in jobs)), strict=True))

    if 'partner' in results and results['partner'][0] is not None:
        header_values['partner_id'] = results['partner'][0]
    if header_values:
        data['values'] = header_values
    if lines is not None and all(results[i][0] is not None for i in range(len(lines))):
        data['lines'] = [{'product_id': results[i][0], 'quantity': lines[i]['quantity']} for i in range(len(lines))]

    messages = [error for _, error in results.values() if error]
    if messages:
        merged = copy.deepcopy(saved) if mode == 'update' else {}
        if data.get('company_id'):
            merged['company_id'] = data['company_id']
        if data.get('values'):
            merged['values'] = {**merged.get('values', {}), **data['values']}
        if data.get('lines'):
            merged['lines'] = data['lines']
        draft = {
            'draft_type': 'business', 'business_action': 'business.stock.create',
            'business_data': merged, 'draft_id': current.get('draft_id') if mode == 'update' else None,
            'version': 1, 'updated_at': time.time(),
        }
        store.set_active_draft(
            context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, draft,
            database=context.database,
            expected_version=current.get('version') if mode == 'update' else None,
            expected_draft_id=current.get('draft_id') if mode == 'update' else None,
        )
        if len(messages) == 1 and isinstance(messages[0], OptionButtonsMessage):
            return [messages[0]]
        return ['\n'.join(messages)]
    return await read('business.draft', {'action': mode, 'business_action': 'business.stock.create', 'data': data}, context)


def invalidate(context, draft):
    if draft.get('draft_id'):
        confirmation_service.cancel_pending_draft_confirmations(user_id=context.user_id, chat_id=context.chat_id,
            telegram_binding_id=context.telegram_binding_id or 'default', draft_id=draft['draft_id'])


def preview_text(preview):
    lines = [str(preview.get('title', 'Xem trước thao tác Odoo'))]
    if preview.get('company_name'):
        lines.append('Công ty: ' + str(preview['company_name']))
    target = preview.get('target') or {}
    if target:
        if 'approval_state' in preview:
            lines.append('Báo giá: ' + str(target.get('name') or target.get('display_name')))
        else:
            lines.append('Bản ghi: ' + str(target.get('display_name') or target.get('name') or target.get('id')) + ' (ID ' + str(target.get('id')) + ')')
    changes = preview.get('changes', [])
    if 'approval_state' in preview:
        states = {'not_requested': 'Chưa yêu cầu duyệt', 'pending': 'Chờ cấp trên duyệt', 'approved': 'Đã duyệt', 'rejected': 'Đã từ chối'}
        lines.append('Duyệt nội bộ: ' + states.get(preview['approval_state'], 'Chưa xác minh'))
        for rule in preview.get('approval_rules', []):
            names = ', '.join(rule.get('approver_names') or [])
            lines.append('Bước duyệt: ' + str(rule.get('name') or 'Duyệt nội bộ') + ((' — ' + names) if names else ''))
    for field in preview.get('quotation_fields', []):
        value = field.get('value')
        lines.append(str(field['label']) + ': ' + ('' if value is None else str(value)))
    for index, item in enumerate(preview.get('quotation_lines', []), 1):
        product = item.get('product_id') or []
        name = product[1] if isinstance(product, list) and len(product) > 1 else item.get('name', '')
        lines.append(f"{index}. {name}; Số lượng: {item.get('product_uom_qty', '')}; Đơn giá: {item.get('price_unit', '')}; Chiết khấu: {item.get('discount', '')}")
    if isinstance(changes, list):
        for change in changes:
            if change.get('field') == 'company_id':
                continue
            label = change.get('label') or change.get('field')
            before = change.get('before')
            after = change.get('after')
            if before is None:
                lines.append(f"{label}: {after}")
            else:
                lines.append(f"{label}: {before} → {after}")
    elif changes:
        lines.append('Thay đổi: ' + json.dumps(changes, ensure_ascii=False, default=str))
    for key, label in {'affected_variant_ids': 'Biến thể cùng bị ảnh hưởng', 'lines': 'Dòng hàng', 'recipients': 'Người nhận', 'subject': 'Tiêu đề', 'body': 'Nội dung', 'attachments': 'File đính kèm', 'activity_type': 'Loại hoạt động', 'summary': 'Tóm tắt', 'date_deadline': 'Ngày đến hạn', 'start': 'Bắt đầu', 'stop': 'Kết thúc', 'assignee': 'Phân công cho', 'attendees': 'Người tham dự', 'execution_lines': 'Số lượng thực hiện', 'backorder': 'Phần còn lại', 'return_lines': 'Dòng trả hàng', 'destination': 'Vị trí đích', 'before_quantity': 'Tồn trước', 'counted_quantity': 'Số kiểm kê', 'difference': 'Chênh lệch', 'conversion': 'Chuyển đổi CRM', 'lost_reason': 'Lý do thua', 'feedback': 'Kết quả hoạt động', 'notice': 'Lưu ý'}.items():
        if key in preview and preview[key] is not None:
            value = preview[key]
            if key == 'lines' and isinstance(value, list) and all(isinstance(item, dict) for item in value):
                lines.append(label + ':')
                for idx, item in enumerate(value, 1):
                    if item.get('remove_line_id'):
                        lines.append(f"  {idx}. Xóa dòng ID {item['remove_line_id']}")
                        continue
                    prod_name = item.get('product') or item.get('name') or f"SP #{item.get('product_id', '')}"
                    qty = item.get('quantity', '')
                    unit = f" {item['unit']}" if item.get('unit') else ''
                    parts = [f"SL: {qty}{unit}"]
                    if item.get('price_unit') is not None:
                        try:
                            parts.append(f"Đơn giá: {float(item['price_unit']):,.0f}")
                        except (TypeError, ValueError):
                            parts.append(f"Đơn giá: {item['price_unit']}")
                    if isinstance(item.get('taxes'), list):
                        parts.append("Thuế: " + (", ".join(str(t) for t in item['taxes']) if item['taxes'] else "Không thuế"))
                    if item.get('date_planned'):
                        parts.append(f"Ngày nhận: {str(item['date_planned'])[:10]}")
                    lines.append(f"  {idx}. {prod_name} — " + " · ".join(parts))
            elif key == 'recipients' and isinstance(value, list) and all(isinstance(item, dict) for item in value):
                formatted_rec = ", ".join(
                    f"{item.get('name', '')} <{item.get('email', '')}>".strip() if item.get('email') else str(item.get('name', ''))
                    for item in value
                )
                lines.append(f"{label}: {formatted_rec}")
            elif key in ('execution_lines', 'return_lines') and isinstance(value, list) and all(isinstance(item, dict) for item in value):
                lines.append(label + ':')
                for idx, item in enumerate(value, 1):
                    ref = f"Dòng #{item.get('id') or item.get('move_id')}"
                    lot_str = f" · Lô/Serial ID {item['lot_id']}" if item.get('lot_id') else ""
                    lines.append(f"  {idx}. {ref} — SL: {item.get('quantity', '')}{lot_str}")
            elif key == 'backorder' and isinstance(value, str):
                bo_label = {'create': 'Tạo phiếu phần còn lại (Backorder)', 'cancel': 'Không tạo phiếu phần còn lại'}.get(value, value)
                lines.append(f"{label}: {bo_label}")
            else:
                lines.append(label + ': ' + (json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value)))
    return '\n'.join(lines)


async def prepare(context):
    from app.core.config import get_settings
    if not allowed_action(get_settings()) or not context.owner_key:
        return ['Chức năng chưa bật hoặc không xác minh được tài khoản.']
    draft = session(context).get('active_draft') or {}
    if draft.get('draft_type') != 'business':
        return ['Chưa có nháp nghiệp vụ.']
    if not allowed_action(get_settings(), draft.get('business_action')):
        return ['Thao tác này chưa được bật.']
    response = await request(context, 'POST', 'business/prepare', {'action': draft['business_action'], 'payload': draft['business_data']})
    if response.get('status') == 'needs_information':
        remember(context, 'business_missing', {'action': draft['business_action'], 'requirements': response.get('requirements', [])})
        requirements = response.get('requirements', [])
        lines = ['Cần bổ sung thông tin:']
        for item in requirements:
            label = item.get('label') or item['field']
            if item.get('choices'):
                label += ': ' + ', '.join(f'{caption} ({value})' for value, caption in item['choices'])
            field_labels = {'values.partner_id': 'Nhà cung cấp', 'lines': 'Sản phẩm và số lượng',
                            'target_id': 'Số chứng từ trên Odoo', 'options.body': 'Nội dung', 'options.recipient': 'Email người nhận', 'options.activity_type': 'Loại hoạt động',
                            'options.summary': 'Tóm tắt cuộc họp', 'options.start': 'Thời gian bắt đầu', 'options.stop': 'Thời gian kết thúc'}
            lines.append(field_labels.get(item['field']) or label)
        if not requirements:
            lines.extend(response.get('missing', []))
        return ['\n'.join(lines) + '\nChưa thay đổi Odoo.']
    if response.get('status') != 'ready' or not response.get('prepared_id'):
        return format_result(response)
    text = preview_text(response['preview'])
    if len(text) > 18000:
        return ['Bản xem trước quá dài để kiểm tra đầy đủ trong chat; hãy giảm số dòng. Chưa tạo xác nhận.']
    # Invalidate older approvals even if the same draft was previewed twice.
    invalidate(context, draft)
    _, confirmation = confirmation_service.create_confirmation(user_id=context.user_id, chat_id=context.chat_id,
        telegram_binding_id=context.telegram_binding_id or 'default', action=draft['business_action'],
        arguments={'prepared_id': response['prepared_id']}, preview_text=text, owner_key=context.owner_key,
        draft_id=draft['draft_id'], draft_version=draft['version'], telegram_user_id=context.telegram_user_id)
    return [confirmation]


PROMPT = '''
THAO TÁC ODOO: business.* phục vụ Bán hàng, Mua hàng, CRM, Liên hệ, Tồn kho và sản phẩm.
Trước thao tác mới dùng business.capabilities; nếu không hỗ trợ hoặc không có quyền thì giải thích, không đổi công cụ để vượt chặn.
Mọi thao tác ghi đều phải qua business.draft rồi business.preview và người dùng xác nhận. Không tự xác nhận hoặc hiểu yêu cầu tạo/sửa là đã xác nhận bản xem trước.
business.draft data gồm company_id, target_id, target_kind (ghi chú), values, lines, options; không có model/method/context RPC.
Tra cứu bằng business.lookup, cho người dùng chọn nếu trùng tên. Không đoán ID, số lượng, giá, thuế, người nhận, lô/serial, lý do thua hoặc cách xử lý phần còn lại.
Hỏi theo missing từ Gateway. Dòng bán/mua dùng product_id, quantity, price_unit, price_confirmed=true khi người dùng chốt, tax_ids ([] nghĩa là không thuế), uom_id; dòng mua cần date_planned.
Sửa dòng hàng cần id thật, remove=true để xóa đúng dòng; không gửi command ORM. Thay product/đối tác/tiền tệ cần chốt lại giá, thuế và đơn vị.
Gửi email là sales.send/purchases.send riêng: options.recipient_ids,subject,body,attachment_ids. Không tự thêm người nhận/file hoặc suy ra email đã đến nơi từ trạng thái submitted.
Kho validate cần mọi dòng thực hiện lines={id,quantity,lot_id} và options.backorder=create/cancel; return cần lines={move_id,quantity}, options.location_id; inventory cần target_id quant và options.counted_quantity.
Ghi chú cần target_kind và target_id, options.body. Hoạt động create dùng values.res_model_id (tra cứu models),res_id,activity_type_id,user_id,date_deadline; done dùng options.feedback.
CRM convert dùng options.partner_action=exist/create,partner_id khi chọn exist,user_id/team_id nếu được chọn. Không gộp lead tự động.
Khi kết quả awaiting_input/unknown_result: thông báo phần đã làm và phần còn thiếu; không chạy lại thao tác hoặc tạo operation mới. Chỉ tiếp tục bằng token Gateway cấp hoặc hướng dẫn mở Odoo.
Không thay state, số tồn hoặc trường duyệt trực tiếp. Không lập hóa đơn/thanh toán hoặc thay cấu hình hệ thống.
'''
