"""The real Telegram turn endpoint must route quotation creation to the collector."""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.v1 import internal
from app.business import quote_collector, workflow
from app.core.config import Settings
from app.main import create_application
from app.services import natural_pipeline
from app.services.conversation_history_store import ConversationHistoryStore
from app.services.turn_ledger import TurnLedger


def test_tao_giup_minh_goes_through_quote_collector(tmp_path, monkeypatch):
    settings = Settings(INTEGRATION_MODE='gateway', ENABLE_QUOTATIONS=True,
        GATEWAY_TO_AGENT_SECRET='test_gateway_secret_for_quote_turn_12345')
    store = ConversationHistoryStore(str(tmp_path / 'conversation.db'))
    monkeypatch.setattr(internal, 'conversation_history_store', store)
    monkeypatch.setattr(workflow, 'store', store)
    monkeypatch.setattr(quote_collector, 'store', store)
    monkeypatch.setattr(internal, 'turn_ledger', TurnLedger(tmp_path / 'turns.db'))
    monkeypatch.setattr(internal, 'get_settings', lambda: settings)
    monkeypatch.setattr(natural_pipeline, 'get_settings', lambda: settings)
    monkeypatch.setattr(natural_pipeline.NaturalPipeline, '_call_gemini_raw',
                        AsyncMock(side_effect=RuntimeError('model unavailable in test')))
    async def identity(telegram_user_id, chat_id):
        return {'status': 'OK', 'contract_version': 'v2', 'user_id': 42, 'name': 'Sale',
                'tenant': 'https://odoo.invalid', 'database': 'fixture', 'odoo_uid': 5,
                'telegram_user_id': telegram_user_id, 'chat_id': chat_id,
                'telegram_binding_id': 'fixture', 'permissions': ['sales.create']}
    monkeypatch.setattr(internal.gateway_client, 'resolve_identity', identity)
    captured = []
    async def collect(params, context):
        captured.append(params)
        return ['Đã vào luồng báo giá.']
    monkeypatch.setattr(quote_collector, 'collect', collect)
    client = TestClient(create_application(settings=settings))
    for index, message in enumerate(('Tạo một báo giá', 'Tạo giúp mình một báo giá'), 1):
        response = client.post('/internal/v1/telegram/turn', headers={
            'Authorization': 'Bearer test_gateway_secret_for_quote_turn_12345'}, json={
            'request_id': f'quote-route-{index}', 'gateway_user_id': 42,
            'telegram_user_id': 77, 'chat_id': 77, 'telegram_binding_id': 'fixture',
            'text': message})
        assert response.status_code == 200
        assert response.json()['messages'] == ['Đã vào luồng báo giá.']
    assert [item['action'] for item in captured] == ['start', 'start']


