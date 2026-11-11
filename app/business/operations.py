"""Local business drafts and Gateway-issued preparations; never arbitrary RPC."""
import copy
import json
import re
import time

from app.business.client import request
from app.business.workflow import remember, session
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.services.confirmation_service import confirmation_service
from app.services.conversation_history_store import conversation_history_store as store

OPS = {
    'contacts': 'create update archive restore delete', 'products': 'create update archive restore',
    'crm': 'create update assign stage won lost reopen archive restore delete convert',
    'sales': 'create update copy confirm cancel draft lock unlock delete send export request_approval grant_approval reject_approval',
    'purchases': 'create update copy confirm approve reject cancel draft delete send',
    'stock': 'create update confirm reserve unreserve validate cancel return inventory backorder lot_create',
    'activities': 'create update done', 'notes': 'create',
}
ACTIONS = [f'business.{module}.{op}' for module, operations in OPS.items() for op in operations.split()]
QUOTE_FLOW_ACTIONS = {'business.sales.request_approval', 'business.sales.grant_approval',
                      'business.sales.reject_approval', 'business.sales.send', 'business.sales.confirm'}


def allowed_action(settings, action=None):
    return settings.ENABLE_BUSINESS_ACTIONS or (
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


def interpret_quotation_flow(text):
    lower = text.lower().strip()
    names = re.findall(r'\bS\d{3,}\b', text, re.I)
    if len(names) != 1:
        return None
    action = None
    if re.search(r'(?:xin|yêu cầu|trình|gửi.*cấp trên).*duyệt|request approval', lower):
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
        ('business.quotation_flow', 'read', 'Xin duyệt/duyệt/từ chối/gửi báo giá theo số Odoo. Chỉ chuẩn bị bản xem trước, mọi thực thi phải xác nhận riêng. Không gọi để tạo báo giá mới.', {
            'quotation_name': {'type': 'string'},
            'action': {'enum': ['request_approval', 'grant_approval', 'reject_approval', 'send', 'confirm']},
            'options': {'type': 'object'}, 'company_id': {'type': 'integer'}}),
        ('business.capabilities', 'read', 'Xem thao tác Odoo được hỗ trợ và giới hạn quyền hiện tại', {}),
        ('business.lookup', 'read', 'Tra cứu bản ghi thật cho thao tác trong năm phân hệ; không đoán ID', {
            'kind': {'enum': ['contacts', 'products', 'sales', 'purchases', 'crm', 'stock', 'quants', 'activities', 'warehouses', 'locations', 'picking_types', 'lots', 'users', 'stages', 'teams', 'lost_reasons', 'taxes', 'uoms', 'currencies', 'pricelists', 'payment_terms', 'activity_types', 'countries', 'states', 'categories', 'models', 'attachments', 'move_lines', 'moves', 'product_templates', 'variant_values']},
            'query': {'type': 'string'}, 'company_id': {'type': 'integer'}}),
        ('business.draft', 'read', 'Thu thập/sửa nháp nghiệp vụ cục bộ. Không thay đổi Odoo; action=start/update/cancel, business_action là thao tác, data là payload.', {
            'action': {'enum': ['start', 'update', 'cancel']}, 'business_action': {'enum': available_actions}, 'data': {'type': 'object'}}),
        ('business.preview', 'write', 'Kiểm tra nháp trên Gateway và tạo bản xem trước có mã xác nhận; chưa ghi Odoo.', {}),
    ]
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
            if isinstance(value, list) and len(value) == 2:
                value = value[1]
            output.append(f'{label}: {value}')
    if result.get('download_url'):
        output.append('Tải PDF: ' + result['download_url'])
    if result.get('delivery_status'):
        output.append('Giao email: ' + result['delivery_status'])
    if data.get('steps'):
        output.append('Các bước đã ghi nhận: ' + ', '.join(item['stage'] for item in data['steps']))
    return ['\n'.join(output)]


async def read(tool, params, context):
    from app.core.config import get_settings
    if not allowed_action(get_settings()):
        return ['Thao tác nghiệp vụ chưa được bật để nghiệm thu.']
    if tool == 'business.quotation_flow':
        name = str(params.get('quotation_name') or '').upper().strip()
        action = 'business.sales.' + str(params.get('action') or '')
        if action not in QUOTE_FLOW_ACTIONS or not name or len(name) > 100 or not allowed_action(get_settings(), action):
            return ['Bạn muốn xử lý bước nào và báo giá có số nào trên Odoo?']
        lookup = {'kind': 'sales', 'query': name}
        if params.get('company_id'):
            lookup['company_id'] = params['company_id']
        found = await request(context, 'POST', 'business/lookup', lookup)
        matches = [item for item in found.get('items', []) if str(item.get('name') or '').upper() == name]
        if len(matches) != 1:
            return [found.get('message') or 'Chưa xác định duy nhất báo giá trong công ty tài khoản hiện tại. Hãy cung cấp số báo giá và công ty chính xác.']
        row = matches[0]
        payload = {'target_id': row['id'], 'options': params.get('options') or {}}
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
    if changed or ('company_id' in data and data['company_id'] != payload.get('company_id')):
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
    if business_action in QUOTE_FLOW_ACTIONS:
        return await prepare(context)
    return [f"Đã lưu thông tin, phiên bản {saved['version']}. Chưa thay đổi Odoo. Bạn có thể yêu cầu xem trước để kiểm tra trước khi thực hiện."]


def invalidate(context, draft):
    if draft.get('draft_id'):
        confirmation_service.cancel_pending_draft_confirmations(user_id=context.user_id, chat_id=context.chat_id,
            telegram_binding_id=context.telegram_binding_id or 'default', draft_id=draft['draft_id'])


def preview_text(preview):
    lines = [str(preview.get('title', 'Xem trước thao tác Odoo')),
             ('Công ty: ' + str(preview['company_name'])) if preview.get('company_name') else 'Công ty ID: ' + str(preview.get('company_id'))]
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
            lines.append(f"{change.get('label') or change.get('field')}: {change.get('before')} → {change.get('after')}")
    elif changes:
        lines.append('Thay đổi: ' + json.dumps(changes, ensure_ascii=False, default=str))
    for key, label in {'affected_variant_ids': 'Biến thể cùng bị ảnh hưởng', 'lines': 'Dòng hàng', 'recipients': 'Người nhận', 'subject': 'Tiêu đề', 'body': 'Nội dung', 'attachments': 'File đính kèm', 'execution_lines': 'Số lượng thực hiện', 'backorder': 'Phần còn lại', 'return_lines': 'Dòng trả hàng', 'destination': 'Vị trí đích', 'before_quantity': 'Tồn trước', 'counted_quantity': 'Số kiểm kê', 'difference': 'Chênh lệch', 'conversion': 'Chuyển đổi CRM', 'lost_reason': 'Lý do thua', 'feedback': 'Kết quả hoạt động', 'notice': 'Lưu ý'}.items():
        if key in preview:
            value = preview[key]
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
        requirements = response.get('requirements', [])
        lines = ['Cần bổ sung thông tin:']
        for item in requirements:
            label = item.get('label') or item['field']
            if item.get('choices'):
                label += ': ' + ', '.join(f'{caption} ({value})' for value, caption in item['choices'])
            lines.append(label + ' [' + item['field'] + ']')
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
