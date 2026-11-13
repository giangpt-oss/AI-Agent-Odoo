import time
from unittest.mock import AsyncMock

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.domain.tool_registry import global_tool_registry
from app.services.natural_pipeline import natural_pipeline


def context():
    return GatewayUserContext(user_id=1, tenant="https://odoo.invalid", database="test", odoo_uid=7, telegram_user_id=7, chat_id=7, telegram_binding_id="binding", permissions={"workspace.drive.read", "workspace.drive.write"})


def test_workspace_help_has_no_command_templates():
    from app.workspace.agent import catalog_messages
    text = '\n'.join(catalog_messages({}))
    assert '/workspace' not in text
    assert 'parameters' not in text and 'files.list' not in text


def test_workspace_tools_are_available_and_filter_by_scope_permissions():
    tools = global_tool_registry.list_tools(["workspace.drive.read"])
    names = {tool.name for tool in tools}
    assert "workspace.catalog" in names
    assert "workspace.drive.read" in names
    assert "workspace.drive.write" not in names
    assert "workspace.sheets.read" not in names


@pytest.mark.asyncio
async def test_workspace_commands_use_catalog_without_llm(monkeypatch):
    llm = AsyncMock(side_effect=AssertionError("No LLM for deterministic command"))
    monkeypatch.setattr(natural_pipeline, "_call_gemini_raw", llm)
    intent = await natural_pipeline.analyze_input('/workspace drive files.list {"parameters":{"q":"trashed=false"}}', context(), {})
    assert intent.intent_type == IntentType.READ
    assert intent.tool_name == "workspace.drive.read"
    assert intent.parameters["method"] == "files.list"
    write = await natural_pipeline.analyze_input('/workspace drive files.create {"body":{"name":"Báo cáo"}}', context(), {})
    assert write.intent_type == IntentType.PREPARE_WRITE
    assert write.tool_name == "workspace.drive.write"
    llm.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_invalid_json_is_not_dispatched():
    intent = await natural_pipeline.analyze_input('/workspace drive files.create not-json', context(), {})
    assert intent.intent_type in {IntentType.CLARIFY, IntentType.REPLY}
    text = intent.reply_text or intent.clarification_prompt
    assert 'tác vụ' in text and '/workspace' not in text and 'JSON' not in text
    assert intent.tool_name is None and not intent.parameters


@pytest.mark.asyncio
async def test_workspace_catalog_command_keeps_method_name():
    intent = await natural_pipeline.analyze_input('/workspace catalog docs documents.create', context(), {})
    assert intent.parameters == {"service": "docs", "method": "documents.create"}


@pytest.mark.asyncio
async def test_workspace_read_uses_gateway_and_checks_kind(monkeypatch):
    from app.services.gateway_client import gateway_client
    from app.services.unified_executor import unified_executor
    query = AsyncMock(return_value={"status": "OK", "data": {"files": [{"id": "f1", "name": "Report"}]}})
    monkeypatch.setattr(gateway_client, "workspace_query", query, raising=False)
    result = await unified_executor.execute(IntentAnalysisResult(intent_type="read", tool_name="workspace.drive.read", parameters={"method": "files.list"}), context(), 7, 7, {}, time.time() + 18, {})
    assert "Report" in " ".join(result)
    assert query.await_count == 1
    result = await unified_executor.execute(IntentAnalysisResult(intent_type="read", tool_name="workspace.drive.read", parameters={"method": "files.create"}), context(), 7, 7, {}, time.time() + 18, {})
    assert query.await_count == 1


@pytest.mark.asyncio
async def test_workspace_read_accepts_background_job_with_bound_identity(monkeypatch):
    from app.core.config import get_settings
    from app.services.gateway_client import gateway_client
    from app.services.turn_trace import request_id
    from app.services.unified_executor import unified_executor

    monkeypatch.setattr(get_settings(), "WORKSPACE_BACKGROUND_READS", True)
    queue = AsyncMock(return_value={"status": "queued", "job_id": "job-bound"})
    monkeypatch.setattr(gateway_client, "create_workspace_job", queue)
    token = request_id.set("workspace-background-test")
    try:
        intent = IntentAnalysisResult(intent_type="read", tool_name="workspace.drive.read",
                                      parameters={"method": "files.list"})
        messages = await unified_executor.execute(intent, context(), 7, 7, {}, time.time() + 18, {})
    finally:
        request_id.reset(token)
    assert "job-bound" in messages[0]
    assert queue.await_args.args == (1, "workspace-background-test", "drive", "files.list")
    assert queue.await_args.kwargs["telegram_binding_id"] == "binding"


@pytest.mark.asyncio
async def test_write_needs_preview_then_confirmation_and_freezes_arguments(monkeypatch, tmp_path):
    from app.services.confirmation_service import ConfirmationService
    from app.services.gateway_client import gateway_client
    from app.services.unified_executor import unified_executor
    import app.services.unified_executor as executor_module
    service = ConfirmationService(tmp_path / "confirm.db")
    monkeypatch.setattr(executor_module, "confirmation_service", service)
    prepare = AsyncMock(return_value={"status": "OK", "arguments": {"method": "files.create", "body": {"name": "Report"}, "_ticket": "test-signed-preview"}})
    execute = AsyncMock(return_value={"status": "completed", "result": {"id": "new-file", "name": "Report"}})
    monkeypatch.setattr(gateway_client, "workspace_prepare", prepare, raising=False)
    monkeypatch.setattr(gateway_client, "execute_action", execute)
    intent = IntentAnalysisResult(intent_type="prepare_write", tool_name="workspace.drive.write", parameters={"method": "files.create", "body": {"name": "Report"}})
    preview = await unified_executor.execute(intent, context(), 7, 7, {}, time.time() + 18, {})
    assert "Report" in " ".join(preview)
    execute.assert_not_called()
    pending = service.list_pending_confirmations(1, 7, "binding")
    assert len(pending) == 1
    cid = pending[0]["confirmation_id"]
    confirmed = await unified_executor.execute(IntentAnalysisResult(intent_type="action_confirm", confirmation_id=cid), context(), 7, 7, {}, time.time() + 18, {})
    assert "new-file" in " ".join(confirmed)
    assert execute.await_count == 1
    assert execute.call_args.kwargs["arguments"]["_ticket"] == "test-signed-preview"
    await unified_executor.execute(IntentAnalysisResult(intent_type="action_confirm", confirmation_id=cid), context(), 7, 7, {}, time.time() + 18, {})
    assert execute.await_count == 1
