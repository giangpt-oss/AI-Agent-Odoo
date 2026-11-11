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
        return {'items': [{'id': 11, 'display_name': 'Customer'}]} if params['kind'] == 'partners' else {'items': [{'id': 22, 'display_name': 'Product', 'list_price': 100}]}

    monkeypatch.setattr(collector, 'request', rpc)
    return context, store, confirmations


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
