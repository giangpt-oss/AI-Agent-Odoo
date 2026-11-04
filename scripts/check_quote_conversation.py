"""Opt-in live intent-model evaluation with isolated state and a fake Odoo boundary.

Run: python scripts/check_quote_conversation.py --live
Calls only the configured AI model. Never contacts Odoo/Telegram or creates ERP data.
"""
import argparse
import asyncio
import copy
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.business import quote_collector, workflow  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.domain.gateway_context import GatewayUserContext  # noqa: E402
from app.domain.intent_schema import IntentType  # noqa: E402
from app.services.confirmation_service import ConfirmationService  # noqa: E402
from app.services.conversation_history_store import ConversationHistoryStore  # noqa: E402
from app.services.natural_pipeline import NaturalPipeline  # noqa: E402


async def evaluate(directory, min_interval=13.0, local_only=False):
    store = ConversationHistoryStore(str(Path(directory) / 'conversation.db'))
    confirmations = ConfirmationService(Path(directory) / 'confirmations.db')
    context = GatewayUserContext(user_id=1, tenant='https://odoo.invalid', database='fixture', odoo_uid=1,
        chat_id=7, telegram_user_id=7, telegram_binding_id='fixture-binding', permissions={'sales.create'})
    jobs = {}
    calls = []

    async def gateway(ctx, method, path, data=None):
        calls.append((method, path))
        if method == 'GET' and path == 'quotations/lookup':
            item = {'id': 11, 'display_name': 'Công ty Demo'} if data['kind'] == 'partners' else {'id': 22, 'display_name': 'Bao tay Demo', 'list_price': 100000}
            return {'items': [item]}
        if method == 'POST' and path == 'quotations/prepare-jobs':
            jobs['fixture'] = copy.deepcopy(data['payload'])
            return {'status': 'queued', 'job_id': 'fixture'}
        if method == 'GET' and path == 'quotations/prepare-jobs/fixture':
            payload = jobs['fixture']
            line = payload['lines'][0]
            assert payload['partner_id'] == 11 and line['product_id'] == 22
            assert line['quantity'] == 1 and line['price_unit'] == 200000 and line['price_confirmed'] is True
            assert line['tax_ids'] == [] and line['customer_lead'] == 10
            return {'status': 'completed', 'result': {'status': 'ready', 'payload': payload,
                'preview': {'company_id': 1, 'customer': 'Công ty Demo', 'lines': [dict(line, product='Bao tay Demo')]}}}
        raise AssertionError('Forbidden provider call: ' + method + ' ' + path)

    turns = [
        'Tạo báo giá mới', 'Khách là Công ty Demo', 'Chọn khách hàng ID 11', 'Sản phẩm Bao tay Demo',
        'Chọn sản phẩm ID 22', 'Số lượng là một', 'Không thêm dòng nào nữa', 'Đơn giá 200000 đồng',
        'Không áp dụng thuế', 'Giao sau 10 ngày', 'Cho xem bản báo giá trước', 'Báo giá kiểm tra xong chưa?',
        'Tôi xác nhận tạo báo giá theo bản vừa xem',
    ]
    observations = []
    pipeline = NaturalPipeline()
    original_call = pipeline._call_gemini_raw
    model_calls = 0

    async def observed_call(prompt, response_schema=None):
        nonlocal model_calls
        model_calls += 1
        if local_only:
            raise AssertionError('Local evaluation must not depend on the AI provider')
        raw = await original_call(prompt, response_schema=response_schema)
        # This evaluation contains synthetic fixtures only; retain model evidence.
        print(json.dumps({'model_response': raw}, ensure_ascii=False), flush=True)
        return raw

    pipeline._call_gemini_raw = observed_call
    with patch.object(quote_collector, 'store', store), patch.object(workflow, 'store', store), \
         patch.object(quote_collector, 'confirmation_service', confirmations), patch.object(workflow, 'confirmation_service', confirmations), \
         patch.object(quote_collector, 'request', gateway), patch.object(workflow, 'request', gateway):
        last_request = None
        for index, text in enumerate(turns, 1):
            if last_request is not None:
                await asyncio.sleep(max(0.0, min_interval - (time.monotonic() - last_request)))
            started = time.monotonic()
            last_request = started
            session = workflow.session(context)
            intent = await pipeline.analyze_input(text, context, session)
            if intent.intent_type == IntentType.ACTION_CONFIRM:
                conf = confirmations.get_confirmation(intent.confirmation_id)
                assert conf and conf['status'] == 'pending' and conf['draft_id'] == session['active_draft']['draft_id']
                messages = ['Xác nhận được gắn đúng bản xem trước; kiểm tra dừng trước ghi Odoo.']
            elif intent.intent_type == IntentType.READ and intent.tool_name == 'sales.quotation.collect':
                messages = await quote_collector.collect(intent.parameters, context)
            elif intent.tool_name == 'sales.quotation.preview' and intent.intent_type == IntentType.PREPARE_WRITE:
                messages = await workflow.prepare(context)
            elif intent.intent_type == IntentType.READ and intent.tool_name and intent.tool_name.startswith('sales.quotation.'):
                messages = await workflow.read(intent.tool_name, intent.parameters, context)
            else:
                raise AssertionError(f'Turn {index} did not enter the durable quote flow: {intent.model_dump()}')
            store.append_turn(context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, 'user', text, database=context.database)
            store.append_turn(context.tenant, context.user_id, context.chat_id, context.telegram_binding_id, 'assistant', '\n'.join(messages), database=context.database)
            observation = {'turn': index, 'text': text, 'tool': intent.tool_name, 'parameters': intent.parameters,
                           'elapsed_seconds': round(time.monotonic() - started, 2), 'reply': messages}
            observations.append(observation)
            print(json.dumps(observation, ensure_ascii=False), flush=True)
        draft = workflow.session(context)['active_draft']
        assert draft['quotation_data']['lines'][0]['customer_lead'] == 10
        assert len(confirmations.list_pending_confirmations(user_id=1, chat_id=7, telegram_binding_id='fixture-binding')) == 1
        assert not any('/actions' in path for _, path in calls)
    return {'model': get_settings().INTENT_LLM_MODEL or get_settings().DEFAULT_LLM_MODEL,
            'passed': True, 'model_calls': model_calls, 'turns': observations, 'odoo_calls': 0, 'telegram_messages': 0, 'writes_to_odoo': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize calls to the configured intent model using synthetic data.')
    parser.add_argument('--local', action='store_true', help='Require the full clear-fact conversation to work without any model calls.')
    parser.add_argument('--min-interval', type=float, default=13.0, help='Minimum seconds between model turns, respecting low-RPM API quotas.')
    args = parser.parse_args()
    if not args.live and not args.local:
        parser.error('Pass --live or --local. Odoo/Telegram remain replaced.')
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        result = asyncio.run(evaluate(directory, 0 if args.local else max(0.0, args.min_interval), args.local))
    output = Path(__file__).resolve().parents[1] / 'data' / 'quote-eval-latest.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"PASS: 13-turn natural conversation; model calls={result['model_calls']}; bound confirmation; no Odoo writes or Telegram messages.")


if __name__ == '__main__':
    main()
