from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.business import workflow


@pytest.mark.asyncio
@pytest.mark.parametrize(('tool', 'parameters'), [
    ('reports.create', {'report_type': 'sales', 'date_from': '2026-10-01', 'date_to': '2026-10-31', 'metric': 'profit'}),
    ('reports.file', {'report_id': 'rpt_fixture', 'partner_id': 99}),
])
async def test_unavailable_requirement_never_dispatches_broader_report(monkeypatch, tool, parameters):
    monkeypatch.setattr('app.core.config.get_settings', lambda: SimpleNamespace(ENABLE_REPORTING=True, ENABLE_QUOTATIONS=True, ENABLE_BUSINESS_ACTIONS=False))
    request = AsyncMock(return_value={'status': 'queued', 'report_id': 'rpt_fixture'})
    monkeypatch.setattr(workflow, 'request', request)
    monkeypatch.setattr(workflow, 'session', lambda context: {'last_read': {}})
    monkeypatch.setattr(workflow, 'remember', lambda *args: None)
    messages = await workflow.read(tool, parameters, SimpleNamespace(timezone='Asia/Bangkok'))
    request.assert_not_awaited()
    assert 'chưa hỗ trợ' in '\n'.join(messages)
