"""No Odoo writes: verify PDF selection remains bound through the next chat turn."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.business import quote_saved_interaction as saved
from app.business.quote_local import interpret


class Store:
    def __init__(self):
        self.pending = None

    def set_pending_clarification(self, *args, **kwargs):
        self.pending = args[4]

    def clear_pending_clarification(self, *args, **kwargs):
        self.pending = None


@pytest.mark.asyncio
async def test_pdf_button_edit_uses_selected_record_and_line(monkeypatch):
    token = 'a' * 24
    context = SimpleNamespace(tenant='fixture', user_id=1, chat_id=7,
        telegram_binding_id='binding', database='db')
    store = Store()
    observed = []
    async def gateway(_context, _method, path, payload):
        observed.append((path, payload))
        if path == 'quotations/pdf-open':
            return {'status': 'OK', 'order_id': 97, 'name': 'S00097',
                'version': '2026-11-16 10:00:00', 'lines': [
                    {'id': 31, 'product_id': [22, 'Bao tay'], 'product_uom_qty': 2}]}
        return {'status': 'sent', 'order_id': 97, 'name': 'S00097'}
    monkeypatch.setattr(saved, 'request', gateway)
    pipeline = SimpleNamespace(_call_gemini_raw=AsyncMock(side_effect=RuntimeError('offline')))
    opened = await saved.handle('/quote_edit ' + token, context, {}, store, pipeline)
    assert opened == ['Bạn muốn sửa gì trên báo giá S00097?']
    assert store.pending['order_id'] == 97
    result = await saved.handle('Đổi 2 Bao tay thành 3, còn lại giữ nguyên', context,
                                {'pending_clarification': store.pending}, store, pipeline)
    assert result == ['Đã sửa báo giá S00097 trên cùng bản ghi và gửi PDF mới.']
    assert observed[-1] == ('quotations/pdf-edit', {'token': token, 'line_id': 31, 'quantity': 3})
    assert store.pending is None


@pytest.mark.asyncio
async def test_multiple_lines_need_target_and_feedback_is_verbatim(monkeypatch):
    token = 'b' * 24
    context = SimpleNamespace(tenant='fixture', user_id=2, chat_id=8,
        telegram_binding_id='binding', database='db')
    store = Store()
    observed = []
    async def gateway(_context, _method, path, payload):
        observed.append((path, payload))
        return {'status': 'recorded', 'message': 'Đã chuyển góp ý.'}
    monkeypatch.setattr(saved, 'request', gateway)
    pipeline = SimpleNamespace(_call_gemini_raw=AsyncMock(side_effect=RuntimeError('offline')))
    pending = {'kind': 'saved_quote_edit', 'name': 'S00101', 'token': token,
        'lines': [{'id': 1, 'product_id': [11, 'Bao tay']},
                  {'id': 2, 'product_id': [12, 'Khẩu trang']}]}
    reply = await saved.handle('à lấy ba cái thôi', context,
                               {'pending_clarification': pending}, store, pipeline)
    assert 'sản phẩm nào' in reply[0]
    assert not observed
    comment = 'Đổi lại số lượng vì kho chỉ còn 2 hộp; giữ nguyên giá.'
    store.pending = {'kind': 'quote_approval_feedback', 'token': token, 'name': 'S00101'}
    await saved.handle(comment, context, {'pending_clarification': store.pending}, store, pipeline)
    assert observed[-1] == ('quotations/approval-decision', {'token': token, 'feedback': comment})


@pytest.mark.asyncio
async def test_second_feedback_button_cannot_steal_pending_comment(monkeypatch):
    context = SimpleNamespace(tenant='fixture', user_id=2, chat_id=8,
        telegram_binding_id='binding', database='db')
    store = Store()
    store.pending = {'kind': 'quote_approval_feedback', 'token': 'a' * 24,
                     'name': 'S00097'}
    gateway = AsyncMock(side_effect=AssertionError('Must not open a second target'))
    monkeypatch.setattr(saved, 'request', gateway)
    reply = await saved.handle('/quote_feedback ' + 'b' * 24, context,
        {'pending_clarification': store.pending}, store, None)
    assert 'S00097' in reply[0]
    assert store.pending['token'] == 'a' * 24
    gateway.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_account_feedback_blocks_sale_button_until_cancelled(monkeypatch):
    context = SimpleNamespace(tenant='fixture', user_id=1, chat_id=7,
        telegram_binding_id='binding', database='db')
    store = Store()
    store.pending = {'kind': 'quote_approval_feedback', 'token': 'a' * 24,
                     'name': 'S00097'}
    gateway = AsyncMock(side_effect=AssertionError('No Gateway operation expected'))
    monkeypatch.setattr(saved, 'request', gateway)
    reply = await saved.handle('/quote_edit ' + 'b' * 24, context,
        {'pending_clarification': store.pending}, store, None)
    assert 'S00097' in reply[0]
    assert store.pending['kind'] == 'quote_approval_feedback'
    cancelled = await saved.handle('hủy', context,
        {'pending_clarification': store.pending}, store, None)
    assert 'hủy' in cancelled[0] and store.pending is None
    gateway.assert_not_awaited()


def test_named_pdf_retry_is_not_tied_to_latest_draft():
    assert interpret('Gửi lại PDF S00097', {}) == {'action': 'retry_pdf', 'quotation_name': 'S00097'}


@pytest.mark.asyncio
async def test_older_pdf_a_edit_then_newer_pdf_b_approval_keep_separate_targets(monkeypatch):
    context = SimpleNamespace(tenant='fixture', user_id=1, chat_id=7,
        telegram_binding_id='binding', database='db')
    store = Store()
    a, b = 'a' * 24, 'b' * 24
    calls = []

    async def gateway(_context, _method, path, payload):
        calls.append((path, payload.copy()))
        if path == 'quotations/pdf-open':
            if payload['token'] == a:
                return {'status': 'OK', 'order_id': 101, 'name': 'S00101',
                    'version': 'v1', 'lines': [{'id': 31, 'product_id': [1, 'Bao tay'],
                                              'product_uom_qty': 2}]}
            return {'status': 'OK', 'order_id': 102, 'name': 'S00102',
                'version': 'v1', 'lines': [{'id': 32, 'product_id': [2, 'Máy X'],
                                          'product_uom_qty': 4}]}
        if path == 'quotations/pdf-edit':
            return {'status': 'sent', 'order_id': 101, 'name': 'S00101'}
        return {'status': 'sent', 'message': 'Đã gửi duyệt S00102.'}

    monkeypatch.setattr(saved, 'request', gateway)
    pipeline = SimpleNamespace(_call_gemini_raw=AsyncMock(side_effect=RuntimeError('offline')))
    await saved.handle('/quote_edit ' + a, context, {}, store, pipeline)
    assert store.pending['order_id'] == 101
    await saved.handle('Đổi 2 Bao tay thành 3', context,
                       {'pending_clarification': store.pending}, store, pipeline)
    assert ('quotations/pdf-edit', {'token': a, 'line_id': 31, 'quantity': 3}) in calls
    assert store.pending is None
    await saved.handle('/quote_approve ' + b, context, {}, store, pipeline)
    assert calls[-1] == ('quotations/approval-submit', {'token': b})
    assert not any(path == 'quotations/pdf-edit' and payload['token'] == b
                   for path, payload in calls)


@pytest.mark.asyncio
async def test_payment_term_edit_selects_catalog_row_on_pdf_a_only(monkeypatch):
    context = SimpleNamespace(tenant='fixture', user_id=1, chat_id=7,
        telegram_binding_id='binding', database='db')
    store = Store()
    token_a, token_b = 'a' * 24, 'b' * 24
    calls = []
    async def gateway(_context, method, path, payload):
        calls.append((path, payload.copy()))
        if path == 'quotations/pdf-open':
            return {'status': 'OK', 'order_id': 101, 'name': 'S00101',
                    'version': 'v1', 'lines': []}
        if path == 'quotations/lookup':
            return {'items': [
                {'id': 1, 'name': 'Thanh toán ngay', 'api_name': 'Immediate Payment'},
                {'id': 8, 'name': '30% trả ngay, còn lại trả trong 60 ngày'}],
                'exact_match_unique': False}
        return {'status': 'sent', 'order_id': 101, 'name': 'S00101'}
    monkeypatch.setattr(saved, 'request', gateway)
    pipeline = SimpleNamespace(_call_gemini_raw=AsyncMock(side_effect=RuntimeError('offline')))
    await saved.handle('/quote_edit ' + token_a, context, {}, store, pipeline)
    reply = await saved.handle('đổi điều khoản thành trả ngay', context,
        {'pending_clarification': store.pending}, store, pipeline)
    assert 'Thanh toán ngay' in reply[0] and '30%' in reply[0]
    await saved.handle('1', context, {'pending_clarification': store.pending}, store, pipeline)
    assert calls[-1] == ('quotations/pdf-edit', {'token': token_a, 'payment_term_id': 1})
    assert not any(path == 'quotations/pdf-edit' and payload['token'] == token_b for path, payload in calls)
    assert not any('approval' in path for path, _ in calls)


@pytest.mark.asyncio
async def test_optional_edit_on_selected_pdf_a_preserves_false_and_no_approval(monkeypatch):
    from app.business import quote_optional
    context = SimpleNamespace(tenant='fixture', user_id=1, chat_id=7,
        telegram_binding_id='binding', database='db')
    store = Store()
    token_a, token_b = 'a' * 24, 'b' * 24
    observed = []
    async def gateway(_context, _method, path, payload):
        observed.append((path, payload.copy()))
        if path == 'quotations/pdf-open':
            return {'status': 'OK', 'order_id': 101, 'name': 'S00101',
                'version': 'v1', 'company_id': 3, 'lines': []}
        if path == 'quotations/optional-metadata':
            return {'available': True, 'type': 'boolean', 'options': []}
        return {'status': 'sent', 'order_id': 101, 'name': 'S00101'}
    monkeypatch.setattr(saved, 'request', gateway)
    monkeypatch.setattr(quote_optional, 'request', gateway)
    pipeline = SimpleNamespace(_call_gemini_raw=AsyncMock(side_effect=RuntimeError('offline')))
    await saved.handle('/quote_edit ' + token_a, context, {}, store, pipeline)
    reply = await saved.handle('Đổi chữ ký online thành Không', context,
        {'pending_clarification': store.pending}, store, pipeline)
    assert 'S00101' in reply[0]
    assert ('quotations/pdf-edit', {'token': token_a,
        'optional_fields': {'require_signature': False}}) in observed
    assert not any(path == 'quotations/pdf-edit' and payload['token'] == token_b for path, payload in observed)
    assert not any('approval' in path for path, _ in observed)
