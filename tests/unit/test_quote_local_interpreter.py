import time

import pytest

from app.business.quote_local import interpret


@pytest.mark.parametrize(('text', 'expected'), [
    ('Tạo báo giá mới', {'action': 'start'}),
    ('Khách là Công ty Demo', {'action': 'update', 'customer_query': 'Công ty Demo'}),
    ('sản phẩm là bao tay', {'action': 'update', 'product_query': 'bao tay'}),
    ('Số lượng là một', {'action': 'update', 'quantity': 1.0}),
    ('sử dụng giá bán đang niêm yết đi, không thuế', {'action': 'update', 'use_catalog_price': True, 'tax_ids': []}),
    ('Không thêm dòng nào nữa', {'action': 'finish_lines'}),
    ('Đơn giá 200000 đồng', {'action': 'update', 'price_unit': 200000.0, 'price_confirmed': True}),
    ('nhập là 10 ngày', {'action': 'update', 'customer_lead': 10.0}),
    ('Báo giá kiểm tra xong chưa?', {'action': 'status'}),
    ('không cần xem trước, tạo luôn đi', {'action': 'preview'}),
    ('Tôi xác nhận tạo báo giá theo bản vừa xem', {'action': 'confirm'}),
    ('hết hạn ngày 3/12/2026', {'action': 'update', 'validity_date': '2026-12-03'}),
])
def test_clear_natural_facts_without_model(text, expected):
    assert interpret(text, {'draft_type': 'quotation'}) == expected


def test_no_guesses_or_accidental_starts():
    for text in ['cách tạo báo giá', 'đừng tạo báo giá', 'không', 'bao tay hay khẩu trang', 'giá khoảng 200 nghìn', 'số lượng 1 và 2', 'hết hạn 31/2/2026']:
        assert interpret(text, {'draft_type': 'quotation'}) is None
    assert interpret('1', {}) is None


def test_selection_scoped_and_quantity_unambiguous():
    draft = {'draft_type': 'quotation', 'quotation_hints': {'selection': {'ids': [11, 12], 'at': time.time()}}}
    assert interpret('Chọn khách hàng ID 11', draft) == {'action': 'update', 'selected_id': 11}
    assert interpret('11', draft) == {'action': 'update', 'selected_id': 11}
    assert interpret('99', draft) is None
    draft['quotation_hints']['selection']['at'] = time.time() - 1900
    assert interpret('11', draft) is None
    assert interpret('1', {'draft_type': 'quotation', 'quotation_data': {'partner_id': 11, 'lines': [{'product_id': 22}]}}) == {'action': 'update', 'quantity': 1.0}


def test_names_resolve_only_from_current_candidates_and_actual_question():
    draft = {'draft_type': 'quotation', 'quotation_hints': {'selection': {'kind': 'partners', 'ids': [11], 'at': time.time()}}}
    session = {'last_read': {'business_candidates_partners': {'data': {'items': [{'id': 11, 'display_name': 'Công ty Demo'}]}}}}
    assert interpret('chọn Công ty Demo', draft, session) == {'action': 'update', 'selected_id': 11}
    session['last_read']['business_candidates_partners']['data']['items'].append({'id': 11, 'display_name': 'Công ty Demo'})
    assert interpret('chọn Công ty Demo', draft, session) is None
    question = {'history': [{'role': 'assistant', 'content': 'Cần bổ sung: tên khách hàng; tên sản phẩm và số lượng.'}]}
    assert interpret('nguyễn văn a', {'draft_type': 'quotation'}, question) == {'action': 'update', 'customer_query': 'nguyễn văn a'}
    assert interpret('nguyễn văn a', {'draft_type': 'quotation'}, {}) is None
    assert interpret('không', {'draft_type': 'quotation'}, question) is None


def test_explicit_multiple_facts_preserve_all_values_and_reject_conflicts():
    draft = {'draft_type': 'quotation'}
    assert interpret('Đơn giá 1, xác nhận không thuế', draft) == {'action': 'update', 'price_unit': 1.0, 'price_confirmed': True, 'tax_ids': []}
    assert interpret('sản phẩm là Bao tay, số lượng 2', draft) == {'action': 'update', 'product_query': 'Bao tay', 'quantity': 2.0}
    assert interpret('số lượng 1, số lượng 2', draft) is None
    assert interpret('không thuế, hủy báo giá', draft) is None
