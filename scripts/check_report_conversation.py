"""Offline report conversation audit. No AI/Odoo/Telegram requests are made."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.business import workflow  # noqa: E402
from app.domain.gateway_context import GatewayUserContext  # noqa: E402
from app.services.conversation_history_store import ConversationHistoryStore  # noqa: E402
from app.services.natural_pipeline import NaturalPipeline  # noqa: E402


async def audit(directory):
    store = ConversationHistoryStore(str(Path(directory) / 'reports.db'))
    context = GatewayUserContext(user_id=1, tenant='https://fixture.invalid', database='fixture', odoo_uid=1,
        chat_id=7, telegram_user_id=7, telegram_binding_id='fixture')
    pipeline = NaturalPipeline()
    unavailable = AsyncMock(side_effect=RuntimeError('503 UNAVAILABLE synthetic provider boundary'))
    requests, turns = [], []

    async def gateway(ctx, method, path, data=None):
        requests.append({'method': method, 'path': path, 'parameters': data})
        if path == 'reports' and method == 'POST':
            return {'status': 'queued', 'report_id': 'rpt_fixture'}
        if path == 'reports/rpt_fixture' and method == 'GET':
            return {'status': 'completed', 'delivery_status': 'delivered'}
        if path == 'reports/rpt_fixture/deliver' and method == 'POST':
            return {'status': 'queued', 'message': 'Đã nhận yêu cầu đổi định dạng của bộ dữ liệu hiện có.'}
        raise AssertionError('Unexpected provider boundary')

    with patch.object(workflow, 'store', store), patch.object(workflow, 'request', gateway), patch.object(pipeline, '_call_gemini_raw', unavailable):
        for text in ('Báo cáo bán hàng', 'từ 1/10/2026 đến 31/10/2026', 'báo cáo xong chưa?', 'xuất lại bằng Word', 'Báo cáo tồn kho hiện tại'):
            intent = await pipeline.analyze_input(text, context, workflow.session(context))
            assert intent.tool_name and intent.tool_name.startswith('reports.')
            messages = await workflow.read(intent.tool_name, intent.parameters, context)
            turns.append({'text': text, 'tool': intent.tool_name, 'parameters': intent.parameters, 'reply': messages})
        assert len(requests) == 4
        assert requests[0]['parameters']['date_from'] == '2026-10-01'
        assert requests[0]['parameters']['date_to'] == '2026-10-31'
        assert requests[2]['parameters']['output_format'] == 'docx'
        assert requests[3]['parameters']['report_type'] == 'inventory'
        assert 'date_from' not in requests[3]['parameters'] and 'date_to' not in requests[3]['parameters']
        assert unavailable.await_count == 0
        cases = []
        # Holdout wording: record dependency, do not tune the parser against it.
        for text in ('Tôi muốn xem tình hình bán hàng tháng 10 năm 2026',
                     'Thống kê các báo giá của tôi tháng trước',
                     'Báo cáo bán hàng tháng 10/2026 theo từng sản phẩm',
                     'Báo cáo tồn kho ngày 31/10/2026',
                     'Báo cáo lợi nhuận của từng khách hàng',
                     'Lập báo cáo từ những số liệu tôi vừa nhập'):
            count = unavailable.await_count
            intent = await pipeline.analyze_input(text, context, {})
            cases.append({'text': text, 'requires_model': unavailable.await_count > count,
                'intent': intent.intent_type.value, 'tool': intent.tool_name,
                'provider_requests_dispatched': 0})
    return {'stateful_flow': 'passed', 'boundary': 'mock_gateway_no_network', 'turns': turns,
        'requests': requests, 'holdout_routing': cases, 'live_model_behavior': 'not_tested', 'odoo_writes': 0}


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        result = asyncio.run(audit(directory))
    output = Path(__file__).resolve().parents[1] / 'data' / 'report-conversation-audit.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'stateful_flow': result['stateful_flow'], 'turns': len(result['turns']),
        'holdout_model_dependent': sum(row['requires_model'] for row in result['holdout_routing']),
        'holdout_total': len(result['holdout_routing']), 'odoo_writes': 0}))


if __name__ == '__main__':
    main()
