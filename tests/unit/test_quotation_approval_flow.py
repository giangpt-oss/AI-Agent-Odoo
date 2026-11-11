from types import SimpleNamespace

import pytest

from app.business.operations import allowed_action, interpret_quotation_flow
from app.business.quote_status import result_message


@pytest.mark.parametrize('text,action', [
    ('Xin duyệt báo giá S00086', 'request_approval'),
    ('Gửi cấp trên duyệt báo giá S00086', 'request_approval'),
    ('Duyệt báo giá S00086', 'grant_approval'),
    ('Từ chối duyệt báo giá S00086', 'reject_approval'),
    ('Gửi báo giá S00086 cho khách', 'send'),
    ('Khách đã đồng ý S00086, chuyển thành đơn bán', 'confirm'),
])
def test_natural_flow_uses_distinct_actions(text, action):
    result = interpret_quotation_flow(text)
    assert result['action'] == action
    assert result['quotation_name'] == 'S00086'
    assert result['options'] == ({'customer_accepted': True} if action == 'confirm' else {})


@pytest.mark.parametrize('text', [
    'Khi khách đồng ý S00086 thì chuyển thành đơn bán',
    'Khách chưa đồng ý S00086, đừng chuyển thành đơn bán',
    'Không gửi báo giá S00086 cho khách',
    'Xin duyệt S00086 và S00087',
    'S00086 đang chờ duyệt à?',
    'Khách đã không đồng ý S00086, chuyển thành đơn bán',
    'Nếu khách đã đồng ý S00086 thì chuyển thành đơn bán',
    'Khách đã đồng ý S00086 chưa, chuyển thành đơn bán được không?',
])
def test_ambiguous_or_future_decision_never_implies_acceptance(text):
    assert interpret_quotation_flow(text) is None


def test_flow_flag_does_not_enable_unrelated_business_writes():
    settings = SimpleNamespace(ENABLE_BUSINESS_ACTIONS=False, ENABLE_QUOTATION_APPROVAL_FLOW=True)
    assert allowed_action(settings, 'business.sales.request_approval')
    assert not allowed_action(settings, 'business.sales.delete')
    assert not allowed_action(settings, 'business.purchases.confirm')


def test_tool_catalog_does_not_advertise_disabled_writes(monkeypatch):
    from app.business.operations import QUOTE_FLOW_ACTIONS, register
    from app.core import config
    monkeypatch.setattr(config, 'get_settings', lambda: SimpleNamespace(
        ENABLE_BUSINESS_ACTIONS=False, ENABLE_QUOTATION_APPROVAL_FLOW=True))
    registered = {}
    register(SimpleNamespace(register=lambda tool: registered.update({tool.name: tool})), SimpleNamespace)
    actions = registered['business.draft'].parameter_schema['properties']['business_action']['enum']
    assert set(actions) == QUOTE_FLOW_ACTIONS


@pytest.mark.asyncio
async def test_uncertain_acceptance_is_clarified_before_model_or_tools(monkeypatch):
    from app.services import natural_pipeline as module
    from app.domain.intent_schema import IntentType
    monkeypatch.setattr(module, 'get_settings', lambda: SimpleNamespace(ENABLE_QUOTATION_APPROVAL_FLOW=True))
    pipeline = module.NaturalPipeline()
    result = await pipeline.analyze_input('Nếu khách đã đồng ý S00086 thì chuyển thành đơn bán', None, {})
    assert result.intent_type == IntentType.CLARIFY
    assert not result.tool_name


def test_quote_status_separates_native_state_and_internal_approval():
    text = result_message({'name': 'S00086', 'state': 'draft',
        'approval': {'status': 'pending', 'approvers': ['Nguyễn Tiến Phương']}})
    assert 'Báo giá nháp' in text and 'Chờ cấp trên duyệt' in text
    assert 'Nguyễn Tiến Phương' in text
    assert 'khách chấp thuận' in text


def test_flow_confirmation_is_plain_text_and_has_long_enough_execution_lease(tmp_path):
    import time
    from app.services.confirmation_service import ConfirmationService
    service = ConfirmationService(tmp_path / 'confirmations.db')
    cid, message = service.create_confirmation(user_id=1, chat_id=7, telegram_binding_id='binding',
        action='business.sales.request_approval', arguments={'prepared_id': 'example'},
        preview_text='Xin duyệt báo giá S00086', owner_key='owner', telegram_user_id=99)
    assert '**' not in message and '_(' not in message
    assert '5 phút' in message
    ok, _, data = service.claim_and_validate(cid, 1, 7, 'binding', owner_key='owner', telegram_user_id=99)
    assert ok
    assert data['lease_expires_at'] > time.time() + 60
