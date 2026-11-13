"""Stateful quote regressions. Provider and Odoo traffic are replaced at boundaries."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.business import quote_collector as collector, workflow
from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.services import natural_pipeline as natural_module
from app.services.confirmation_service import ConfirmationService
from app.services.conversation_history_store import ConversationHistoryStore


def test_tao_lich_hop_is_recognized_as_quotation_chatter():
    from app.business.operations import looks_like_quote_chatter
    assert looks_like_quote_chatter('tạo lịch họp ngày 12/11/2026 12:00 A.M ở s00089', {})


def test_quotation_requires_pricelist_before_confirmation():
    draft = {'quotation_data': {'partner_id': 1, 'lines': [{'product_id': 2}]}, 'quotation_hints': {}}
    assert 'cần bổ sung: bảng giá' in collector.state_prompt(draft).lower()


def test_pricelist_selection_is_saved_as_a_named_reference():
    draft = {'quotation_data': {'lines': []}, 'quotation_hints': {}}
    collector.save_reference(draft, 'pricelists', 1, {'id': 7, 'display_name': 'Giá thương mại'})
    assert draft['quotation_data']['pricelist_id'] == 7
    assert 'Giá thương mại' in collector.quote_details(draft)


@pytest.mark.asyncio
async def test_request_for_boss_uses_last_created_quotation(quote_runtime, monkeypatch):
    from app.business import quote_status

    context, _, confirmations = quote_runtime
    await collector.collect({'action': 'start', 'quantity': 1}, context)
    draft = workflow.session(context)['active_draft']
    cid, _ = confirmations.create_confirmation(user_id=1, chat_id=7, telegram_binding_id='fixture-binding',
        action='sales.quotation.create', arguments={}, preview_text='preview', owner_key=context.owner_key,
        draft_id=draft['draft_id'], draft_version=draft['version'], telegram_user_id=7)
    assert confirmations.claim_and_validate(cid, 1, 7, 'fixture-binding')[0]
    monkeypatch.setattr(quote_status, 'request', AsyncMock(return_value={
        'status': 'completed', 'result': {'id': 88, 'name': 'S00089', 'company_id': [1, 'Company']},
    }))
    from app.core import config
    settings = config.get_settings()
    settings.ENABLE_QUOTATION_APPROVAL_FLOW = True
    pipeline = natural_module.NaturalPipeline()
    result = await pipeline.analyze_input('gửi yêu cầu xác nhận cho sếp', context, workflow.session(context))
    assert result.tool_name == 'business.quotation_flow'
    assert result.parameters['quotation_name'] == 'S00089'
    assert result.parameters['company_id'] == 1
    assert result.parameters['action'] == 'request_approval'


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
                                        'product_query': 'Product', 'quantity': 2}, context)
    assert 'bảng giá' in messages[0]
    messages = await collector.collect({'selected_id': 33}, context)
    preview = messages[0]
    assert 'Customer' in preview and 'Product' in preview
    saved = confirmations.get_confirmation(preview.confirmation_id)
    assert json.loads(saved['arguments_json']) == {'partner_id': 11, 'pricelist_id': 33, 'lines': [{'product_id': 22, 'quantity': 2}]}
    assert (await workflow.prepare(context))[0].confirmation_id == preview.confirmation_id


@pytest.mark.asyncio
@pytest.mark.parametrize('change_quantity', [False, True])
async def test_confirmed_quote_resumes_only_for_missing_fields(quote_runtime, monkeypatch, change_quantity):
    from app.business import quote_status
    from app.services import unified_executor as executor_module

    context, store, confirmations = quote_runtime
    await collector.collect({'action': 'start', 'customer_query': 'Customer', 'product_query': 'Product', 'quantity': 2}, context)
    await collector.collect({'selected_id': 11}, context)
    await collector.collect({'selected_id': 22}, context)
    preview = (await collector.collect({'selected_id': 33}, context))[0]
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
        return {'items': [{'id': 31, 'display_name': 'Giá thương mại'}, {'id': 32, 'display_name': 'Giá bệnh viện'}]}
    monkeypatch.setattr(collector, 'request', lookup)
    monkeypatch.setattr(workflow, 'prepare', AsyncMock(return_value=['preview']))
    result = await collector.collect({'action': 'start', 'customer_query': 'Customer', 'product_query': 'Product'}, context)
    assert 'Giá thương mại' in result[0] and 'Giá bệnh viện' in result[0]
    assert not workflow.session(context)['active_draft']['quotation_data'].get('pricelist_id')
    result = await collector.collect({'selected_id': 32}, context)
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
