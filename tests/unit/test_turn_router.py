# -*- coding: utf-8 -*-
"""Comprehensive offline unit tests for Stage 1 TurnRouter, grounded context, and read-only safety."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.domain.route_schema import ContextLookupSpec, RouteDecision, RouteDomain, RouteMode
from app.services.natural_pipeline import NaturalPipeline
from app.services.turn_router import resolve_grounded_context
from app.services.workspace_tool_plan import select_tools


@pytest.fixture
def ctx():
    return GatewayUserContext(
        user_id=7,
        tenant="https://erp.example.com",
        database="prod",
        odoo_uid=12,
        telegram_user_id=555666,
        telegram_binding_id="bind_1",
        google_account_id=10,
        google_account_email="a@example.com",
        permissions={
            "identity.read",
            "crm.read",
            "crm.write",
            "calendar.read",
            "calendar.write",
            "gmail.read",
            "gmail.write",
            "drive.read",
            "tasks.read",
            "tasks.write",
            "odoo.read",
        },
    )


def _settings_with(**overrides):
    base = {
        "ENABLE_TWO_STAGE_ROUTER": True,
        "ROUTER_LLM_MODEL": None,
        "ROUTER_CONFIDENCE_THRESHOLD": 0.6,
        "ROUTER_TIMEOUT_SECONDS": 8.0,
        "ENABLE_QUOTATIONS": True,
        "ENABLE_REPORTING": False,
        "ENABLE_QUOTATION_APPROVAL_FLOW": False,
        "GEMINI_API_KEY": "test-key",
        "GROQ_API_KEY": None,
        "OPENAI_API_KEY": None,
        "DEFAULT_LLM_PROVIDER": "gemini",
        "DEFAULT_LLM_MODEL": "gemini-3.5-flash-lite",
        "INTENT_LLM_MODEL": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_flag_off_never_calls_router(ctx):
    pipeline = NaturalPipeline()
    pipeline._call_gemini_raw = AsyncMock(
        return_value=json.dumps({"intent_type": "read", "tool_name": "calendar.events.list", "parameters": {}})
    )
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with(ENABLE_TWO_STAGE_ROUTER=False)):
        res = await pipeline.analyze_input("mai co lich gi k", ctx, {})
    assert res.intent_type == IntentType.READ
    assert pipeline._call_gemini_raw.await_count == 1  # Only Stage 2, zero Stage 1 calls


@pytest.mark.asyncio
async def test_router_error_single_fallback_with_raw_text(ctx):
    pipeline = NaturalPipeline()
    pipeline._call_gemini_raw = AsyncMock(
        side_effect=[
            "not valid json {{{",  # Stage 1 fails
            json.dumps({"intent_type": "read", "tool_name": "calendar.events.list", "parameters": {}}),  # Fallback Stage 2
        ]
    )
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        res = await pipeline.analyze_input("mai co lich gi k", ctx, {})
    assert res.intent_type == IntentType.READ
    assert res.tool_name == "calendar.events.list"
    assert pipeline._call_gemini_raw.await_count == 2
    # Fallback prompt preserved exact raw text
    fallback_prompt = pipeline._call_gemini_raw.await_args_list[1].args[0]
    assert "mai co lich gi k" in fallback_prompt


@pytest.mark.asyncio
async def test_low_confidence_returns_specific_clarify(ctx):
    pipeline = NaturalPipeline()
    decision = RouteDecision(
        mode=RouteMode.READ,
        confidence=0.45,
        normalized_text="Cái đó thế nào?",
        domains=[],
        clarify_reason="Chưa rõ bạn đang hỏi về đơn hàng hay khách hàng nào.",
        clarify_options=["Xem đơn hàng gần đây", "Xem danh sách khách hàng"],
    )
    pipeline._call_gemini_raw = AsyncMock(return_value=decision.model_dump_json())
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        res = await pipeline.analyze_input("cai do the nao", ctx, {})
    assert res.intent_type == IntentType.CLARIFY
    assert "đơn hàng hay khách hàng" in (res.clarification_prompt or "")
    assert len(res.candidate_options) == 2
    assert pipeline._call_gemini_raw.await_count == 1  # No Stage 2 call


@pytest.mark.asyncio
async def test_chitchat_skips_knowledge_query(ctx):
    pipeline = NaturalPipeline()
    decision = RouteDecision(
        mode=RouteMode.CHITCHAT,
        confidence=0.96,
        normalized_text="Ok cảm ơn nha",
        domains=[],
    )
    pipeline._call_gemini_raw = AsyncMock(return_value=decision.model_dump_json())
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        res = await pipeline.analyze_input("ok cam on nha", ctx, {})
    assert res.intent_type == IntentType.REPLY
    assert res.tool_name is None
    assert "hỗ trợ" in (res.reply_text or "").lower()


@pytest.mark.asyncio
async def test_negation_guard_blocks_write(ctx):
    """Even if Stage 1 LLM mistakenly returns prepare_write for 'đừng gửi mail cho Nam', safety guard blocks it."""
    pipeline = NaturalPipeline()
    wrong_decision = RouteDecision(
        mode=RouteMode.PREPARE_WRITE,
        confidence=0.91,
        normalized_text="Đừng gửi email cho Nam",
        domains=[RouteDomain.GMAIL],
    )
    pipeline._call_gemini_raw = AsyncMock(return_value=wrong_decision.model_dump_json())
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        res = await pipeline.analyze_input("đừng gửi mail cho Nam nhé", ctx, {})
    assert res.intent_type == IntentType.REPLY
    assert "không thực hiện" in (res.reply_text or "").lower()


@pytest.mark.asyncio
async def test_knowledge_mode_routes_to_knowledge_query_with_normalized_text(ctx):
    pipeline = NaturalPipeline()
    decision = RouteDecision(
        mode=RouteMode.KNOWLEDGE,
        confidence=0.94,
        normalized_text="Quy trình xin nghỉ phép như thế nào?",
        domains=[RouteDomain.KNOWLEDGE],
    )
    pipeline._call_gemini_raw = AsyncMock(return_value=decision.model_dump_json())
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        res = await pipeline.analyze_input("nghi phep thi lam s", ctx, {})
    assert res.intent_type == IntentType.READ
    assert res.tool_name == "knowledge.query"
    assert res.parameters == {"query": "Quy trình xin nghỉ phép như thế nào?"}


def test_grounded_context_exact_order_and_field(ctx):
    session_ctx = {
        "last_read": {
            "odoo.partners": [
                {"id": 101, "name": "Công ty Alpha", "phone": "0901111111"},
                {"id": 102, "name": "Công ty Beta", "phone": "0902222222"},
            ]
        }
    }
    decision = RouteDecision(
        mode=RouteMode.CONTEXT,
        confidence=0.95,
        normalized_text="Số điện thoại khách hàng thứ 2",
        domains=[RouteDomain.ODOO],
        context_lookup=ContextLookupSpec(item_index=2, field_name="phone"),
    )
    outcome, payload = resolve_grounded_context(decision, "sdt khach thu 2", ctx, session_ctx)
    assert outcome == "reply"
    assert "Công ty Beta" in payload
    assert "0902222222" in payload


def test_grounded_context_missing_field_triggers_requery(ctx):
    session_ctx = {
        "last_read": {
            "odoo.partners": [
                {"id": 101, "name": "Công ty Alpha"},
                {"id": 102, "name": "Công ty Beta", "email": "beta@example.com"},
            ]
        }
    }
    decision = RouteDecision(
        mode=RouteMode.CONTEXT,
        confidence=0.92,
        normalized_text="Số điện thoại khách hàng thứ 2",
        domains=[RouteDomain.ODOO],
        context_lookup=ContextLookupSpec(item_index=2, field_name="phone"),
    )
    outcome, hint = resolve_grounded_context(decision, "sdt khach thu 2", ctx, session_ctx)
    assert outcome == "requery"
    assert "Công ty Beta" in hint
    assert "102" in hint


def test_grounded_context_ambiguous_two_lists_triggers_clarify(ctx):
    session_ctx = {
        "last_read": {
            "odoo.partners": [{"id": 1, "name": "Khách A"}, {"id": 2, "name": "Khách B"}],
            "workspace.gmail": [{"id": "m1", "subject": "Mail 1"}, {"id": "m2", "subject": "Mail 2"}],
        }
    }
    decision = RouteDecision(
        mode=RouteMode.CONTEXT,
        confidence=0.88,
        normalized_text="Cái thứ 2 là gì?",
        domains=[],
        context_lookup=ContextLookupSpec(item_index=2),
    )
    outcome, payload = resolve_grounded_context(decision, "cai thu 2 la gi", ctx, session_ctx)
    assert outcome == "clarify"
    prompt_msg, options = payload
    assert "nhiều danh sách" in prompt_msg.lower()
    assert len(options) == 2


def test_grounded_context_live_status_always_requeries(ctx):
    session_ctx = {
        "last_read": {
            "odoo.quotations": [{"id": 1, "name": "S00010", "state": "draft"}]
        }
    }
    decision = RouteDecision(
        mode=RouteMode.CONTEXT,
        confidence=0.9,
        normalized_text="Đơn S00010 đã duyệt chưa?",
        domains=[RouteDomain.QUOTATION],
        context_lookup=ContextLookupSpec(item_index=1, field_name="status", asks_live_status=False),
    )
    outcome, _ = resolve_grounded_context(decision, "đơn S00010 đã duyệt chưa?", ctx, session_ctx)
    assert outcome == "requery"


def test_grounded_context_blocks_cross_account_record(ctx):
    """Records belonging to a different Google account or owner_key must NEVER be returned."""
    session_ctx = {
        "last_read": {
            "workspace.gmail": [
                {"id": "m1", "subject": "Secret mail from account 99", "google_account_id": 99}
            ]
        }
    }
    decision = RouteDecision(
        mode=RouteMode.CONTEXT,
        confidence=0.93,
        normalized_text="Tiêu đề mail đầu tiên",
        domains=[RouteDomain.GMAIL],
        context_lookup=ContextLookupSpec(item_index=1, field_name="name"),
    )
    outcome, _ = resolve_grounded_context(decision, "tieu de mail dau tien", ctx, session_ctx)
    assert outcome == "requery"


@pytest.mark.asyncio
async def test_read_only_mode_blocks_write_tool_from_stage2(ctx):
    """If Stage 1 says READ, Stage 2 returning PREPARE_WRITE or a write tool MUST be blocked."""
    pipeline = NaturalPipeline()
    stage1 = RouteDecision(
        mode=RouteMode.READ,
        confidence=0.95,
        normalized_text="Xem lịch ngày mai",
        domains=[RouteDomain.CALENDAR],
    )
    bad_stage2 = json.dumps({
        "intent_type": "prepare_write",
        "tool_name": "calendar.events.create",
        "parameters": {"summary": "Họp"},
    })
    pipeline._call_gemini_raw = AsyncMock(side_effect=[stage1.model_dump_json(), bad_stage2])
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        res = await pipeline.analyze_input("mai co lich gi k", ctx, {})
    assert res.intent_type != IntentType.PREPARE_WRITE
    assert res.intent_type == IntentType.CLARIFY


@pytest.mark.asyncio
async def test_ok_with_edit_instruction_does_not_trigger_confirm(ctx):
    """'đồng ý, sửa số lượng thành 3' must NOT fire ACTION_CONFIRM."""
    pipeline = NaturalPipeline()
    decision = RouteDecision(
        mode=RouteMode.PREPARE_WRITE,
        confidence=0.9,
        normalized_text="Đồng ý, sửa số lượng thành 3",
        domains=[RouteDomain.QUOTATION],
    )
    stage2 = json.dumps({
        "intent_type": "read",
        "tool_name": "sales.quotation.collect",
        "parameters": {"action": "update"},
    })
    pipeline._call_gemini_raw = AsyncMock(side_effect=[decision.model_dump_json(), stage2])
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with(ENABLE_QUOTATIONS=False)), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with(ENABLE_QUOTATIONS=False)):
        res = await pipeline.analyze_input("đồng ý sửa số lượng thành 3", ctx, {})
    assert res.intent_type != IntentType.ACTION_CONFIRM


@pytest.mark.asyncio
async def test_topic_switch_during_quotation_draft_preserves_draft(ctx):
    """Asking 'mai có lịch gì?' while a quotation draft is active routes to calendar without cancelling draft."""
    pipeline = NaturalPipeline()
    stage1 = RouteDecision(
        mode=RouteMode.READ,
        confidence=0.95,
        normalized_text="Ngày mai tôi có lịch gì không?",
        domains=[RouteDomain.CALENDAR],
    )
    stage2 = json.dumps({
        "intent_type": "read",
        "tool_name": "calendar.events.list",
        "parameters": {},
    })
    pipeline._call_gemini_raw = AsyncMock(side_effect=[stage1.model_dump_json(), stage2])
    session_ctx = {"active_draft": {"draft_type": "quotation", "draft_id": "q1", "version": 1}}
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        res = await pipeline.analyze_input("mai có lịch gì?", ctx, session_ctx)
    assert res.intent_type == IntentType.READ
    assert res.tool_name == "calendar.events.list"
    # Original session_ctx still has active_draft untouched
    assert session_ctx["active_draft"]["draft_id"] == "q1"


def test_select_tools_read_only_and_domain_hints():
    tools = [
        SimpleNamespace(name="calendar.events.list", action_type="read", description="list", parameter_schema={}),
        SimpleNamespace(name="calendar.events.create", action_type="write", description="create", parameter_schema={}),
        SimpleNamespace(name="gmail.messages.send", action_type="write", description="send", parameter_schema={}),
        SimpleNamespace(name="knowledge.query", action_type="read", description="kb", parameter_schema={}),
    ]
    selected = select_tools(
        "mai co gi k",
        tools,
        normalized_text="Ngày mai có lịch gì không?",
        domains=["calendar"],
        read_only=True,
    )
    names = [t.name for t in selected]
    assert "calendar.events.list" in names
    assert "calendar.events.create" not in names
    assert "gmail.messages.send" not in names


@pytest.mark.asyncio
async def test_quotation_other_action_strips_draft_and_uses_two_stage_router(ctx):
    """When quotation extractor returns action='other' (e.g. inventory/task switch),
    route via Two-Stage Router without active_draft pulling it back, while preserving the draft."""
    pipeline = NaturalPipeline()
    quote_other = json.dumps({"action": "other"})
    stage1 = RouteDecision(
        mode=RouteMode.READ,
        confidence=0.94,
        normalized_text="Tồn kho sản phẩm A còn bao nhiêu?",
        domains=[RouteDomain.ODOO],
    )
    stage2 = json.dumps({
        "intent_type": "read",
        "tool_name": "odoo.products.search",
        "parameters": {"query": "A"},
    })
    pipeline._call_gemini_raw = AsyncMock(side_effect=[quote_other, stage1.model_dump_json(), stage2])
    session_ctx = {"active_draft": {"draft_type": "quotation", "draft_id": "q9", "version": 2}}
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        res = await pipeline.analyze_input("ton kho san pham A con bn", ctx, session_ctx)
    assert res.intent_type == IntentType.READ
    assert res.tool_name == "odoo.products.search"
    # Stage 1 and Stage 2 prompts must NOT contain active_draft
    assert "<active_draft>" not in pipeline._call_gemini_raw.await_args_list[1].args[0]
    assert "<active_draft>" not in pipeline._call_gemini_raw.await_args_list[2].args[0]
    # Original session_ctx still preserves the unfinished quotation draft
    assert session_ctx["active_draft"]["draft_id"] == "q9"


@pytest.mark.asyncio
async def test_button_only_confirmation_blocks_text_confirm(ctx):
    """When ENABLE_TWO_STAGE_ROUTER=True, typing 'xác nhận' / 'đồng ý' / 'ok' NEVER confirms;
    only Telegram inline button callbacks (/confirm c_..., /cancel c_...) confirm or cancel."""
    pipeline = NaturalPipeline()
    chitchat = RouteDecision(
        mode=RouteMode.CHITCHAT,
        confidence=0.95,
        normalized_text="Xác nhận",
        domains=[],
    )
    pipeline._call_gemini_raw = AsyncMock(return_value=chitchat.model_dump_json())
    with patch("app.services.natural_pipeline.get_settings", return_value=_settings_with()), \
         patch("app.services.turn_router.get_settings", return_value=_settings_with()):
        for text in ("xác nhận", "đồng ý", "ok", "hủy"):
            res = await pipeline.analyze_input(text, ctx, {})
            assert res.intent_type not in (IntentType.ACTION_CONFIRM, IntentType.ACTION_CANCEL)

        btn_confirm = await pipeline.analyze_input("/confirm c_1234abcd", ctx, {})
        assert btn_confirm.intent_type == IntentType.ACTION_CONFIRM
        assert btn_confirm.confirmation_id == "c_1234abcd"

        btn_cancel = await pipeline.analyze_input("/cancel c_1234abcd", ctx, {})
        assert btn_cancel.intent_type == IntentType.ACTION_CANCEL
        assert btn_cancel.confirmation_id == "c_1234abcd"
