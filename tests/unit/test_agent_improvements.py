# -*- coding: utf-8 -*-
"""Unit tests for LLM failover, last_read TTL, turn_metrics observability, and CLARIFY option buttons."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.domain.route_schema import ContextLookupSpec, RouteDecision, RouteDomain, RouteMode
from app.services import llm_client
from app.services.confirmation_service import OptionButtonsMessage
from app.services.turn_metrics import TurnMetricsStore
from app.services.turn_router import LAST_READ_TTL_SECONDS, resolve_grounded_context
from app.services.unified_executor import UnifiedExecutor


@pytest.mark.asyncio
async def test_llm_client_failover_on_429():
    """When primary provider (gemini) raises 429, llm_client automatically fails over to groq."""
    fake_settings = SimpleNamespace(
        DEFAULT_LLM_PROVIDER="gemini",
        GEMINI_API_KEY="gem-key",
        GROQ_API_KEY="groq-key",
        OPENAI_API_KEY=None,
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
        DEFAULT_LLM_MODEL="gemini-3.5-flash-lite",
        INTENT_LLM_MODEL=None,
    )

    class _RateLimitErr(Exception):
        code = 429

    calls = []

    async def fake_invoke(provider, settings, prompt, schema, model_override, timeout_s):
        calls.append(provider)
        if provider == "gemini":
            raise _RateLimitErr("429 RESOURCE_EXHAUSTED")
        return '{"ok":true}', llm_client.LLMCallMetrics(provider="groq", model="openai/gpt-oss-120b", prompt_tokens=10, output_tokens=5)

    with patch.object(llm_client, "get_settings", return_value=fake_settings), \
         patch.object(llm_client, "_invoke_provider", side_effect=fake_invoke):
        out = await llm_client.generate_json("hello", timeout_seconds=15.0)
    assert out == '{"ok":true}'
    assert calls == ["gemini", "groq"]
    metrics = llm_client.last_llm_metrics.get()
    assert metrics is not None
    assert metrics.provider == "groq"
    assert metrics.failover_used is True


def test_grounded_context_rejects_expired_ttl_records():
    ctx = GatewayUserContext(user_id=1, tenant="t", database="d", odoo_uid=1, google_account_id=5)
    expired_ts = time.time() - (LAST_READ_TTL_SECONDS + 60)
    session_ctx = {
        "last_read": {
            "odoo.partners": [
                {"id": 1, "name": "Old Corp", "phone": "0900000000", "_fetched_at": expired_ts}
            ]
        }
    }
    decision = RouteDecision(
        mode=RouteMode.CONTEXT,
        confidence=0.95,
        normalized_text="Số điện thoại khách hàng đầu tiên",
        domains=[RouteDomain.ODOO],
        context_lookup=ContextLookupSpec(item_index=1, field_name="phone"),
    )
    outcome, _ = resolve_grounded_context(decision, "sdt khach dau tien", ctx, session_ctx)
    assert outcome == "requery"


def test_turn_metrics_store_records_and_queries_weak_turns(tmp_path):
    store = TurnMetricsStore(db_path=tmp_path / "metrics.db")
    store.record(
        request_id="req-1",
        user_id=7,
        raw_text="mai co lich gi k",
        intent_type="read",
        tool_name="calendar.events.list",
        latency_ms=420.5,
        route_mode="read",
        confidence=0.95,
        llm_provider="gemini",
        llm_model="gemini-3.5-flash-lite",
        prompt_tokens=120,
        output_tokens=40,
    )
    store.record(
        request_id="req-2",
        user_id=7,
        raw_text="cai kia do",
        intent_type="clarify",
        tool_name=None,
        latency_ms=310.0,
        route_mode="clarify",
        confidence=0.4,
        fallback_used=False,
    )
    all_rows = store.query_recent(limit=10)
    assert len(all_rows) == 2
    weak_rows = store.query_recent(limit=10, only_weak=True)
    assert len(weak_rows) == 1
    assert weak_rows[0]["request_id"] == "req-2"


@pytest.mark.asyncio
async def test_unified_executor_clarify_returns_option_buttons_message():
    executor = UnifiedExecutor()
    ctx = GatewayUserContext(user_id=7, tenant="t", database="d", odoo_uid=1, telegram_binding_id="b1")
    intent = IntentAnalysisResult(
        intent_type=IntentType.CLARIFY,
        clarification_prompt="Bạn muốn xem danh sách nào?",
        candidate_options=["Lịch ngày mai", "Email mới nhất"],
    )
    with patch.object(executor.conversation_history_store, "set_pending_clarification"):
        msgs = await executor.execute(intent, ctx, 100, 100, {}, time.time() + 10, {})
    assert len(msgs) == 1
    assert isinstance(msgs[0], OptionButtonsMessage)
    assert msgs[0].options == ["Lịch ngày mai", "Email mới nhất"]


@pytest.mark.asyncio
async def test_turn_router_injects_clarification_reply_time_and_permissions():
    from app.services.turn_router import TurnRouter

    captured_prompts = []

    async def fake_llm(prompt, **kwargs):
        captured_prompts.append(prompt)
        return RouteDecision(
            mode=RouteMode.READ,
            confidence=0.92,
            normalized_text="Tìm email của anh Nam",
            domains=[RouteDomain.GMAIL],
        ).model_dump_json()

    ctx = GatewayUserContext(
        user_id=7, tenant="t", database="d", odoo_uid=1,
        timezone="Asia/Ho_Chi_Minh",
        permissions={"gmail.read", "calendar.read"},
    )
    session_ctx = {
        "pending_clarification": {"prompt": "Bạn muốn tìm email của ai?", "missing_fields": ["recipient"]},
        "reply_to_text": "Danh sách email chưa đọc: 1. Báo giá Q4",
    }
    router = TurnRouter(fake_llm)
    decision = await router.route("anh Nam", ctx, session_ctx)
    assert decision is not None
    assert decision.mode == RouteMode.READ
    p = captured_prompts[0]
    assert "<pending_clarification>" in p
    assert "<replied_message>" in p
    assert "Báo giá Q4" in p
    assert "Thời điểm hiện tại:" in p
    assert "Domain khả dụng theo quyền tài khoản:" in p
    assert "gmail" in p and "calendar" in p


@pytest.mark.asyncio
async def test_turn_router_deictic_guard_blocks_overconfident_blind_pronoun():
    from app.services.turn_router import TurnRouter

    async def overconfident_llm(prompt, **kwargs):
        return RouteDecision(
            mode=RouteMode.READ,
            confidence=0.93,
            normalized_text="Xem cái đó sao rồi",
            domains=[RouteDomain.ODOO],
        ).model_dump_json()

    ctx = GatewayUserContext(user_id=7, tenant="t", database="d", odoo_uid=1)
    router = TurnRouter(overconfident_llm)

    # Without any context anchor -> structurally downgraded to CLARIFY
    res_blind = await router.route("cai do sao roi", ctx, {})
    assert res_blind is not None
    assert res_blind.mode == RouteMode.CLARIFY
    assert res_blind.confidence < 0.6

    # With reply_to_text anchor -> allowed to proceed as READ
    res_anchored = await router.route("cai do sao roi", ctx, {"reply_to_text": "Đơn hàng S00042 đang chờ duyệt"})
    assert res_anchored is not None
    assert res_anchored.mode == RouteMode.READ


@pytest.mark.asyncio
async def test_turn_router_ambiguous_hd_abbreviation_forces_clarify():
    from app.services.turn_router import TurnRouter

    async def llm_guesses(prompt, **kwargs):
        return RouteDecision(
            mode=RouteMode.READ,
            confidence=0.9,
            normalized_text="Xem hóa đơn tháng này",
            domains=[RouteDomain.ODOO],
        ).model_dump_json()

    ctx = GatewayUserContext(user_id=7, tenant="t", database="d", odoo_uid=1)
    router = TurnRouter(llm_guesses)
    res = await router.route("xem hd thang nay", ctx, {})
    assert res is not None
    assert res.mode == RouteMode.CLARIFY
    assert res.clarify_options == ["Tra cứu hợp đồng", "Tra cứu hóa đơn"]


@pytest.mark.asyncio
async def test_tasks_done_and_calendar_update_resolve_by_title_without_id():
    from app.services.confirmation_service import ConfirmationPreview

    executor = UnifiedExecutor()
    ctx = GatewayUserContext(
        user_id=7, tenant="t", database="d", odoo_uid=1, telegram_binding_id="b1",
        permissions={"tasks.write", "calendar.write"},
    )
    fake_session = {
        "last_read": {
            "tasks": [
                {"id": "tsk_1", "title": "Gọi khách ABC"},
                {"id": "tsk_2", "title": "Gửi báo cáo tuần"},
            ],
            "calendar": [
                {"id": "ev_1", "summary": "Họp giao ban sáng", "start": "2026-11-25T09:00:00+07:00", "end": "2026-11-25T10:00:00+07:00"},
                {"id": "ev_2", "summary": "Họp đối tác X", "start": "2026-11-25T14:00:00+07:00", "end": "2026-11-25T15:00:00+07:00"},
            ],
        }
    }
    task_intent = IntentAnalysisResult(
        intent_type=IntentType.PREPARE_WRITE,
        tool_name="tasks.personal.done",
        parameters={"title": "goi khach abc"},
    )
    cal_intent = IntentAnalysisResult(
        intent_type=IntentType.PREPARE_WRITE,
        tool_name="calendar.events.update",
        parameters={"summary": "giao ban", "start_time": "2026-11-25T15:00:00+07:00"},
    )
    with patch.object(executor.conversation_history_store, "get_session", return_value=fake_session), \
         patch.object(executor.local_tasks, "get_task", AsyncMock(return_value={"id": "tsk_1", "title": "Gọi khách ABC"})), \
         patch.object(executor.calendar_provider.client, "get_calendar_event", AsyncMock(return_value=None)), \
         patch.object(executor.confirmation_service, "create_confirmation", return_value=("c_11223344", ConfirmationPreview("ok", "c_11223344"))) as mock_conf:
        task_msgs = await executor.execute(task_intent, ctx, 100, 100, {}, time.time() + 10, {})
        cal_msgs = await executor.execute(cal_intent, ctx, 100, 100, {}, time.time() + 10, {})

    assert isinstance(task_msgs[0], ConfirmationPreview)
    assert isinstance(cal_msgs[0], ConfirmationPreview)
    assert mock_conf.call_args_list[0].kwargs["arguments"]["task_id"] == "tsk_1"
    assert mock_conf.call_args_list[1].kwargs["arguments"]["event_id"] == "ev_1"


def test_select_tools_balances_multi_domain_slots():
    from app.services.workspace_tool_plan import select_tools

    tools = [
        SimpleNamespace(name="calendar.events.list", action_type="read", description="", parameter_schema={}),
        SimpleNamespace(name="calendar.events.create", action_type="write", description="", parameter_schema={}),
        SimpleNamespace(name="calendar.events.update", action_type="write", description="", parameter_schema={}),
        SimpleNamespace(name="workspace.calendar.read", action_type="read", description="", parameter_schema={}),
        SimpleNamespace(name="workspace.calendar.write", action_type="write", description="", parameter_schema={}),
        SimpleNamespace(name="workspace.gmail.read", action_type="read", description="", parameter_schema={}),
        SimpleNamespace(name="workspace.gmail.write", action_type="write", description="", parameter_schema={}),
        SimpleNamespace(name="odoo.partners.search", action_type="read", description="", parameter_schema={}),
        SimpleNamespace(name="tasks.personal.create", action_type="write", description="", parameter_schema={}),
    ]
    selected = select_tools(
        "xem lich hop roi gui mail cho khach",
        tools,
        domains=["calendar", "gmail", "odoo"],
    )
    names = {t.name for t in selected}
    assert any("calendar" in n for n in names)
    assert any("gmail" in n for n in names)
    assert "odoo.partners.search" in names
