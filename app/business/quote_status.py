"""Human-readable quotation lifecycle; status reads never dispatch another create."""
import json
from datetime import UTC, datetime

from app.business.client import request


def result_message(result):
    labels = {'draft': 'Báo giá nháp', 'sent': 'Báo giá đã gửi tới khách hàng (trạng thái Odoo)', 'sale': 'Đơn bán đã xác nhận', 'cancel': 'Đã hủy', 'done': 'Đã khóa/hoàn tất'}
    state = result.get('state')
    text = f"Báo giá {result.get('name') or '(chưa có số Odoo)'}\nTrạng thái: {labels.get(state, state or 'Chưa xác định')}.\nĐã tạo trên Odoo."
    if result.get('amount_total') is not None:
        currency = result.get('currency_id') or []
        unit = currency[1] if isinstance(currency, list) and len(currency) > 1 else ''
        text += f"\nTổng tiền: {result['amount_total']} {unit}."
    if result.get('verification') == 'check_required':
        text += '\nCần kiểm tra chênh lệch với bản xem trước trước khi sử dụng.'
    approval = result.get('approval')
    if approval:
        labels = {'not_requested': 'Chưa yêu cầu duyệt', 'pending': 'Chờ cấp trên duyệt',
                  'approved': 'Đã duyệt nội bộ', 'rejected': 'Bị từ chối duyệt',
                  'review_required': 'Cần kiểm tra và xin duyệt lại nội dung',
                  'configuration_required': 'Chưa có quy tắc duyệt phù hợp', 'unknown': 'Chưa đọc được trạng thái duyệt'}
        text += '\nDuyệt nội bộ: ' + labels.get(approval.get('status'), 'Chưa xác minh') + '.'
        if approval.get('approvers'):
            text += '\nNgười duyệt: ' + ', '.join(approval['approvers']) + '.'
        if approval.get('agent_review_required'):
            text += '\nCần kiểm tra phiên bản xin duyệt trước khi gửi qua Agent.'
        if state in ('draft', 'sent'):
            text += '\nChỉ chuyển thành đơn bán sau khi khách chấp thuận.'
    if result.get('url'):
        text += '\n' + result['url']
    return text


async def status_by_name(context, name, company_id=None):
    parameters = {'name': name}
    if company_id:
        parameters['company_id'] = company_id
    remote = await request(context, 'GET', 'quotations/status', parameters)
    if remote.get('status') in ('OK', 'completed') and remote.get('result'):
        return result_message(remote['result'])
    return f"Báo giá {name}\n" + str(remote.get('message') or 'Chưa đọc được trạng thái hiện tại trên Odoo. Không tạo lại báo giá.')


async def refresh_confirmation(context, confirmations, conf):
    if conf and conf['status'] in ('executing', 'unknown_result'):
        remote = await request(context, 'GET', 'actions/' + conf['operation_id'])
        if remote.get('status') in ('completed', 'failed'):
            confirmations.reconcile_confirmation_result(conf['confirmation_id'], status=remote['status'],
                result=remote.get('result'), error_message=remote.get('message'), operation_id=conf['operation_id'])
            return confirmations.get_confirmation(conf['confirmation_id']), remote.get('status')
        return conf, remote.get('status')
    return conf, None


def legacy_address_error(error):
    lines = [line.strip() for line in (error or '').splitlines() if line.strip()]
    prefix = 'Còn thiếu thông tin:'
    if not lines or not lines[-1].startswith(prefix):
        return False
    fields = {field.strip() for field in lines[-1][len(prefix):].split(',') if field.strip()}
    return bool(fields) and fields <= {'partner_invoice_id', 'partner_shipping_id'}


def quotation_failure_message(error, details=None):
    from app.business.workflow import missing_prompt

    details = details or {}
    lines = [line.strip() for line in (error or '').splitlines() if line.strip()]
    summary = lines[-1][:700] if lines else 'Odoo từ chối thao tác.'
    legacy_prefix = 'Còn thiếu thông tin:'
    if legacy_address_error(error):
        return 'Lần tạo trước bị Agent yêu cầu nhầm thông tin địa chỉ. Hãy yêu cầu tạo lại báo giá từ nháp đã lưu.'
    if not details.get('missing_fields') and summary.startswith(legacy_prefix):
        details = {'missing_fields': [path.strip() for path in summary[len(legacy_prefix):].split(',') if path.strip()]}
    if details.get('missing_fields'):
        return missing_prompt(details)
    return f'Trạng thái: Lỗi tạo báo giá.\nLý do: {summary}\nNháp được giữ.'


async def status_for(context, draft, confirmations, refresh=True):
    conf = confirmations.latest_draft_confirmation(user_id=context.user_id, chat_id=context.chat_id,
        telegram_binding_id=context.telegram_binding_id or 'default', draft_id=draft['draft_id'])
    if not conf or not confirmations.validate_access(conf, context.user_id, context.chat_id, context.telegram_binding_id, context.owner_key):
        return None
    if refresh:
        conf, remote_status = await refresh_confirmation(context, confirmations, conf)
        if remote_status == 'in_progress':
            return 'Trạng thái: Đang tạo báo giá trên Odoo. Tôi sẽ gửi kết quả khi hoàn tất.'
    status = conf['status']
    if status == 'completed':
        result = conf.get('result_json') or '{}'
        result = json.loads(result) if isinstance(result, str) else result
        if refresh and result.get('name'):
            return await status_by_name(context, result['name'], (result.get('company_id') or [None])[0])
        return result_message(result)
    if status == 'unknown_result' or status == 'executing':
        return 'Trạng thái: Chưa rõ kết quả tạo báo giá, chưa nhận được số báo giá từ Odoo. Không tạo lại để tránh trùng. Cần kiểm tra Odoo trước khi thực hiện tiếp.'
    if conf.get('draft_version') != draft.get('version'):
        return None
    if status == 'pending':
        expiry = datetime.fromisoformat(conf['expires_at'])
        if expiry <= datetime.now(UTC):
            return 'Trạng thái: Xác nhận đã hết hạn. Nháp được giữ. Bạn có thể yêu cầu xem trước lại để nhận xác nhận mới.'
        return 'Trạng thái: Chờ xác nhận tạo báo giá. Chọn Xác nhận hoặc Từ chối trên bản xem trước.'
    if status == 'failed':
        return quotation_failure_message(conf.get('error_message'), json.loads(conf.get('result_json') or '{}'))
    return 'Trạng thái: Đã hủy yêu cầu tạo báo giá. Nháp được giữ; có thể sửa hoặc xem trước lại.'
