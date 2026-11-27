import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.domain.intent_schema import IntentAnalysisResult
from app.services import workspace_tool_plan


def step(id, *, depends_on=(), parameters=None):
    return {"id": id, "tool_name": "workspace.drive.read",
            "depends_on": depends_on, "parameters": parameters or {}}


def plan(*steps):
    return IntentAnalysisResult(intent_type="tool_plan", steps=list(steps))


@pytest.mark.parametrize("steps", [
    [step("a", depends_on=["b"]), step("b", depends_on=["a"])],
    [step("a", depends_on=["missing"])],
    [step("a"), step("a")],
    [step("a", parameters={"fileId": {"$step": "b", "path": ["id"]}}), step("b")],
])
def test_invalid_graph_rejected(steps):
    with pytest.raises(ValidationError):
        plan(*steps)


@pytest.mark.asyncio
async def test_independent_steps_overlap_and_dependent_step_gets_data():
    release = asyncio.Event()
    started = set()

    async def invoke(node, params, timeout):
        started.add(node.id)
        if node.id in {"a", "b"}:
            if len(started) == 2:
                release.set()
            await release.wait()
        else:
            assert params == {"fileId": "file-a"}
        return workspace_tool_plan.StepResult("completed", data={"id": f"file-{node.id}"})

    result = await workspace_tool_plan.execute_plan(plan(
        step("a"), step("b"),
        step("c", depends_on=["a"], parameters={"fileId": {"$step": "a", "path": ["id"]}}),
    ).steps, invoke, time.time() + 120)
    assert [item.status for item in result.values()] == ["completed"] * 3


@pytest.mark.asyncio
async def test_timeout_preserves_partial_results_and_skips_dependent_write(monkeypatch):
    monkeypatch.setattr(workspace_tool_plan, "STEP_TIMEOUT", 0.03)
    invoked = []

    async def invoke(node, params, timeout):
        invoked.append(node.id)
        if node.id == "slow":
            await asyncio.sleep(1)
        return workspace_tool_plan.StepResult("completed")

    write = {**step("write", depends_on=["slow"]), "tool_name": "workspace.drive.write"}
    result = await workspace_tool_plan.execute_plan(
        plan(step("slow"), step("independent"), write).steps, invoke, time.time() + 120,
    )
    assert {key: item.status for key, item in result.items()} == {
        "slow": "timeout", "independent": "completed", "write": "skipped",
    }
    assert invoked == ["slow", "independent"]


def test_routing_limits_schemas_and_prioritizes_requested_services():
    tools = [SimpleNamespace(name=f"workspace.{service}.{action}", category="workspace")
             for service in ("drive", "calendar", "gmail", "docs", "sheets", "slides")
             for action in ("read", "write")]
    chosen = workspace_tool_plan.select_tools("Đọc Google Drive và Calendar", tools)
    assert 3 <= len(chosen) <= 6
    assert {item.name for item in chosen} >= {"workspace.drive.read", "workspace.calendar.read"}


@pytest.mark.asyncio
async def test_eight_independent_reads_peak_at_four_calls():
    release = asyncio.Event()
    full = asyncio.Event()
    active = peak = 0

    async def invoke(node, params, timeout):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 4:
            full.set()
        try:
            await release.wait()
            return workspace_tool_plan.StepResult("completed")
        finally:
            active -= 1

    steps = plan(*(step(f"read{index}") for index in range(8))).steps
    task = asyncio.create_task(workspace_tool_plan.execute_plan(steps, invoke, time.time() + 120))
    try:
        await asyncio.wait_for(full.wait(), timeout=2)
    finally:
        release.set()
        result = await task
    assert peak == 4
    assert len(result) == 8 and all(item.status == "completed" for item in result.values())


def test_calendar_summary_uses_event_fields_and_reports_incomplete_page():
    messages = workspace_tool_plan.summarize_calendar_page({
        "items": [{"summary": "Kiểm kê", "start": {"date": "2026-11-18"},
                   "description": "Internal details"}],
        "nextPageToken": "second-page",
    })
    assert "2026-11-18 · Kiểm kê" in messages[1]
    assert "Internal details" not in "\n".join(messages)
    assert "một phần" in messages[-1]