def test_customer_retry_and_combined_facts_through_telegram_turn(tmp_path, monkeypatch):
    settings = Settings(INTEGRATION_MODE='gateway', ENABLE_QUOTATIONS=True,
        ENABLE_REPORTING=False, GATEWAY_TO_AGENT_SECRET='test_gateway_secret_for_quote_turn_12345')
    store = ConversationHistoryStore(str(tmp_path / 'conversation.db'))
    monkeypatch.setattr(internal, 'conversation_history_store', store)
    monkeypatch.setattr(workflow, 'store', store)
    monkeypatch.setattr(quote_collector, 'store', store)
    monkeypatch.setattr(internal, 'turn_ledger', TurnLedger(tmp_path / 'turns.db'))
    monkeypatch.setattr(internal, 'get_settings', lambda: settings)
    monkeypatch.setattr(natural_pipeline, 'get_settings', lambda: settings)
    monkeypatch.setattr('app.core.config.get_settings', lambda: settings)
    monkeypatch.setattr(natural_pipeline.NaturalPipeline, '_call_gemini_raw',
                        AsyncMock(side_effect=RuntimeError('model unavailable in test')))
    async def identity(telegram_user_id, chat_id):
        return {'status': 'OK', 'contract_version': 'v2', 'user_id': 42, 'name': 'Sale',
                'tenant': 'https://odoo.invalid', 'database': 'fixture', 'odoo_uid': 5,
                'telegram_user_id': telegram_user_id, 'chat_id': chat_id,
                'telegram_binding_id': 'fixture', 'permissions': ['sales.create']}
    monkeypatch.setattr(internal.gateway_client, 'resolve_identity', identity)
    observed = []
    async def lookup(_context, _method, path, params):
        assert path == 'quotations/lookup'
        observed.append((params['kind'], params['query']))
        if params['kind'] == 'partners' and params['query'] == 'Bệnh viện đa khoa TPHCM':
            return {'items': []}
        if params['kind'] == 'pricelists' and params['query'] == 'Giá cho KH bệnh viện (VND)':
            return {'items': []}
        if params['kind'] == 'pricelists' and params['query'].casefold() in ('bệnh viện', 'kh bệnh viện'):
            return {'items': [{'id': 3, 'name': 'KH bệnh viện',
                               'display_name': 'KH bệnh viện (VND)'}], 'exact_match_unique': True}
        rows = {
            'partners': {'id': 11, 'display_name': 'An Nhiên Spa - Đông Y'},
            'products': {'id': 22, 'display_name': 'Bao tay'},
            'payment_terms': {'id': 19, 'display_name': '30 ngày'},
            'pricelists': {'id': 33, 'display_name': 'Thương mại'},
        }
        return {'items': [rows[params['kind']]], 'exact_match_unique': True}
    monkeypatch.setattr(quote_collector, 'request', lookup)
    client = TestClient(create_application(settings=settings))
    def send(index, message, chat=77):
        response = client.post('/internal/v1/telegram/turn', headers={
            'Authorization': 'Bearer test_gateway_secret_for_quote_turn_12345'}, json={
            'request_id': f'quote-context-{index}', 'gateway_user_id': 42,
            'telegram_user_id': 77, 'chat_id': chat, 'telegram_binding_id': 'fixture',
            'text': message})
        assert response.status_code == 200
        return response.json()['messages']
    send(1, 'Tạo giúp mình một báo giá')
    missing = send(2, 'Tên khách hàng: Bệnh viện đa khoa TPHCM')
    assert ('partners', 'Bệnh viện đa khoa TPHCM') in observed
    assert 'Không tìm thấy' in missing[0]
    send(3, 'An Nhiên Spa - Đông Y')
    session = store.get_session('https://odoo.invalid', 42, 77, 'fixture', database='fixture')
    assert session['active_draft']['quotation_data']['partner_id'] == 11
    assert session['active_draft']['quotation_hints']['customer_query'] == 'An Nhiên Spa - Đông Y'

    prompt = 'Lên giúp mình báo giá cho An Nhiên Spa - Đông Y, 2 Bao tay, hết hạn ngày 16/12/2026, thanh toán 30 ngày nhé'
    response = send(4, prompt, chat=88)
    assert len(response) == 1 and 'Bạn muốn dùng bảng giá nào trong Odoo?' in response[0]
    assert '1. Thương mại' in response[0]
    before = store.get_session('https://odoo.invalid', 42, 88, 'fixture', database='fixture')['active_draft']
    assert before['quotation_data']['partner_id'] == 11
    assert before['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]
    assert before['quotation_data']['validity_date'] == '2026-12-16'
    assert before['quotation_data']['payment_term_id'] == 19
    menu = send(5, 'thương mại nhé', chat=88)
    assert 'Đã đủ thông tin bắt buộc' in str(menu)
    after = store.get_session('https://odoo.invalid', 42, 88, 'fixture', database='fixture')['active_draft']
    assert after['draft_id'] == before['draft_id']
    assert after['quotation_data']['pricelist_id'] == 33
    assert after['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]
    send(6, 'à lấy ba cái thôi', chat=88)
    edited = store.get_session('https://odoo.invalid', 42, 88, 'fixture', database='fixture')['active_draft']
    assert edited['draft_id'] == before['draft_id']
    assert edited['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 3}]
    assert edited['quotation_data']['partner_id'] == 11
    assert edited['quotation_data']['validity_date'] == '2026-12-16'
    preview = send(7, '0', chat=88)
    assert 'Chờ xác nhận tạo báo giá' in str(preview)
    assert 'optional_fields' not in store.get_session('https://odoo.invalid', 42, 88, 'fixture', database='fixture')['active_draft']['quotation_data']
    send(8, prompt, chat=99)
    price = send(9, 'bệnh viện', chat=99)
    assert 'Đã đủ thông tin bắt buộc' in str(price)
    hospital = store.get_session('https://odoo.invalid', 42, 99, 'fixture', database='fixture')['active_draft']
    assert hospital['quotation_data']['pricelist_id'] == 3
    assert hospital['quotation_hints']['pricelist_label'] == 'KH bệnh viện (VND)'
    assert hospital['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]
    send(10, 'đổi bảng giá sang Giá cho KH bệnh viện (VND)', chat=99)
    corrected = store.get_session('https://odoo.invalid', 42, 99, 'fixture', database='fixture')['active_draft']
    assert corrected['quotation_data']['pricelist_id'] == 3
    assert corrected['quotation_data']['partner_id'] == 11
    assert corrected['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]


def test_pricelist_answer_never_becomes_product_in_new_or_old_telegram_draft(tmp_path, monkeypatch):
    settings = Settings(INTEGRATION_MODE='gateway', ENABLE_QUOTATIONS=True,
        GATEWAY_TO_AGENT_SECRET='test_gateway_secret_for_quote_turn_12345')
    store = ConversationHistoryStore(str(tmp_path / 'conversation.db'))
    monkeypatch.setattr(internal, 'conversation_history_store', store)
    monkeypatch.setattr(workflow, 'store', store)
    monkeypatch.setattr(quote_collector, 'store', store)
    monkeypatch.setattr(internal, 'turn_ledger', TurnLedger(tmp_path / 'turns.db'))
    monkeypatch.setattr(internal, 'get_settings', lambda: settings)
    monkeypatch.setattr(natural_pipeline, 'get_settings', lambda: settings)
    monkeypatch.setattr('app.core.config.get_settings', lambda: settings)
    monkeypatch.setattr(natural_pipeline.NaturalPipeline, '_call_gemini_raw',
                        AsyncMock(side_effect=RuntimeError('model unavailable in test')))
    async def identity(telegram_user_id, chat_id):
        return {'status': 'OK', 'contract_version': 'v2', 'user_id': 42, 'name': 'Sale',
                'tenant': 'https://odoo.invalid', 'database': 'fixture', 'odoo_uid': 5,
                'telegram_user_id': telegram_user_id, 'chat_id': chat_id,
                'telegram_binding_id': 'fixture', 'permissions': ['sales.create']}
    monkeypatch.setattr(internal.gateway_client, 'resolve_identity', identity)
    calls = []
    async def lookup(_context, method, path, params):
        assert method == 'GET' and path == 'quotations/lookup'
        calls.append((params['kind'], params['query']))
        if params['kind'] == 'partners':
            return {'items': [{'id': 15, 'display_name': 'Bệnh viện đa khoa TPHCM'}],
                    'exact_match_unique': True}
        if params['kind'] == 'products':
            assert params['query'] == 'Bao tay'
            return {'items': [{'id': 22, 'display_name': 'Bao tay'}], 'exact_match_unique': True}
        if params['query'] == '':
            return {'items': [{'id': 3, 'name': 'KH bệnh viện',
                               'display_name': 'KH bệnh viện (VND)'}]}
        return {'items': [{'id': 3, 'name': 'KH bệnh viện',
                           'display_name': 'KH bệnh viện (VND)'}], 'unique_query_match': True}
    monkeypatch.setattr(quote_collector, 'request', lookup)
    client = TestClient(create_application(settings=settings))
    def send(chat, number, message):
        response = client.post('/internal/v1/telegram/turn', headers={
            'Authorization': 'Bearer test_gateway_secret_for_quote_turn_12345'}, json={
            'request_id': f'price-no-product-{chat}-{number}', 'gateway_user_id': 42,
            'telegram_user_id': 77, 'chat_id': chat, 'telegram_binding_id': 'fixture',
            'text': message})
        assert response.status_code == 200
        return response.json()['messages'][0]
    def draft(chat):
        return store.get_session('https://odoo.invalid', 42, chat, 'fixture',
                                 database='fixture')['active_draft']

    send(201, 1, 'Tạo giúp mình một báo giá')
    send(201, 2, 'Tên khách hàng: Bệnh viện đa khoa TPHCM')
    prompt = send(201, 3, 'bệnh viện')
    assert draft(201)['quotation_data']['pricelist_id'] == 3
    assert draft(201)['quotation_data']['lines'] == []
    assert 'Bạn muốn báo giá sản phẩm nào và số lượng bao nhiêu?' in prompt
    assert 'Dòng 1: bệnh viện' not in prompt
    send(201, 4, '2 Bao tay')
    assert draft(201)['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]

    store.set_active_draft('https://odoo.invalid', 42, 202, 'fixture', {
        'draft_type': 'quotation', 'draft_id': 'legacy_price', 'version': 1,
        'content': 'Báo giá nháp', 'quotation_data': {'partner_id': 15,
            'pricelist_id': 3, 'lines': [{}]}, 'quotation_hints': {
            'partner_label': 'Bệnh viện đa khoa TPHCM', 'pricelist_label': 'KH bệnh viện (VND)',
            'pricelist_query': 'bệnh viện', 'product_query_1': 'bệnh viên',
            'product_query_1_needs_confirmation': True}}, database='fixture')
    legacy_prompt = send(202, 1, 'xem nháp báo giá')
    assert 'Thông tin ‘bệnh viên’ trước đó chỉ là bảng giá, đúng không?' in legacy_prompt
    assert 'Dòng 1: bệnh viên' not in legacy_prompt
    assert draft(202)['quotation_data']['lines'] == []
    product_prompt = send(202, 2, 'đúng')
    assert 'Bạn muốn báo giá sản phẩm nào và số lượng bao nhiêu?' in product_prompt
    send(202, 3, '2 Bao tay')
    assert draft(202)['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]
    assert ('products', 'bệnh viện') not in calls and ('products', 'bệnh viên') not in calls
