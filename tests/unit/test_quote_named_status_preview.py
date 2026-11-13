from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.business import quote_status
from app.business.quote_local import interpret
from app.business.workflow import quote_preview


def test_saved_missing_address_error_uses_display_names():
    message = quote_status.quotation_failure_message('Còn thiếu thông tin: partner_invoice_id, partner_shipping_id')
    assert 'yêu cầu nhầm' in message
    assert 'tạo lại' in message
    assert 'partner_' not in message


def test_named_status_is_independent_of_active_draft_and_never_a_write():
    assert interpret('Báo giá S00086 trạng thái là gì?', {}) == {'action': 'status', 'quotation_name': 'S00086'}
    assert interpret('S00086', {}) == {'action': 'status', 'quotation_name': 'S00086'}
    assert interpret('Báo giá S00086 đã gửi khách hàng chưa?', {}) == {'action': 'status', 'quotation_name': 'S00086'}
    assert interpret('Hủy báo giá S00086', {}) is None
    assert interpret('Xác nhận báo giá S00086', {}) is None


def test_preview_renders_empty_values_as_empty_and_zero_as_zero():
    text = quote_preview({'fields': [{'name': 'partner_id', 'label': 'Khách hàng', 'value': 'Công ty Demo'},
        {'name': 'origin', 'label': 'Chứng từ gốc', 'value': None},
        {'name': 'validity_date', 'label': 'Ngày hết hạn', 'value': None}],
        'lines': [{'product': 'Bao tay', 'quantity': 1, 'price_unit': 0, 'tax_ids': []}]}, {'lines': [{}]})
    assert 'Khách hàng: Công ty Demo' in text
    assert 'Chứng từ gốc: \n' in text and 'Ngày hết hạn: \n' in text
    assert 'Đơn giá: 0' in text and 'Thuế: Không thuế' in text
    assert not any(word in text for word in ('chưa nhập', 'chưa chọn', 'mặc định', 'AGENT:', 'price_confirmed'))


@pytest.mark.asyncio
async def test_named_status_reloads_odoo_without_exposing_internal_operation(monkeypatch):
    request = AsyncMock(side_effect=[{'status': 'OK', 'result': {'name': 'S00086', 'state': 'draft'}},
        {'status': 'OK', 'result': {'name': 'S00086', 'state': 'sent'}}])
    monkeypatch.setattr(quote_status, 'request', request)
    context = SimpleNamespace()
    assert 'Báo giá nháp' in await quote_status.status_by_name(context, 'S00086')
    result = await quote_status.status_by_name(context, 'S00086')
    assert 'Báo giá S00086' in result and 'Báo giá đã gửi' in result
    assert 'op_' not in result and 'ID' not in result
    assert request.call_args.args[1:] == ('GET', 'quotations/status', {'name': 'S00086'})