@pytest.mark.asyncio
async def test_telegram_document_plan_is_queued_with_document_deadline(monkeypatch):
    from app.core.config import get_settings
    from app.domain.gateway_context import GatewayUserContext
    from app.services.turn_trace import request_id
    from app.services.unified_executor import UnifiedExecutor, gateway_client

    monkeypatch.setattr(get_settings(), "WORKSPACE_BACKGROUND_READS", True)
    enqueue = AsyncMock(return_value={"job_id": "job_plan"})
    monkeypatch.setattr(gateway_client, "create_workspace_job", enqueue)
    context = GatewayUserContext(
        user_id=1, telegram_user_id=42, chat_id=43, telegram_binding_id="bind",
        permissions={"workspace.drive.read"},
    )
    token = request_id.set("turn-plan")
    try:
        messages = await UnifiedExecutor().execute(
            plan(step("drive")), context, 43, 42, {}, time.time() + 30, {},
        )
    finally:
        request_id.reset(token)
    assert "job_plan" in messages[0]
    assert enqueue.await_args.args == (1, "turn-plan", "workspace", "plan.read")
    assert enqueue.await_args.kwargs["deadline_seconds"] == 300


@pytest.mark.asyncio
async def test_read_plan_endpoint_checks_link_and_returns_step_result(monkeypatch):
    from fastapi import HTTPException

    from app.api.v1.internal import WorkspaceReadPlanPayload, run_workspace_read_plan
    from app.services.gateway_client import gateway_client

    identity = AsyncMock(return_value={
        "user_id": 1, "telegram_user_id": 42, "chat_id": 43, "telegram_binding_id": "bind",
        "permissions": ["workspace.drive.read"],
    })
    monkeypatch.setattr(gateway_client, "resolve_identity", identity)
    link = AsyncMock(return_value=False)
    monkeypatch.setattr(gateway_client, "drive_link_is_current", link)
    query = AsyncMock(return_value={"status": "OK", "data": {"files": []}})
    monkeypatch.setattr(gateway_client, "workspace_query", query)
    payload = WorkspaceReadPlanPayload(
        user_id=1, telegram_user_id=42, chat_id=43, binding_id="bind",
        link={"google_sub": "sub", "link_version": 2}, remaining_seconds=120,
        steps=[step("a", parameters={"method": "files.list"})],
    )
    with pytest.raises(HTTPException) as denied:
        await run_workspace_read_plan(payload)
    assert denied.value.status_code == 403
    query.assert_not_awaited()

    link.return_value = True
    result = await run_workspace_read_plan(payload)
    assert result["steps"][0]["status"] == "completed"
    assert result["partial"] is False
    query.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2])
async def test_draft_preview_requires_unambiguous_odoo_partner(monkeypatch, count):
    from app.domain.gateway_context import GatewayUserContext
    from app.services.unified_executor import UnifiedExecutor, gateway_client

    context = GatewayUserContext(
        user_id=1, tenant="test", database="odoo", odoo_uid=1,
        permissions={"workspace.gmail.write"},
    )
    lookup = AsyncMock(return_value={
        "status": "OK",
        "items": [{"id": index, "email": f"partner{index}@example.test"} for index in range(count)],
        "total_count": count,
    })
    monkeypatch.setattr(gateway_client, "query_odoo", lookup)
    executor = UnifiedExecutor()
    preview = AsyncMock(return_value=["Bản xem trước"])
    monkeypatch.setattr(executor, "_handle_prepare_write", preview)
    result = await executor.execute(plan(
        {"id": "partner", "tool_name": "odoo.partners.search", "parameters": {"query": "Acme"}},
        {"id": "draft", "tool_name": "workspace.gmail.write", "depends_on": ["partner"],
         "parameters": {"method": "users.drafts.create", "mail": {
             "to": {"$step": "partner", "path": ["email"]}, "subject": "Hello", "text": "Draft",
         }}},
    ), context, 1, 1, {}, time.time() + 30, {})
    lookup.assert_awaited_once()
    if count == 1:
        preview.assert_awaited_once()
        assert preview.await_args.args[0].parameters["mail"]["to"] == "partner0@example.test"
        assert "Bản xem trước" in result[-1]
    else:
        preview.assert_not_awaited()
        assert "nhiều đối tác" in result[0]
        assert "phụ thuộc" in result[-1]

def test_purchase_requests_include_business_tools_and_respect_read_only():
    from types import SimpleNamespace

    from app.services.workspace_tool_plan import select_tools

    names = [
        "odoo.purchases.search", "business.capabilities", "business.lookup",
        "business.draft", "business.preview", "knowledge.query",
        "workspace.drive.read", "workspace.calendar.read",
    ]
    tools = [
        SimpleNamespace(name=name, action_type="write" if name == "business.preview" else "read")
        for name in names
    ]
    for text in ("tạo đơn mua hàng", "tao don mua hang", "xác nhận RFQ"):
        selected = {tool.name for tool in select_tools(text, tools)}
        assert {"business.lookup", "business.draft", "business.preview"} <= selected
    selected = {tool.name for tool in select_tools("số lượng 3", tools, domains=["purchases"], read_only=True)}
    assert "business.draft" in selected
    assert "business.preview" not in selected
