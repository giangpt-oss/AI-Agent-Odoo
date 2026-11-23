"""Stateful quote regressions. Provider and Odoo traffic are replaced at boundaries."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.business import quote_collector as collector, quote_optional, workflow
from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.services import natural_pipeline as natural_module
from app.services.confirmation_service import ConfirmationService
from app.services.conversation_history_store import ConversationHistoryStore


def test_tao_lich_hop_is_recognized_as_quotation_chatter():
    from app.business.operations import looks_like_quote_chatter
    assert looks_like_quote_chatter('tạo lịch họp ngày 12/11/2026 12:00 A.M ở s00089', {})


@pytest.mark.asyncio
async def test_natural_quote_routes_to_fact_extraction_not_sales_search(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    pipeline = natural_module.NaturalPipeline()
    quote = AsyncMock(return_value=natural_module.IntentAnalysisResult(
        intent_type=IntentType.READ, tool_name='sales.quotation.collect',
        parameters={'action': 'start', 'customer_query': 'An Phát', 'pricelist_query': 'Thương mại',
                    'line_updates': [{'line_index': 1, 'product_query': 'máy X', 'quantity': 2}]}))
    search = AsyncMock(return_value=natural_module.IntentAnalysisResult(
        intent_type=IntentType.READ, tool_name='sales.quotation.lookup', parameters={}))
    monkeypatch.setattr(pipeline, '_dispatch_quote_turn', quote)
    monkeypatch.setattr(pipeline, '_dispatch_with_llm', search)
    for text in ('Lên giúp em báo giá cho An Phát, 2 máy X, dùng giá thương mại nhé',
                 'Tạo giúp mình một báo giá'):
        result = await pipeline.analyze_input(text, context, {})
        assert result.tool_name == 'sales.quotation.collect'
        assert result.parameters['line_updates'][0]['quantity'] == 2
    assert quote.await_count == 2
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_creation_never_returns_previous_saved_quote(quote_runtime, monkeypatch):
    from app.business.quote_fallback import extract
    from app.business.quote_local import interpret

    context, _, _ = quote_runtime
    await collector.collect({'action': 'start'}, context)
    current = workflow.session(context)
    assert interpret('Tạo một báo giá', current['active_draft']) == {'action': 'start'}
    assert extract('Tạo giúp mình một báo giá', current['active_draft'])['action'] == 'start'
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(return_value=json.dumps({
        'action': 'status', 'quotation_name': 'S00097'})))
    result = await pipeline.analyze_input('Tạo giúp mình một báo giá', context, current)
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters['action'] == 'start'
    assert result.parameters.get('quotation_name') is None


@pytest.mark.asyncio
async def test_ok_while_collecting_quote_cannot_submit_approval(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start'}, context)
    current = workflow.session(context)
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(return_value='{"action":"confirm"}'))
    result = await pipeline.analyze_input('ok', context, current)
    assert result.intent_type != IntentType.ACTION_CONFIRM
    assert result.tool_name != 'business.quotation_flow'


def test_quotation_requires_pricelist_before_confirmation():
    draft = {'quotation_data': {'partner_id': 1, 'lines': [{'product_id': 2}]}, 'quotation_hints': {}}
    assert 'bảng giá' in collector.state_prompt(draft).lower()


def test_pricelist_selection_is_saved_as_a_named_reference():
    draft = {'quotation_data': {'lines': []}, 'quotation_hints': {}}
    collector.save_reference(draft, 'pricelists', 1, {'id': 7, 'display_name': 'Giá thương mại'})
    assert draft['quotation_data']['pricelist_id'] == 7
    assert 'Giá thương mại' in collector.quote_details(draft)


def test_six_groups_are_requested_without_guessing_pricelist_from_customer():
    draft = {'quotation_data': {'partner_id': 1, 'lines': [{'product_id': 2, 'quantity': 2}]},
             'quotation_hints': {'partner_label': 'Bệnh viện An Phát'}}
    prompt = collector.state_prompt(draft)
    assert 'Bạn muốn dùng bảng giá nào trong Odoo?' in prompt
    assert 'ngày hết hạn báo giá' in prompt
    assert 'điều khoản thanh toán' in prompt
    assert 'Bảng giá: chưa chọn' in prompt


@pytest.mark.parametrize('text,products', [
    ('Lên giúp mình báo giá cho An Nhiên Spa - Đông Y , 2 Bao tay, hết hạn ngày 16/12/2026, thanh toán 30 ngày nhé.', [('Bao tay', 2)]),
    ('Cho An Nhiên Spa - Đông Y lập báo giá, hai chiếc Bao tay, thanh toán 30 ngày, hết hạn ngày 16/12/2026', [('Bao tay', 2)]),
    ('TAO BAO GIA cho An Nhiên Spa - Đông Y, Bao tay lấy 2 cái, thanh toan 30 ngay, het han ngay 16/12/2026', [('Bao tay', 2)]),
    ('Soạn báo giá cho An Nhiên Spa - Đông Y, 2 Bao tay và 3 khẩu trang, thanh toán 30 ngày, hết hạn ngày 16/12/2026', [('Bao tay', 2), ('khẩu trang', 3)]),
])
def test_local_facts_preserve_names_and_line_quantities(text, products):
    from app.business.quote_fallback import extract
    facts = extract(text)
    assert facts['action'] == 'start'
    assert facts['customer_query'] == 'An Nhiên Spa - Đông Y'
    assert facts['validity_date'] == '2026-12-16'
    assert facts['payment_term_query'].casefold() == '30 ngày'
    assert [(row['product_query'], row['quantity']) for row in facts['line_updates']] == products


def test_duration_payment_and_expiry_are_distinct_and_bare_duration_is_ambiguous():
    from datetime import date
    from app.business.quote_fallback import extract
    facts = extract('Lập báo giá cho An Nhiên, 2 Bao tay, báo giá có hiệu lực 30 ngày, thanh toán trong 15 ngày',
                    today=date(2026, 11, 16))
    assert facts['validity_date'] == '2026-12-16'
    assert facts['payment_term_query'] == '15 ngày'
    active = {'draft_type': 'quotation', 'quotation_data': {'lines': [{}]}}
    assert extract('30 ngày', active) is None


@pytest.mark.asyncio
async def test_quantity_correction_uses_single_line_and_preserves_other_facts(quote_runtime):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 2, 'validity_date': '2026-12-16',
                             'payment_term_id': 19}, context)
    pipeline = natural_module.NaturalPipeline()
    turn = await pipeline.analyze_input('à đổi từ 2 lên 3 nhé', context, workflow.session(context))
    assert turn.parameters['quantity'] == 3
    await collector.collect(turn.parameters, context)
    data = workflow.session(context)['active_draft']['quotation_data']
    assert data['lines'][0]['quantity'] == 3
    assert data['validity_date'] == '2026-12-16' and data['payment_term_id'] == 19


@pytest.mark.asyncio
async def test_bare_duration_and_multiline_quantity_edit_ask_short_questions(quote_runtime):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'line_updates': [
        {'line_index': 1, 'quantity': 2}, {'line_index': 2, 'quantity': 4}]}, context)
    pipeline = natural_module.NaturalPipeline()
    draft = workflow.session(context)
    duration = await pipeline.analyze_input('30 ngày', context, draft)
    assert duration.intent_type == IntentType.CLARIFY
    assert 'hạn báo giá hay điều khoản thanh toán' in duration.clarification_prompt
    correction = await pipeline.analyze_input('à đổi từ 2 lên 3 nhé', context, draft)
    assert correction.intent_type == IntentType.CLARIFY
    assert 'sản phẩm nào' in correction.clarification_prompt


@pytest.mark.asyncio
async def test_model_failure_salvages_only_unambiguous_facts(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    class InvalidKey(Exception):
        code = 400
        def __str__(self):
            return 'API key not valid'
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(side_effect=InvalidKey())
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    result = await pipeline.analyze_input('Lên báo giá cho An Nhiên Spa - Đông Y, 2 Bao tay, ưu tiên giao nhanh', context, {})
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters['customer_query'] == 'An Nhiên Spa - Đông Y'
    assert result.parameters['line_updates'][0]['product_query'] == 'Bao tay'
    assert 'validity_date' not in result.parameters
    model.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_help_and_negation_never_mutate_quote_draft(quote_runtime):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 2}, context)
    before = workflow.session(context)['active_draft']
    pipeline = natural_module.NaturalPipeline()
    search = await pipeline.analyze_input('Tìm báo giá của An Nhiên', context, workflow.session(context))
    assert search.tool_name == 'business.lookup'
    assert search.parameters == {'kind': 'sales', 'query': 'An Nhiên'}
    help_result = await pipeline.analyze_input('Cách tạo báo giá thế nào?', context, workflow.session(context))
    assert help_result.intent_type == IntentType.REPLY
    negative = await pipeline.analyze_input('Đừng tạo báo giá', context, workflow.session(context))
    assert negative.intent_type == IntentType.REPLY
    assert workflow.session(context)['active_draft'] == before


@pytest.mark.asyncio
async def test_named_pdf_retry_targets_the_named_record_without_latest_draft(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    pipeline = natural_module.NaturalPipeline()
    result = await pipeline.analyze_input('Gửi lại PDF S00097', context, workflow.session(context))
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters == {'action': 'retry_pdf', 'quotation_name': 'S00097'}
    calls = []
    async def gateway(_context, _method, path, payload):
        calls.append((path, payload))
        return {'status': 'sent', 'name': 'S00097'}
    monkeypatch.setattr(collector, 'request', gateway)
    reply = await collector.collect(result.parameters, context)
    assert calls == [('quotations/pdf-retry', {'name': 'S00097'})]
    assert 'S00097' in reply[0]


@pytest.mark.asyncio
async def test_free_quote_request_uses_model_even_without_creation_verb(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(return_value=json.dumps({'action': 'start', 'customer_query': 'Minh Sơn',
        'line_updates': [{'line_index': 1, 'product_query': 'máy X', 'quantity': 2}]}, ensure_ascii=False))
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    result = await pipeline.analyze_input('Báo giá cho Minh Sơn gồm 2 máy X nhé', context, {})
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters['customer_query'] == 'Minh Sơn'
    assert result.parameters['line_updates'][0]['quantity'] == 2
    model.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_cannot_turn_clear_creation_into_search(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(
        return_value='{"action":"lookup","customer_query":"An Nhiên"}'))
    result = await pipeline.analyze_input('Tạo giúp mình một báo giá cho An Nhiên', context, {})
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters['action'] == 'start'


@pytest.mark.asyncio
async def test_model_quantity_without_target_cannot_change_first_of_two_lines(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'line_updates': [
        {'line_index': 1, 'quantity': 2}, {'line_index': 2, 'quantity': 4}]}, context)
    before = workflow.session(context)['active_draft']
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(
        return_value='{"action":"update","quantity":3}'))
    result = await pipeline.analyze_input('à lấy ba cái thôi', context, workflow.session(context))
    assert result.intent_type == IntentType.CLARIFY
    assert 'sản phẩm nào' in result.clarification_prompt
    assert workflow.session(context)['active_draft'] == before


@pytest.mark.asyncio
async def test_model_targets_one_line_and_keeps_the_other(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'line_updates': [
        {'line_index': 1, 'quantity': 2}, {'line_index': 2, 'quantity': 4}]}, context)
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(
        return_value='{"action":"update","line_updates":[{"line_index":2,"quantity":3}]}'))
    result = await pipeline.analyze_input('Sửa dòng 2 còn ba cái, dòng đầu giữ nguyên',
                                          context, workflow.session(context))
    assert result.tool_name == 'sales.quotation.collect'
    await collector.collect(result.parameters, context)
    lines = workflow.session(context)['active_draft']['quotation_data']['lines']
    assert [line['quantity'] for line in lines] == [2, 3]


@pytest.mark.asyncio
async def test_llm_gets_context_and_gateway_receives_exact_customer_name(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start'}, context)
    original = 'Tên khách hàng: Bệnh viện đa khoa TPHCM'
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(return_value=json.dumps({'action': 'update',
        'customer_query': 'Bệnh viện đa khoa TP HCM', 'pricelist_query': 'Bệnh viện',
        'pricelist_id': 51, 'payment_term_id': 30}))
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    observed = []
    async def gateway(_context, _method, _path, params):
        observed.append(params)
        return {'items': [{'id': 51, 'display_name': original.split(': ', 1)[1]}],
                'exact_match_unique': True}
    monkeypatch.setattr(collector, 'request', gateway)
    current = workflow.session(context)
    current['history'] = [{'role': 'assistant', 'content': 'Bạn cho biết tên khách hàng nhé.'}]
    result = await pipeline.analyze_input(original, context, current)
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters['customer_query'] == 'Bệnh viện đa khoa TPHCM'
    assert result.parameters.get('pricelist_query') is None
    assert result.parameters.get('pricelist_id') is None
    assert result.parameters.get('payment_term_id') is None
    prompt = model.call_args.args[0]
    assert original in prompt and 'pending_field' in prompt and 'draft' in prompt
    await collector.collect(result.parameters, context)
    assert observed[0]['query'] == 'Bệnh viện đa khoa TPHCM'
    assert workflow.session(context)['active_draft']['quotation_data']['partner_id'] == 51


@pytest.mark.asyncio
@pytest.mark.parametrize('question,answer,pending,field', [
    ('Bạn chọn điều khoản thanh toán nào?', {'action': 'update', 'payment_term_query': '30 ngày'}, 'payment_term', 'payment_term_query'),
    ('Báo giá hết hạn sau bao nhiêu ngày?', {'action': 'update', 'validity_date': '2026-12-16'}, 'validity_date', 'validity_date'),
])
async def test_llm_uses_previous_question_for_bare_duration(quote_runtime, monkeypatch, question, answer, pending, field):
    from datetime import date

    monkeypatch.setattr(natural_module, '_quote_today', lambda _context: date(2026, 11, 16))
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start'}, context)
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(return_value=json.dumps(answer, ensure_ascii=False))
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    current = workflow.session(context)
    current['history'] = [{'role': 'assistant', 'content': question}]
    result = await pipeline.analyze_input('30 ngày nhé', context, current)
    assert result.parameters[field] == answer[field]
    assert '"pending_field": "' + pending + '"' in model.call_args.args[0]


@pytest.mark.asyncio
async def test_gemini_client_reads_settings_and_validates_schema_locally(monkeypatch):
    from google import genai
    from app.business.quote_schema import QuoteTurn

    observed = {}

    def fake_client(**kwargs):
        observed['key'] = kwargs['api_key']

        def generate_content(**call):
            observed['call'] = call
            return SimpleNamespace(text='{"action":"start"}')

        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))

    monkeypatch.setattr(natural_module, 'get_settings', lambda: SimpleNamespace(
        GEMINI_API_KEY='fixture-only', DEFAULT_LLM_PROVIDER='gemini',
        GROQ_API_KEY=None, OPENAI_API_KEY=None, INTENT_LLM_MODEL=None,
        DEFAULT_LLM_MODEL='gemini-3.5-flash-lite'))
    monkeypatch.setattr(genai, 'Client', fake_client)
    response = await natural_module.NaturalPipeline()._call_gemini_raw(
        'Create a fictional quotation.', response_schema=QuoteTurn.model_json_schema())
    assert QuoteTurn.model_validate_json(response).action == 'start'
    assert observed['key'] == 'fixture-only'
    assert 'customer_query' in observed['call']['contents']
    assert observed['call']['config'].response_json_schema is None
    assert observed['call']['config'].response_mime_type == 'application/json'


@pytest.mark.asyncio
async def test_llm_sees_current_odoo_candidates_and_respects_selection(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'customer_query': 'Customer'}, context)
    current = workflow.session(context)
    current['last_read'] = {'business_candidates_partners': {'data': {'items': [
        {'id': 11, 'display_name': 'Công ty An'}, {'id': 12, 'display_name': 'Công ty Bình'}]}}}
    current['active_draft']['quotation_hints']['selection'] = {
        'kind': 'partners', 'ids': [11, 12], 'line_index': 1, 'at': time.time()}
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(return_value='{"action":"update","selected_id":12}')
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    result = await pipeline.analyze_input('mình lấy phương án Công ty Bình nhé', context, current)
    assert result.parameters['selected_id'] == 12
    prompt = model.call_args.args[0]
    assert 'Công ty Bình' in prompt and '"pending_field": "partners"' in prompt


@pytest.mark.asyncio
async def test_llm_change_of_mind_updates_only_requested_quantity(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 2, 'validity_date': '2026-12-16',
                             'payment_term_id': 19}, context)
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(return_value='{"action":"update","quantity":3}')
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    result = await pipeline.analyze_input('À không, chuyển sang 3 cái nhé', context, workflow.session(context))
    assert result.parameters['action'] == 'update'
    assert result.parameters['quantity'] == 3
    await collector.collect(result.parameters, context)
    data = workflow.session(context)['active_draft']['quotation_data']
    assert data['lines'][0]['quantity'] == 3
    assert data['validity_date'] == '2026-12-16' and data['payment_term_id'] == 19
    model.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_combined_facts_resolve_to_one_durable_draft(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(return_value=json.dumps({'action': 'start',
        'customer_query': 'An Nhiên Spa - Đông Y', 'product_query': 'Bao tay',
        'quantity': 2, 'validity_date': '2026-12-16', 'payment_term_query': '30 ngày',
        'pricelist_query': 'Thương mại'}, ensure_ascii=False))
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    async def lookup(_context, _method, _path, params):
        rows = {'partners': (11, 'An Nhiên Spa - Đông Y'), 'products': (22, 'Bao tay'),
                'payment_terms': (19, '30 ngày'), 'pricelists': (33, 'Thương mại')}
        record_id, name = rows[params['kind']]
        return {'items': [{'id': record_id, 'display_name': name}], 'exact_match_unique': True}
    monkeypatch.setattr(collector, 'request', lookup)
    text = 'Soạn báo giá cho An Nhiên Spa - Đông Y, 2 Bao tay, dùng giá Thương mại, hết hạn 16/12/2026, thanh toán 30 ngày'
    result = await pipeline.analyze_input(text, context, workflow.session(context))
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters['action'] == 'start'
    await collector.collect(result.parameters, context)
    data = workflow.session(context)['active_draft']['quotation_data']
    assert data['partner_id'] == 11 and data['pricelist_id'] == 33
    assert data['payment_term_id'] == 19 and data['validity_date'] == '2026-12-16'
    assert data['lines'] == [{'product_id': 22, 'quantity': 2}]
    model.assert_awaited_once()


@pytest.mark.asyncio
async def test_0940_message_keeps_five_facts_and_then_resolves_pricelist(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    calls = []
    async def lookup(_context, _method, _path, params):
        calls.append((params['kind'], params['query']))
        if params['kind'] == 'payment_terms' and params['query'] == '30 ngày':
            return {'items': [{'id': 19, 'name': '30 ngày', 'api_name': '30 Days'}],
                    'exact_match_unique': True}
        if params['kind'] == 'pricelists':
            return {'items': [{'id': 33, 'display_name': 'KH thương mại (VND)'}],
                    'unique_query_match': True}
        rows = {'partners': {'id': 11, 'display_name': 'An Nhiên Spa - Đông Y'},
                'products': {'id': 22, 'display_name': 'Bao tay'},
                'payment_terms': {'id': 19, 'display_name': '30 Days'}}
        return {'items': [rows[params['kind']]], 'exact_match_unique': True}
    monkeypatch.setattr(collector, 'request', lookup)
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(side_effect=RuntimeError('model unavailable'))
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    message = 'Lên giúp mình báo giá cho An Nhiên Spa - Đông Y , 2 Bao tay, hết hạn ngày 16/12/2026, thanh toán 30 ngày nhé.'
    first = await pipeline.analyze_input(message, context, workflow.session(context))
    assert first.tool_name == 'sales.quotation.collect'
    assert first.parameters['customer_query'] == 'An Nhiên Spa - Đông Y'
    reply = await collector.collect(first.parameters, context)
    assert len(reply) == 1 and 'KH thương mại (VND)' in reply[0]
    assert ('pricelists', '') in calls
    data = workflow.session(context)['active_draft']['quotation_data']
    assert data['partner_id'] == 11 and data['payment_term_id'] == 19
    assert data['validity_date'] == '2026-12-16'
    assert data['lines'] == [{'product_id': 22, 'quantity': 2}]
    assert ('payment_terms', '30 ngày') in calls and ('payment_terms', '30 Days') not in calls
    second = await pipeline.analyze_input('thương mại nhé', context, workflow.session(context))
    assert second.parameters['pricelist_query'].casefold() == 'thương mại'
    await collector.collect(second.parameters, context)
    complete = workflow.session(context)['active_draft']['quotation_data']
    assert complete['pricelist_id'] == 33
    assert complete['partner_id'] == 11 and complete['payment_term_id'] == 19
    assert complete['lines'] == [{'product_id': 22, 'quantity': 2}]
    assert model.await_count == 1  # The pending price-list reply is resolved locally.


@pytest.mark.asyncio
async def test_payment_term_catalog_lookup_keeps_original_query(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    queries = []
    async def lookup(_context, _method, _path, params):
        queries.append(params['query'])
        return {'items': [{'id': 19, 'name': '30 ngày', 'api_name': '30 Days'}],
                'exact_match_unique': True}
    monkeypatch.setattr(collector, 'request', lookup)
    result = await collector.lookup_reference(context, 'payment_terms', '30 ngày', {})
    assert queries == ['30 ngày']
    assert result['exact_match_unique'] is True


@pytest.mark.asyncio
async def test_duplicate_product_keeps_other_facts_until_selection(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    async def lookup(_context, _method, _path, params):
        kind = params['kind']
        if kind == 'products':
            return {'items': [{'id': 22, 'display_name': 'Bao tay loại A'},
                              {'id': 23, 'display_name': 'Bao tay loại B'}]}
        if kind == 'pricelists':
            return {'items': [{'id': 31, 'display_name': 'Giá thương mại'}]}
        return {'items': [{'id': {'partners': 11, 'payment_terms': 19}[kind],
                           'display_name': {'partners': 'An Nhiên Spa - Đông Y',
                                            'payment_terms': '30 ngày'}[kind]}],
                'exact_match_unique': True}
    monkeypatch.setattr(collector, 'request', lookup)
    pipeline = natural_module.NaturalPipeline()
    text = 'Lên giúp mình báo giá cho An Nhiên Spa - Đông Y, 2 Bao tay, hết hạn ngày 16/12/2026, thanh toán 30 ngày nhé.'
    turn = await pipeline.analyze_input(text, context, workflow.session(context))
    response = await collector.collect(turn.parameters, context)
    assert 'Bao tay loại A' in response[0] and 'Bao tay loại B' in response[0]
    before = workflow.session(context)['active_draft']['quotation_data']
    assert before['partner_id'] == 11 and before['payment_term_id'] == 19
    assert before['validity_date'] == '2026-12-16' and before['lines'][0]['quantity'] == 2
    await collector.collect({'selected_id': 23}, context)
    after = workflow.session(context)['active_draft']['quotation_data']
    assert after['lines'] == [{'product_id': 23, 'quantity': 2}]
    assert after['partner_id'] == 11 and after['payment_term_id'] == 19


@pytest.mark.asyncio
async def test_new_customer_does_not_overwrite_another_customer_draft(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'customer_query': 'Khách cũ'}, context)
    await collector.collect({'selected_id': 11}, context)
    before = workflow.session(context)['active_draft']
    pipeline = natural_module.NaturalPipeline()
    result = await pipeline.analyze_input('Lên báo giá cho An Nhiên Spa - Đông Y, 2 Bao tay',
                                          context, workflow.session(context))
    assert result.intent_type == IntentType.CLARIFY
    assert 'báo giá mới' in result.clarification_prompt
    assert workflow.session(context)['active_draft'] == before


def test_payment_term_selection_uses_verified_reference():
    draft = {'quotation_data': {'lines': []}, 'quotation_hints': {}}
    collector.save_reference(draft, 'payment_terms', 1, {'id': 19, 'display_name': '30 ngày'})
    assert draft['quotation_data']['payment_term_id'] == 19
    assert '30 ngày' in collector.quote_details(draft)


@pytest.mark.asyncio
async def test_combined_turn_keeps_facts_while_resolving_payment_term(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    async def exact(_context, _method, _path, params):
        kind = params['kind']
        rows = {'partners': {'id': 11, 'display_name': 'An Phát'},
                'products': {'id': 22, 'display_name': 'máy X'},
                'pricelists': {'id': 33, 'display_name': 'Thương mại'},
                'payment_terms': {'id': 19, 'display_name': '30 ngày'}}
        return {'items': [rows[kind]], 'exact_match_unique': True}
    monkeypatch.setattr(collector, 'request', exact)
    await collector.collect({'action': 'start', 'customer_query': 'An Phát',
        'pricelist_query': 'Thương mại', 'payment_term_query': '30 ngày',
        'validity_date': '2026-12-03',
        'line_updates': [{'line_index': 1, 'product_query': 'máy X', 'quantity': 2}]}, context)
    data = workflow.session(context)['active_draft']['quotation_data']
    assert data['partner_id'] == 11
    assert data['pricelist_id'] == 33
    assert data['payment_term_id'] == 19
    assert data['validity_date'] == '2026-12-03'
    assert data['lines'] == [{'product_id': 22, 'quantity': 2}]


@pytest.mark.asyncio
async def test_request_for_boss_requires_pdf_button_even_after_quote_created(quote_runtime, monkeypatch):
    from app.business import quote_status

    context, _, confirmations = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 1}, context)
    draft = workflow.session(context)['active_draft']
    cid, _ = confirmations.create_confirmation(user_id=1, chat_id=7, telegram_binding_id='fixture-binding',
        action='sales.quotation.create', arguments={}, preview_text='preview', owner_key=context.owner_key,
        draft_id=draft['draft_id'], draft_version=draft['version'], telegram_user_id=7)
    assert confirmations.claim_and_validate(cid, 1, 7, 'fixture-binding')[0]
    status_request = AsyncMock(return_value={
        'status': 'completed', 'result': {'id': 88, 'name': 'S00089', 'company_id': [1, 'Company']},
    })
    monkeypatch.setattr(quote_status, 'request', status_request)
    from app.core import config
    settings = config.get_settings()
    settings.ENABLE_QUOTATION_APPROVAL_FLOW = True
    pipeline = natural_module.NaturalPipeline()
    result = await pipeline.analyze_input('gửi yêu cầu xác nhận cho sếp', context, workflow.session(context))
    assert result.intent_type == IntentType.REPLY
    assert result.tool_name is None
    assert 'Xác nhận gửi duyệt' in result.reply_text
    status_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_quotation_chatter_routes_note_to_explicit_quote(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(return_value=json.dumps({
        'action': 'note', 'quotation_name': 'S00089', 'options': {'body': 'Đã gọi khách hàng'},
    })))
    result = await pipeline.analyze_input('Ghi chú báo giá S00089: Đã gọi khách hàng', context, {})
    assert result.tool_name == 'business.quotation_chatter'
    assert result.parameters == {'quotation_name': 'S00089', 'action': 'note', 'options': {'body': 'Đã gọi khách hàng'}}


@pytest.mark.asyncio
async def test_quotation_meeting_keeps_provided_time_and_people(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(return_value=json.dumps({
        'action': 'meeting', 'quotation_name': 'S12345', 'options': {
            'summary': 'Họp với khách', 'start': '2026-12-12T09:00:00+07:00',
            'stop': '2026-12-12T10:00:00+07:00', 'attendee_queries': ['Nguyễn An'],
        },
    })))
    result = await pipeline.analyze_input('Lên lịch họp báo giá S00089 với Nguyễn An', context, {})
    assert result.parameters['quotation_name'] == 'S00089'
    assert result.parameters['options']['start'] == '2026-12-12T09:00:00+07:00'
    assert result.parameters['options']['attendee_queries'] == ['Nguyễn An']


@pytest.mark.asyncio
async def test_quotation_message_creates_confirmation_for_correct_target(quote_runtime, monkeypatch):
    from app.business import operations

    context, store, confirmations = quote_runtime
    monkeypatch.setattr(operations, 'store', store)
    monkeypatch.setattr(operations, 'confirmation_service', confirmations)

    async def gateway(_context, method, path, payload=None):
        if path == 'business/lookup':
            assert payload['kind'] == 'sales' and payload['query'] == 'S00089'
            return {'items': [{'id': 88, 'name': 'S00089', 'company_id': [1, 'Company']}]}
        assert path == 'business/prepare' and method == 'POST'
        assert payload['action'] == 'business.sales.message'
        assert payload['payload']['target_id'] == 88
        assert payload['payload']['options'] == {'recipient': 'user@example.com', 'body': 'Xin chào'}
        return {'status': 'ready', 'prepared_id': 'prepared-1', 'preview': {
            'title': 'Gửi tin', 'target': {'id': 88, 'name': 'S00089'},
            'recipients': [{'email': 'user@example.com'}], 'body': 'Xin chào',
        }}

    monkeypatch.setattr(operations, 'request', gateway)
    messages = await operations.read('business.quotation_chatter', {
        'action': 'message', 'quotation_name': 'S00089',
        'options': {'recipient': 'user@example.com', 'body': 'Xin chào'},
    }, context)
    assert 'user@example.com' in messages[0] and 'Xin chào' in messages[0]
    assert confirmations.get_confirmation(messages[0].confirmation_id)['action'] == 'business.sales.message'


@pytest.mark.asyncio
async def test_exact_references_and_quantity_return_one_confirmation(quote_runtime, monkeypatch):
    context, _, confirmations = quote_runtime
    original = collector.request

    async def exact(*args):
        return {**await original(*args), 'exact_match_unique': True}

    monkeypatch.setattr(collector, 'request', exact)
    messages = await collector.collect({'action': 'start', 'customer_query': 'Customer',
                                        'product_query': 'Product', 'quantity': 2,
                                        'validity_date': '2026-12-03', 'payment_term_id': 19}, context)
    assert 'bảng giá' in messages[0]
    messages = await collector.collect({'pricelist_query': 'Bảng giá'}, context)
    assert 'Đã đủ thông tin bắt buộc' in messages[0]
    preview = (await quote_optional.handle('0', context, workflow.session(context)))[0]
    assert 'Customer' in preview and 'Product' in preview
    saved = confirmations.get_confirmation(preview.confirmation_id)
    assert json.loads(saved['arguments_json']) == {'partner_id': 11, 'pricelist_id': 33,
        'validity_date': '2026-12-03', 'payment_term_id': 19, 'lines': [{'product_id': 22, 'quantity': 2}]}
    assert (await workflow.prepare(context))[0].confirmation_id == preview.confirmation_id


@pytest.mark.asyncio
@pytest.mark.parametrize('change_quantity', [False, True])
async def test_confirmed_quote_resumes_only_for_missing_fields(quote_runtime, monkeypatch, change_quantity):
    from app.business import quote_status
    from app.services import unified_executor as executor_module

    context, store, confirmations = quote_runtime
    original = collector.request
    async def unique_pricelist(*args):
        data = await original(*args)
        return {**data, 'exact_match_unique': True} if args[3]['kind'] == 'pricelists' else data
    monkeypatch.setattr(collector, 'request', unique_pricelist)
    await collector.collect({'action': 'start', 'customer_query': 'Customer', 'product_query': 'Product', 'quantity': 2,
                             'validity_date': '2026-12-03', 'payment_term_id': 19}, context)
    await collector.collect({'selected_id': 11}, context)
    await collector.collect({'selected_id': 22}, context)
    menu = (await collector.collect({'pricelist_query': 'Bảng giá'}, context))[0]
    assert 'Đã đủ thông tin bắt buộc' in menu
    preview = (await quote_optional.handle('0', context, workflow.session(context)))[0]
    cid = preview.confirmation_id
    assert confirmations.claim_and_validate(cid, 1, 7, 'fixture-binding')[0]
    remote = {'status': 'failed', 'result': {'missing_fields': ['custom_fields.x_reference'],
                                           'field_labels': {'custom_fields.x_reference': 'Mã tham chiếu nội bộ'}}}
    monkeypatch.setattr(quote_status, 'request', AsyncMock(return_value=remote))
    monkeypatch.setattr(executor_module, 'confirmation_service', confirmations)
    monkeypatch.setattr(executor_module, 'conversation_history_store', store)
    execute = AsyncMock(return_value={'status': 'in_progress'})
    monkeypatch.setattr(executor_module.gateway_client, 'execute_action', execute)
    correction = {'custom_fields': {'x_reference': 'ABC'}}
    if change_quantity:
        correction['quantity'] = 3
    message = (await collector.collect(correction, context))[0]
    if change_quantity:
        assert message.confirmation_id != cid
        execute.assert_not_called()
    else:
        assert 'Đang tạo báo giá' in message
        execute.assert_awaited_once()
        attempt = confirmations.get_confirmation(execute.call_args.kwargs['operation_id'])
        assert attempt['operation_id'] != confirmations.get_confirmation(cid)['operation_id']
        assert json.loads(attempt['arguments_json'])['custom_fields'] == {'x_reference': 'ABC'}


@pytest.fixture
def quote_runtime(tmp_path, monkeypatch):
    store = ConversationHistoryStore(str(tmp_path / 'conversation.db'))
    confirmations = ConfirmationService(tmp_path / 'confirmations.db')
    for module in (collector, workflow):
        monkeypatch.setattr(module, 'store', store)
        monkeypatch.setattr(module, 'confirmation_service', confirmations)
    context = GatewayUserContext(user_id=1, tenant='https://odoo.invalid', database='fixture', odoo_uid=1,
        chat_id=7, telegram_user_id=7, telegram_binding_id='fixture-binding', permissions={'sales.create'})
    settings = SimpleNamespace(ENABLE_QUOTATIONS=True, ENABLE_REPORTING=True)
    from app.core import config
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    monkeypatch.setattr(natural_module, 'get_settings', lambda: settings)
    monkeypatch.setattr(natural_module.NaturalPipeline, '_call_gemini_raw',
                        AsyncMock(side_effect=RuntimeError('provider disabled in unit tests')))

    async def rpc(ctx, method, path, params=None):
        assert method == 'GET' and path == 'quotations/lookup'
        if params['kind'] == 'pricelists':
            return {'items': [{'id': 33, 'display_name': 'Bảng giá'}]}
        return {'items': [{'id': 11, 'display_name': 'Customer'}]} if params['kind'] == 'partners' else {'items': [{'id': 22, 'display_name': 'Product', 'list_price': 100}]}

    monkeypatch.setattr(collector, 'request', rpc)
    return context, store, confirmations


@pytest.mark.asyncio
async def test_select_pricelist_before_quote_preview(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    async def lookup(_context, _method, _path, params):
        if params['kind'] == 'partners':
            return {'items': [{'id': 11, 'display_name': 'Customer'}], 'exact_match_unique': True}
        if params['kind'] == 'products':
            return {'items': [{'id': 22, 'display_name': 'Product'}], 'exact_match_unique': True}
        if params['query'] == 'Giá bệnh viện':
            return {'items': [{'id': 32, 'display_name': 'Giá bệnh viện'}], 'exact_match_unique': True}
        return {'items': [{'id': 31, 'display_name': 'Giá thương mại'}, {'id': 32, 'display_name': 'Giá bệnh viện'}]}
    monkeypatch.setattr(collector, 'request', lookup)
    monkeypatch.setattr(workflow, 'prepare', AsyncMock(return_value=['preview']))
    result = await collector.collect({'action': 'start', 'customer_query': 'Customer', 'product_query': 'Product'}, context)
    assert len(result) == 1 and 'Giá thương mại' in result[0] and 'Giá bệnh viện' in result[0]
    assert not workflow.session(context)['active_draft']['quotation_data'].get('pricelist_id')
    result = await collector.collect({'pricelist_query': 'Giá bệnh viện'}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['pricelist_id'] == 32
    assert 'Giá bệnh viện' in collector.quote_details(draft)
    assert result == ['preview']


def test_changing_pricelist_clears_old_explicit_prices():
    draft = {'quotation_data': {'pricelist_id': 31, 'lines': [{'product_id': 22, 'price_unit': 100, 'price_confirmed': True}]}, 'quotation_hints': {}}
    collector.save_reference(draft, 'pricelists', 1, {'id': 32, 'display_name': 'Giá bệnh viện'})
    assert draft['quotation_data']['lines'] == [{'product_id': 22}]


def test_multiline_quote_facts_are_extracted_without_ai():
    from app.business.quote_local import interpret
    text = ('Ban bảo vệ chăm sóc sức khỏe cán bộ Quảng Ninh\n'
            'bảng giá : Giá cho KH thương mại (VND)\n'
            'Dụng cụ tập cơ bàn, ngón tay Model: SY-HR03E, số lượng 2\n'
            'Bộ tập trị liệu tay. Model: HB-SGN3, số lượng 3\nthuế bỏ qua')
    facts = interpret(text, {'draft_type': 'quotation'})
    assert facts['customer_query'] == 'Ban bảo vệ chăm sóc sức khỏe cán bộ Quảng Ninh'
    assert facts['pricelist_query'] == 'Giá cho KH thương mại (VND)'
    assert facts['line_updates'] == [
        {'line_index': 1, 'product_query': 'SY-HR03E', 'quantity': 2.0, 'tax_ids': []},
        {'line_index': 2, 'product_query': 'HB-SGN3', 'quantity': 3.0, 'tax_ids': []},
    ]


def test_multiline_extraction_rejects_unrecognized_clauses():
    from app.business.quote_local import interpret
    assert interpret('Khách A\nbảng giá: Giá A\nSản phẩm Model: X-1, số lượng 2\nchiết khấu 30%', {'draft_type': 'quotation'}) is None


@pytest.mark.asyncio
async def test_pricelist_search_retries_without_descriptive_prefix(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    queries = []
    async def lookup(_context, _method, _path, params):
        queries.append(params['query'])
        return {'items': [] if len(queries) == 1 else [{'id': 2, 'display_name': 'KH thương mại (VND)'}]}
    monkeypatch.setattr(collector, 'request', lookup)
    result = await collector.lookup_reference(context, 'pricelists', 'Giá cho KH thương mại (VND)', {})
    assert queries == ['Giá cho KH thương mại (VND)', 'KH thương mại']
    assert result['items'][0]['id'] == 2
    assert not result.get('exact_match_unique')


@pytest.mark.asyncio
async def test_names_quantity_and_decline_more_are_real_durable_state(quote_runtime):
    context, _, _ = quote_runtime
    for turn in ({'action': 'start'}, {'customer_query': 'Nguyễn Văn A'}, {'product_query': 'bao tay'}, {'quantity': 1}, {'action': 'finish_lines'}, {'customer_lead': 10}):
        await collector.collect(turn, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_hints']['customer_query'] == 'Nguyễn Văn A'
    assert draft['quotation_hints']['product_query_1'] == 'bao tay'
    assert draft['quotation_hints']['lines_complete'] is True
    assert draft['quotation_data']['lines'] == [{'quantity': 1, 'customer_lead': 10}]
    assert not draft['quotation_data'].get('partner_id')
    await collector.collect({'selected_id': 11}, context)
    await collector.collect({'selected_id': 22}, context)
    final = workflow.session(context)['active_draft']['quotation_data']
    assert final['partner_id'] == 11
    assert final['lines'][0]['product_id'] == 22
    assert not final['lines'][0].get('price_confirmed')
    assert 'tax_ids' not in final['lines'][0]


@pytest.mark.asyncio
async def test_date_is_not_duration_and_unknown_id_is_rejected(quote_runtime):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'customer_lead': 10}, context)
    messages = await collector.collect({'delivery_date': '2026-11-03'}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['lines'][0]['customer_lead'] == 10
    assert 'ngày lịch' in messages[0]
    before = draft['version']
    result = await collector.collect({'selected_id': 999}, context)
    assert 'chưa xác minh' in result[0]
    assert workflow.session(context)['active_draft']['version'] == before


@pytest.mark.asyncio
async def test_facts_are_saved_before_preview_and_edit_invalidates_confirmation(quote_runtime, monkeypatch):
    context, _, confirmations = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 1}, context)
    preview = AsyncMock(return_value=['preparation queued'])
    monkeypatch.setattr(workflow, 'prepare', preview)
    await collector.collect({'action': 'preview', 'customer_lead': 10}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['lines'][0]['customer_lead'] == 10
    preview.assert_awaited_once()
    cid, _ = confirmations.create_confirmation(user_id=1, chat_id=7, telegram_binding_id='fixture-binding',
        action='sales.quotation.create', arguments={}, preview_text='preview', draft_id=draft['draft_id'], draft_version=draft['version'])
    await collector.collect({'quantity': 2}, context)
    assert confirmations.get_confirmation(cid)['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_active_quote_uses_typed_facts_and_does_not_confirm_price_answer(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start'}, context)
    pipeline = natural_module.NaturalPipeline()
    fake_model = AsyncMock(return_value=json.dumps({'action': 'update', 'price_unit': 200000, 'price_confirmed': True}))
    monkeypatch.setattr(pipeline, '_call_gemini_raw', fake_model)
    result = await pipeline.analyze_input('đồng ý đơn giá 200000', context, workflow.session(context))
    assert result.intent_type == IntentType.READ
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters['price_unit'] == 200000


@pytest.mark.asyncio
async def test_quote_interpreter_receives_recent_history_and_visible_selection_names(quote_runtime, monkeypatch):
    import time
    context, _, _ = quote_runtime
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(return_value='{"action":"update","selected_id":32}')
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    active = {'draft_type': 'quotation', 'draft_id': 'quote-test', 'quotation_data': {'lines': []},
              'quotation_hints': {'selection': {'kind': 'pricelists', 'ids': [32], 'at': time.time()}}}
    history = [{'role': 'user', 'content': f'tin nhắn {i}'} for i in range(10)]
    context_data = {'active_draft': active, 'history': history, 'last_read': {
        'business_candidates_pricelists': {'data': {'items': [{'id': 32, 'display_name': 'Giá bệnh viện'},
                                                               {'id': 99, 'display_name': 'Không còn được chọn'}]}}}}
    result = await pipeline.analyze_input('lấy cái cho bệnh viên nhé', context, context_data)
    assert result.parameters['selected_id'] == 32
    prompt = model.call_args.args[0]
    assert 'tin nhắn 0' in prompt and 'Giá bệnh viện' in prompt
    assert 'Không còn được chọn' not in prompt


@pytest.mark.asyncio
async def test_model_permission_error_does_not_blame_user(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start'}, context)
    class ProviderDenied(Exception):
        code = 403
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(side_effect=ProviderDenied()))
    result = await pipeline.analyze_input('đổi số lượng thành hai', context, workflow.session(context))
    assert 'quyền' in result.clarification_prompt.lower()
    assert 'chưa được' in result.clarification_prompt.lower()
    assert 'customer_lead' not in result.clarification_prompt


@pytest.mark.asyncio
async def test_model_timeout_preserves_quote_and_does_not_claim_saved(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 1}, context)
    before = workflow.session(context)['active_draft']
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(side_effect=asyncio.TimeoutError()))
    result = await pipeline.analyze_input('giao trong vòng mười ngày làm việc', context, workflow.session(context))
    assert result.intent_type == IntentType.CLARIFY
    assert 'chưa được điền' in result.clarification_prompt
    assert workflow.session(context)['active_draft'] == before


@pytest.mark.asyncio
async def test_select_command_needs_no_ai_and_keeps_pending_reference_check(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'customer_query': 'Customer', 'product_query': 'Product'}, context)
    pipeline = natural_module.NaturalPipeline()
    fake_model = AsyncMock(side_effect=AssertionError('Selection must not call AI'))
    monkeypatch.setattr(pipeline, '_call_gemini_raw', fake_model)
    result = await pipeline.analyze_input('/quote select 11', context, workflow.session(context))
    assert result.tool_name == 'sales.quotation.collect'
    assert result.parameters['selected_id'] == 11
    messages = await collector.collect(result.parameters, context)
    assert workflow.session(context)['active_draft']['quotation_data']['partner_id'] == 11
    assert 'ID 22' in messages[0] and '/quote' not in messages[0]
    fake_model.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_error_explains_limit_and_provides_safe_next_step(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'customer_query': 'Customer'}, context)
    class QuotaError(Exception):
        code = 429
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(side_effect=QuotaError()))
    result = await pipeline.analyze_input('đúng rồi, tạo báo giá đi', context, workflow.session(context))
    assert 'giới hạn lượt gọi' in result.clarification_prompt
    assert '11' in result.clarification_prompt and '/quote' not in result.clarification_prompt
    assert not workflow.session(context)['active_draft']['quotation_data'].get('partner_id')


@pytest.mark.asyncio
async def test_catalog_price_no_tax_and_expiry_do_not_require_delivery_days(quote_runtime):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 1}, context)
    messages = await collector.collect({'use_catalog_price': True, 'tax_ids': [], 'validity_date': '2026-12-03'}, context)
    draft = workflow.session(context)['active_draft']
    line = draft['quotation_data']['lines'][0]
    assert line['use_catalog_price'] is True and line['tax_ids'] == []
    assert 'customer_lead' not in line and 'price_unit' not in line
    assert draft['quotation_data']['validity_date'] == '2026-12-03'
    assert 'giá niêm yết Odoo' in messages[0]
    assert 'thời gian giao hàng tính bằng số ngày' not in messages[0]


@pytest.mark.asyncio
async def test_quote_status_tracks_execution_and_never_creates_again(quote_runtime, monkeypatch):
    from app.business import quote_status
    context, _, confirmations = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 1}, context)
    draft = workflow.session(context)['active_draft']
    cid, _ = confirmations.create_confirmation(user_id=1, chat_id=7, telegram_binding_id='fixture-binding',
        action='sales.quotation.create', arguments={}, preview_text='preview', owner_key=context.owner_key,
        draft_id=draft['draft_id'], draft_version=draft['version'])
    reply = await workflow.prepare(context, status_only=True)
    assert 'Chờ xác nhận' in reply[0]
    ok, _, _ = confirmations.claim_and_validate(cid, 1, 7, 'fixture-binding', owner_key=context.owner_key)
    assert ok
    confirmations.mark_unknown(cid, 'timeout')
    rpc = AsyncMock(return_value={'status': 'completed', 'result': {'id': 99, 'name': 'S0099', 'state': 'draft', 'amount_total': 1}})
    monkeypatch.setattr(quote_status, 'request', rpc)
    reply = await workflow.prepare(context, status_only=True)
    assert 'S0099' in reply[0] and 'Đã tạo' in reply[0]
    assert confirmations.get_confirmation(cid)['status'] == 'completed'
    assert rpc.call_args.args[1] == 'GET'
    # Asking to preview a completed draft must never issue a new confirmation/create.
    assert 'S0099' in (await workflow.prepare(context))[0]


@pytest.mark.asyncio
async def test_natural_confirmation_is_bound_to_quote_without_command(quote_runtime, monkeypatch):
    context, _, confirmations = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 1}, context)
    draft = workflow.session(context)['active_draft']
    cid, preview = confirmations.create_confirmation(user_id=1, chat_id=7, telegram_binding_id='fixture-binding',
        action='sales.quotation.create', arguments={}, preview_text='preview', owner_key=context.owner_key,
        draft_id=draft['draft_id'], draft_version=draft['version'])
    assert '/confirm' not in preview and '/quote' not in preview
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(return_value='{"action":"confirm"}'))
    result = await pipeline.analyze_input('tôi xác nhận tạo theo bản vừa xem', context, workflow.session(context))
    assert result.intent_type == IntentType.ACTION_CONFIRM
    assert result.confirmation_id == cid
    assert confirmations.get_confirmation(cid)['status'] == 'pending'


@pytest.mark.asyncio
async def test_natural_confirm_without_preview_cannot_create(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start'}, context)
    pipeline = natural_module.NaturalPipeline()
    monkeypatch.setattr(pipeline, '_call_gemini_raw', AsyncMock(return_value='{"action":"confirm"}'))
    result = await pipeline.analyze_input('tôi xác nhận tạo', context, workflow.session(context))
    assert result.intent_type == IntentType.CLARIFY
    assert 'bản xem trước' in result.clarification_prompt


def test_general_action_preview_keeps_reference_without_command_templates(quote_runtime):
    _, _, confirmations = quote_runtime
    cid, preview = confirmations.create_confirmation(user_id=1, chat_id=7, telegram_binding_id='fixture-binding',
        action='calendar.create', arguments={}, preview_text='Cuộc họp ngày mai')
    assert cid in preview and 'Cuộc họp ngày mai' in preview
    assert '/confirm' not in preview and '/cancel' not in preview
    assert 'đồng ý' in preview and 'từ chối' in preview


@pytest.mark.asyncio
async def test_short_yes_uses_actual_quote_stage_without_model(quote_runtime, monkeypatch):
    context, _, confirmations = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 1}, context)
    draft = workflow.session(context)['active_draft']
    pipeline = natural_module.NaturalPipeline()
    model = AsyncMock(side_effect=AssertionError('Clear lifecycle replies must not require AI'))
    monkeypatch.setattr(pipeline, '_call_gemini_raw', model)
    workflow.remember(context, 'quotation_preparation_job', {'job_id': 'fixture', 'draft_id': draft['draft_id'], 'draft_version': draft['version']})
    result = await pipeline.analyze_input('có', context, workflow.session(context))
    assert result.tool_name == 'sales.quotation.collect' and result.parameters['action'] == 'status'
    cid, _ = confirmations.create_confirmation(user_id=1, chat_id=7, telegram_binding_id='fixture-binding',
        action='sales.quotation.create', arguments={}, preview_text='preview', owner_key=context.owner_key,
        draft_id=draft['draft_id'], draft_version=draft['version'])
    result = await pipeline.analyze_input('có', context, workflow.session(context))
    assert result.intent_type == IntentType.ACTION_CONFIRM and result.confirmation_id == cid
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_required_addresses_are_filled_only_after_explicit_user_choice(quote_runtime):
    context, _, _ = quote_runtime
    await collector.collect({'action': 'start', 'customer_query': 'Demo'}, context)
    await collector.collect({'selected_id': 11}, context)
    data = workflow.session(context)['active_draft']['quotation_data']
    assert 'partner_invoice_id' not in data and 'partner_shipping_id' not in data
    await collector.collect({'use_customer_addresses': True}, context)
    data = workflow.session(context)['active_draft']['quotation_data']
    assert data['partner_invoice_id'] == 11 and data['partner_shipping_id'] == 11


def _complete_optional_fixture(context):
    collector.save(context, {'draft_type': 'quotation', 'draft_id': 'quote_optional_fixture',
        'version': 1, 'content': 'Báo giá nháp', 'quotation_data': {
            'partner_id': 11, 'pricelist_id': 33, 'validity_date': '2026-12-20',
            'payment_term_id': 19, 'lines': [{'product_id': 22, 'quantity': 2}]},
        'quotation_hints': {}}, None)


@pytest.mark.asyncio
async def test_optional_menu_seven_twelve_and_skip_preserve_required_facts(quote_runtime, monkeypatch):
    context, _, confirmations = quote_runtime
    _complete_optional_fixture(context)
    async def optional_rpc(_context, method, path, params):
        assert method == 'GET' and path == 'quotations/optional-metadata'
        return {'available': True, 'type': 'selection' if params['field'] == 'x_studio_thoi_gian_bao_tri' else 'datetime',
                'options': [{'value': '3 tháng', 'label': '3 tháng'},
                            {'value': '12 tháng', 'label': '12 tháng'}] if params['field'] == 'x_studio_thoi_gian_bao_tri' else []}
    monkeypatch.setattr(quote_optional, 'request', optional_rpc)
    menu = (await workflow.prepare(context))[0]
    assert all(f'{number}.' in menu for number in range(1, 20))
    assert confirmations.latest_draft_confirmation(user_id=1, chat_id=7,
        telegram_binding_id='fixture-binding', draft_id='quote_optional_fixture') is None
    reply = await quote_optional.handle('7, 12', context, workflow.session(context))
    assert 'Thời gian bảo trì' in reply[0]
    reply = await quote_optional.handle('2', context, workflow.session(context))
    assert 'Ngày giao hàng' in reply[0]
    reply = await quote_optional.handle('20/12/2026 14:30', context, workflow.session(context))
    assert '12 tháng' in reply[0] and '20/12/2026 14:30' in reply[0]
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['optional_fields'] == {
        'x_studio_thoi_gian_bao_tri': '12 tháng', 'commitment_date': '2026-12-20 07:30:00'}
    assert draft['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]
    preview = (await quote_optional.handle('Xong', context, workflow.session(context)))[0]
    assert 'Thời gian bảo trì: 12 tháng' in preview
    assert 'Ngày giao hàng' in preview


@pytest.mark.asyncio
async def test_optional_skip_all_and_invalid_menu_do_not_set_defaults(quote_runtime):
    context, _, _ = quote_runtime
    _complete_optional_fixture(context)
    await workflow.prepare(context)
    assert '1 đến 19' in (await quote_optional.handle('20', context, workflow.session(context)))[0]
    assert 'trùng' in (await quote_optional.handle('7, 7', context, workflow.session(context)))[0]
    preview = (await quote_optional.handle('Bỏ qua', context, workflow.session(context)))[0]
    assert preview.confirmation_id
    assert 'optional_fields' not in workflow.session(context)['active_draft']['quotation_data']


@pytest.mark.asyncio
async def test_optional_relation_candidate_and_false_boolean(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    _complete_optional_fixture(context)
    async def optional_rpc(_context, _method, path, params):
        if path == 'quotations/optional-metadata':
            return {'available': True, 'type': 'many2one' if params['field'] == 'team_id' else 'boolean',
                    'options': []}
        assert path == 'quotations/optional-candidates' and params['field'] == 'team_id'
        return {'items': [{'id': 31, 'display_name': 'Miền Bắc'},
                          {'id': 52, 'display_name': 'Miền Nam'}], 'exact_match_unique': False}
    monkeypatch.setattr(quote_optional, 'request', optional_rpc)
    await workflow.prepare(context)
    assert 'Bộ phận sales' in (await quote_optional.handle('2, 3', context, workflow.session(context)))[0]
    assert 'Miền Nam' in (await quote_optional.handle('Miền', context, workflow.session(context)))[0]
    assert 'Chữ ký online' in (await quote_optional.handle('2', context, workflow.session(context)))[0]
    await quote_optional.handle('Không', context, workflow.session(context))
    data = workflow.session(context)['active_draft']['quotation_data']['optional_fields']
    assert data == {'team_id': 52, 'require_signature': False}


@pytest.mark.asyncio
async def test_optional_skip_one_add_more_and_keep_explicit_false(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    _complete_optional_fixture(context)
    async def optional_rpc(_context, _method, path, params):
        assert path == 'quotations/optional-metadata'
        return {'available': True, 'type': 'boolean' if params['field'] in
                ('require_signature', 'require_payment') else 'char', 'options': []}
    monkeypatch.setattr(quote_optional, 'request', optional_rpc)
    await workflow.prepare(context)
    await quote_optional.handle('3, 4', context, workflow.session(context))
    assert 'Thanh toán online' in (await quote_optional.handle('Bỏ qua', context, workflow.session(context)))[0]
    assert 'Thêm mục khác' in (await quote_optional.handle('Không', context, workflow.session(context)))[0]
    assert '1. Nhân viên sales' in (await quote_optional.handle('Thêm mục khác', context, workflow.session(context)))[0]
    assert 'Tham chiếu khách hàng' in (await quote_optional.handle('5', context, workflow.session(context)))[0]
    await quote_optional.handle('REF-123', context, workflow.session(context))
    preview = (await quote_optional.handle('Xong', context, workflow.session(context)))[0]
    data = workflow.session(context)['active_draft']['quotation_data']['optional_fields']
    assert data == {'require_payment': False, 'client_order_ref': 'REF-123'}
    assert 'Thanh toán online: Không' in preview
    assert (await workflow.prepare(context))[0].confirmation_id == preview.confirmation_id


@pytest.mark.asyncio
async def test_online_signature_and_payment_can_both_be_enabled(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    _complete_optional_fixture(context)
    async def optional_rpc(_context, _method, path, _params):
        assert path == 'quotations/optional-metadata'
        return {'available': True, 'type': 'boolean', 'options': []}
    monkeypatch.setattr(quote_optional, 'request', optional_rpc)
    await workflow.prepare(context)
    await quote_optional.handle('3, 4', context, workflow.session(context))
    await quote_optional.handle('Có', context, workflow.session(context))
    await quote_optional.handle('Có', context, workflow.session(context))
    assert workflow.session(context)['active_draft']['quotation_data']['optional_fields'] == {
        'require_signature': True, 'require_payment': True}


@pytest.mark.parametrize(('message', 'expected'), [
    ('bệnh viện', 'bệnh viện'),
    ('Bệnh viện', 'Bệnh viện'),
    ('bảng giá: Bệnh viện', 'Bệnh viện'),
    ('bảng giá là Bệnh viện đấy', 'Bệnh viện'),
    ('Giá cho KH bệnh viện (VND)', 'Giá cho KH bệnh viện (VND)'),
    ('bảng giá: Giá cho KH bệnh viện (VND)', 'Giá cho KH bệnh viện (VND)'),
])
def test_waiting_pricelist_uses_exact_original_text(message, expected):
    from app.business.quote_local import interpret
    draft = {'draft_type': 'quotation', 'quotation_data': {'partner_id': 15, 'lines': []},
             'quotation_hints': {'pricelist_lookup_pending': True}}
    assert interpret(message, draft) == {'action': 'update', 'pricelist_query': expected}
    assert interpret('sản phẩm Bao tay', draft) != {'action': 'update', 'pricelist_query': 'Bao tay'}


@pytest.mark.asyncio
async def test_pricelist_full_name_resolves_verified_odoo_row_and_preserves_facts(quote_runtime, monkeypatch):
    context, _, confirmations = quote_runtime
    _complete_optional_fixture(context)
    prior = workflow.session(context)['active_draft']
    prior['quotation_data'].pop('pricelist_id')
    collector.save(context, prior, workflow.session(context)['active_draft'])
    queries = []
    async def lookup(_context, _method, path, params):
        assert path == 'quotations/lookup' and params['kind'] == 'pricelists'
        queries.append(params['query'])
        if params['query'] == 'Giá cho KH bệnh viện (VND)':
            return {'items': []}
        assert params['query'] == 'KH bệnh viện'
        return {'items': [{'id': 3, 'name': 'KH bệnh viện',
                           'display_name': 'KH bệnh viện (VND)'}], 'exact_match_unique': True}
    monkeypatch.setattr(collector, 'request', lookup)
    from app.business.quote_local import interpret
    turn = interpret('bảng giá: Giá cho KH bệnh viện (VND)', workflow.session(context)['active_draft'])
    reply = await collector.collect(turn, context)
    assert queries == ['Giá cho KH bệnh viện (VND)', 'KH bệnh viện']
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['pricelist_id'] == 3
    assert draft['quotation_hints']['pricelist_label'] == 'KH bệnh viện (VND)'
    assert draft['quotation_data']['partner_id'] == 11
    assert draft['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]
    assert draft['quotation_data']['validity_date'] == '2026-12-20'
    assert draft['quotation_data']['payment_term_id'] == 19
    assert 'Đã đủ thông tin bắt buộc' in reply[0]
    assert confirmations.latest_draft_confirmation(user_id=1, chat_id=7,
        telegram_binding_id='fixture-binding', draft_id=draft['draft_id']) is None
    preview = (await quote_optional.handle('0', context, workflow.session(context)))[0]
    saved = confirmations.get_confirmation(preview.confirmation_id)
    assert json.loads(saved['arguments_json'])['pricelist_id'] == 3


@pytest.mark.asyncio
async def test_pricelist_ambiguous_missing_then_change_keeps_draft(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    _complete_optional_fixture(context)
    prior = workflow.session(context)['active_draft']
    prior['quotation_data'].pop('pricelist_id')
    collector.save(context, prior, workflow.session(context)['active_draft'])
    async def lookup(_context, _method, path, params):
        assert path == 'quotations/lookup'
        if params['query'] == 'không tồn tại':
            return {'items': []}
        if params['query'].casefold() == 'bệnh viện':
            return {'items': [{'id': 3, 'display_name': 'KH bệnh viện (VND)'},
                              {'id': 7, 'display_name': 'Bệnh viện khác (VND)'}]}
        return {'items': [{'id': 2, 'display_name': 'KH thương mại (VND)'}],
                'unique_query_match': True}
    monkeypatch.setattr(collector, 'request', lookup)
    missing = await collector.collect({'pricelist_query': 'không tồn tại'}, context)
    assert 'Không tìm thấy' in missing[0]
    assert workflow.session(context)['active_draft']['quotation_data']['partner_id'] == 11
    ambiguous = await collector.collect({'pricelist_query': 'Bệnh viện'}, context)
    assert '1. KH bệnh viện' in ambiguous[0] and '2. Bệnh viện khác' in ambiguous[0]
    from app.business.quote_local import interpret
    selected = interpret('1', workflow.session(context)['active_draft'], workflow.session(context))
    await collector.collect(selected, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['pricelist_id'] == 3
    assert draft['quotation_data']['partner_id'] == 11
    assert draft['quotation_data']['lines'][0]['quantity'] == 2
    await collector.collect({'pricelist_query': 'Thương mại'}, context)
    changed = workflow.session(context)['active_draft']
    assert changed['quotation_data']['pricelist_id'] == 2
    assert changed['quotation_data']['lines'][0]['quantity'] == 2


@pytest.mark.asyncio
async def test_unproven_product_guess_matching_pricelist_is_preserved(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    collector.save(context, {'draft_type': 'quotation', 'draft_id': 'quote_poisoned',
        'version': 1, 'content': 'Báo giá nháp', 'quotation_data': {'partner_id': 15,
            'company_id': 1, 'lines': [{}]}, 'quotation_hints': {
            'customer_query': 'Bệnh viện đa khoa TPHCM', 'product_query_1': 'bệnh viên',
            'pricelist_query': 'Giá cho KH bệnh viện (VND)', 'pricelist_lookup_pending': True}}, None)
    # Cache expiry removes any evidence that the original product was a classifier mistake.
    async def lookup(_context, _method, path, params):
        assert path == 'quotations/lookup' and params['kind'] == 'pricelists'
        return {'items': [{'id': 3, 'name': 'KH bệnh viện',
                           'display_name': 'KH bệnh viện (VND)'}], 'unique_query_match': True}
    monkeypatch.setattr(collector, 'request', lookup)
    reply = await collector.collect({'pricelist_query': 'Bệnh viện'}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['pricelist_id'] == 3
    assert draft['quotation_data']['lines'] == []
    assert draft['quotation_hints']['unclassified_product_candidate']['text'] == 'bệnh viên'
    assert 'Dòng 1' not in reply[0]
    assert 'theo mặc định Odoo' not in reply[0]
    assert 'Thông tin ‘bệnh viên’ trước đó chỉ là bảng giá, đúng không?' in reply[0]
    assert draft['quotation_data']['partner_id'] == 15
    from app.business.quote_local import interpret
    assert interpret('đúng', draft) == {'action': 'discard_price_candidate'}
    after = await collector.collect({'action': 'discard_price_candidate'}, context)
    assert 'Bạn muốn báo giá sản phẩm nào và số lượng bao nhiêu?' in after[0]
    assert workflow.session(context)['active_draft']['quotation_data']['lines'] == []


@pytest.mark.asyncio
async def test_only_proven_misclassified_empty_line_is_removed(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    collector.save(context, {'draft_type': 'quotation', 'draft_id': 'quote_proven',
        'version': 1, 'content': 'Báo giá nháp', 'quotation_data': {'partner_id': 15,
            'company_id': 1, 'lines': [{}]}, 'quotation_hints': {
            'product_query_1': 'bệnh viên', 'product_query_1_misclassified_pricelist': True,
            'pricelist_lookup_pending': True}}, None)
    async def lookup(_context, _method, path, params):
        assert path == 'quotations/lookup' and params['kind'] == 'pricelists'
        return {'items': [{'id': 3, 'name': 'KH bệnh viện'}], 'unique_query_match': True}
    monkeypatch.setattr(collector, 'request', lookup)
    await collector.collect({'pricelist_query': 'Bệnh viện'}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['lines'] == []
    assert 'product_query_1' not in draft['quotation_hints']
    assert 'unclassified_product_candidate' not in draft['quotation_hints']


@pytest.mark.asyncio
async def test_pricelist_recovery_preserves_verified_product_line(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    collector.save(context, {'draft_type': 'quotation', 'draft_id': 'quote_verified_product',
        'version': 1, 'content': 'Báo giá nháp', 'quotation_data': {'partner_id': 15,
            'company_id': 1, 'lines': [{'product_id': 22, 'quantity': 2}]}, 'quotation_hints': {
            'product_query_1': 'bệnh viên', 'product_query_1_misclassified_pricelist': True,
            'pricelist_lookup_pending': True}}, None)
    async def lookup(_context, _method, path, params):
        assert path == 'quotations/lookup' and params['kind'] == 'pricelists'
        return {'items': [{'id': 3, 'name': 'KH bệnh viện'}], 'unique_query_match': True}
    monkeypatch.setattr(collector, 'request', lookup)
    await collector.collect({'pricelist_query': 'Bệnh viện'}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]
    assert draft['quotation_hints']['product_query_1'] == 'bệnh viên'


@pytest.mark.asyncio
async def test_quarantine_only_suspect_line_and_keep_other_products(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    collector.save(context, {'draft_type': 'quotation', 'draft_id': 'quote_multiple',
        'version': 1, 'content': 'Báo giá nháp', 'quotation_data': {'partner_id': 15,
            'pricelist_id': 3, 'lines': [{}, {'product_id': 22, 'quantity': 2},
                                       {'product_id': 23, 'quantity': 4}]}, 'quotation_hints': {
            'product_query_1': 'bệnh viên', 'product_query_2': 'Bao tay',
            'product_query_3': 'Khẩu trang', 'pricelist_query': 'bệnh viện'}}, None)
    result = await collector.collect({'action': 'show'}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['lines'] == [
        {'product_id': 22, 'quantity': 2}, {'product_id': 23, 'quantity': 4}]
    assert draft['quotation_hints']['product_query_1'] == 'Bao tay'
    assert draft['quotation_hints']['product_query_2'] == 'Khẩu trang'
    assert 'bệnh viên' not in collector.quote_details(draft)
    assert 'Thông tin ‘bệnh viên’' in result[0]
    await collector.collect({'action': 'discard_price_candidate'}, context)
    assert workflow.session(context)['active_draft']['quotation_data']['lines'] == [
        {'product_id': 22, 'quantity': 2}, {'product_id': 23, 'quantity': 4}]


@pytest.mark.asyncio
async def test_candidate_rejected_as_price_restores_product_without_quantity(quote_runtime):
    context, _, _ = quote_runtime
    collector.save(context, {'draft_type': 'quotation', 'draft_id': 'quote_real_product',
        'version': 1, 'content': 'Báo giá nháp', 'quotation_data': {'partner_id': 15,
            'pricelist_id': 3, 'lines': [{}]}, 'quotation_hints': {
            'product_query_1': 'bệnh viện', 'pricelist_query': 'bệnh viện'}}, None)
    await collector.collect({'action': 'show'}, context)
    reply = await collector.collect({'action': 'restore_product_candidate'}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['lines'] == [{}]
    assert draft['quotation_hints']['product_query_1'] == 'bệnh viện'
    assert 'Số lượng: chưa nhập' in reply[0]
    assert 'theo mặc định Odoo' not in reply[0]


def test_price_answer_history_proves_fake_line_but_valid_line_stays():
    draft = {'quotation_data': {'partner_id': 15, 'pricelist_id': 3,
        'lines': [{}, {'product_id': 22, 'quantity': 2}]}, 'quotation_hints': {
        'pricelist_query': 'bệnh viện', 'product_query_1': 'bệnh viên',
        'product_query_2': 'Bao tay'}}
    history = [{'role': 'assistant', 'content': 'Bạn muốn dùng bảng giá nào?'},
               {'role': 'user', 'content': 'bệnh viện'}]
    assert collector.quarantine_price_candidate(draft, history)
    assert draft['quotation_data']['lines'] == [{'product_id': 22, 'quantity': 2}]
    assert draft['quotation_hints']['product_query_1'] == 'Bao tay'
    assert 'unclassified_product_candidate' not in draft['quotation_hints']


@pytest.mark.asyncio
async def test_pricelist_catalog_question_uses_odoo_rows_and_numbered_choice(quote_runtime, monkeypatch):
    context, _, _ = quote_runtime
    collector.save(context, {'draft_type': 'quotation', 'draft_id': 'quote_catalog',
        'version': 1, 'content': 'Báo giá nháp', 'quotation_data': {
            'partner_id': 15, 'company_id': 1, 'lines': []}, 'quotation_hints': {}}, None)
    queries = []
    async def lookup(_context, _method, path, params):
        assert path == 'quotations/lookup'
        queries.append(params)
        return {'items': [{'id': 31, 'display_name': 'KH thương mại (VND)'},
                          {'id': 3, 'display_name': 'KH bệnh viện (VND)'}]}
    monkeypatch.setattr(collector, 'request', lookup)
    prompt = await collector.ask_pricelist(context, workflow.session(context)['active_draft'])
    assert '1. KH thương mại (VND)' in prompt and '2. KH bệnh viện (VND)' in prompt
    assert queries == [{'kind': 'pricelists', 'query': '', 'company_id': 1}]
    selected = await collector.collect({'selected_id': 3}, context)
    draft = workflow.session(context)['active_draft']
    assert draft['quotation_data']['pricelist_id'] == 3
    assert draft['quotation_hints']['pricelist_label'] == 'KH bệnh viện (VND)'
