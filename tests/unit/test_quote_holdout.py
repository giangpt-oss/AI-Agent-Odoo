"""Held-out Vietnamese utterances: never copy these examples into QUOTE_PROMPT."""
import pytest
from datetime import date

from app.business.quote_fallback import extract, non_creation_intent
from app.business.quote_local import requests_new_quote


@pytest.mark.parametrize('text,customer,items,price', [
    ('Nhờ lên báo giá cho Công ty Mộc An, khăn y tế lấy 4 hộp, thanh toán trong 15 ngày, hết hạn ngày 20/12/2026',
     'Công ty Mộc An', [('khăn y tế', 4)], None),
    ('Bảng giá mặc định, soạn báo giá cho Bệnh viện Nam Sơn, 5 găng tay, hết hạn 22/12/2026, thanh toán 21 ngày',
     'Bệnh viện Nam Sơn', [('găng tay', 5)], 'mặc định'),
    ('Cho Phòng khám Minh Tâm lập báo giá; hai bộ áo choàng và 7 hộp khẩu trang; thanh toán 30 ngày; hết hạn ngày 28/12/2026',
     'Phòng khám Minh Tâm', [('áo choàng', 2), ('khẩu trang', 7)], None),
])
def test_unseen_quote_phrasings(text, customer, items, price):
    result = extract(text)
    assert result['action'] == 'start'
    assert result['customer_query'] == customer
    assert [(line['product_query'], line['quantity']) for line in result['line_updates']] == items
    assert result.get('pricelist_query') == price
    assert result.get('payment_term_query')
    assert result.get('validity_date')


def test_unseen_negation_does_not_start_quote():
    text = 'Tôi không muốn soạn báo giá'
    assert not requests_new_quote(text)
    assert extract(text) is None


def test_bare_duration_uses_only_one_pending_field():
    draft = {'draft_type': 'quotation', 'quotation_data': {'lines': []}}
    def session(question):
        return {'history': [{'role': 'assistant', 'content': question}]}
    assert extract('30 ngày nhé', draft, session('Điều khoản thanh toán nào?'),
                   today=date(2026, 11, 16))['payment_term_query'] == '30 ngày'
    assert extract('30 ngày nhé', draft, session('Báo giá hết hạn sau bao nhiêu ngày?'),
                   today=date(2026, 11, 16))['validity_date'] == '2026-12-16'
    assert extract('30 ngày nhé', draft, session('Còn thiếu ngày hết hạn và điều khoản thanh toán.'),
                   today=date(2026, 11, 16)) is None


def test_fallback_change_of_mind_uses_last_positive_quantity():
    draft = {'draft_type': 'quotation', 'quotation_data': {'lines': [{'quantity': 3}]}}
    assert extract('À không, chuyển sang 2 cái', draft)['quantity'] == 2
    assert extract('không lấy 3 cái, lấy 2 cái', draft)['quantity'] == 2
    assert extract('không lấy 3 cái', draft) is None


def test_labelled_customer_keeps_every_character_for_gateway_lookup():
    draft = {'draft_type': 'quotation', 'quotation_data': {'lines': []}}
    result = extract('Tên khách hàng: Bệnh viện đa khoa TPHCM', draft)
    assert result['customer_query'] == 'Bệnh viện đa khoa TPHCM'


def test_new_customer_answer_survives_missing_lookup_and_other_pending_field():
    draft = {'draft_type': 'quotation', 'quotation_data': {'lines': []}}
    for question in ('Hãy cung cấp tên hoặc mã khách chính xác.',
                     'Bạn muốn dùng bảng giá Mặc định, Bệnh viện hay Thương mại?'):
        result = extract('An Nhiên Spa - Đông Y', draft,
                         {'history': [{'role': 'assistant', 'content': question}]})
        assert result == {'action': 'update', 'customer_query': 'An Nhiên Spa - Đông Y'}


def test_quantity_word_uses_one_line_and_leaves_target_to_context():
    for count in (1, 2):
        draft = {'draft_type': 'quotation', 'quotation_data': {'lines': [{}] * count}}
        assert extract('à lấy ba cái thôi', draft)['quantity'] == 3


def test_search_help_and_negation_are_not_creation():
    assert non_creation_intent('Tìm báo giá của An Nhiên') == ('lookup', 'An Nhiên')
    assert non_creation_intent('Cách tạo báo giá thế nào?')[0] == 'help'
    assert non_creation_intent('Đừng tạo báo giá')[0] == 'decline'


def test_unaccented_product_name_is_sent_whole_to_lookup():
    result = extract('Len bao gia cho Cong ty Song Xanh, 2 may X, het han ngay 20/12/2026, thanh toan 30 ngay')
    assert result['customer_query'] == 'Cong ty Song Xanh'
    assert result['line_updates'] == [{'line_index': 1, 'product_query': 'may X', 'quantity': 2}]
    assert result['validity_date'] == '2026-12-20'
