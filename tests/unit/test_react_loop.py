"""Unit tests for the bounded ReAct loop (Perception -> Reasoning -> Planning -> Action -> Observation)."""
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.domain.tool_registry import ToolDefinition, ToolRegistry
from app.services.confirmation_service import ConfirmationPreview, OptionButtonsMessage
from app.services.react_loop import ReactAgentLoop, needs_react_continuation
from app.services.unified_executor import UnifiedExecutor


def _ctx() -> GatewayUserContext:
    return GatewayUserContext(
        user_id=2,
        tenant="https://erp.example.com",
        database="odoo",
        odoo_uid=27,
        telegram_user_id=100,
        permissions=[
            "odoo.read",
            "sales.read",
            "sales.write",
            "gmail.read",
            "gmail.write",
            "calendar.read",
        ],
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolDefinition(name="odoo.products.search", category="odoo", action_type="read", description="Search products", required_permission="odoo.read"))
    reg.register(ToolDefinition(name="odoo.partners.search", category="odoo", action_type="read", description="Search partners", required_permission="odoo.read"))
    reg.register(ToolDefinition(name="odoo.sales.search", category="odoo", action_type="read", description="Search sales", required_permission="sales.read"))
    reg.register(ToolDefinition(name="business.lookup", category="business", action_type="read", description="Business lookup"))
    reg.register(ToolDefinition(name="workspace.gmail.write", category="workspace", action_type="write", description="Send email", required_permission="gmail.write"))
    return reg


def test_needs_react_continuation_criteria():
    read_intent = IntentAnalysisResult(intent_type=IntentType.READ, tool_name="odoo.products.search", parameters={"query": "X"})
    assert needs_react_continuation("tìm sản phẩm X", read_intent, ["Không tìm thấy sản phẩm X"]) is True
    assert needs_react_continuation("xem đơn mua P00065", IntentAnalysisResult(intent_type=IntentType.READ, tool_name="business.lookup"), ['{"status":"ok","items":[{"id":65}]}']) is True
    assert needs_react_continuation("tìm anh Diệp rồi xem báo giá", read_intent, ["Tìm thấy Anh Diệp"]) is True
    assert needs_react_continuation("tìm anh Diệp", read_intent, ["Tìm thấy Anh Diệp (id=10)"]) is False
    assert needs_react_continuation("xác nhận", read_intent, [ConfirmationPreview("preview", "c_123")]) is False


@pytest.mark.asyncio
async def test_react_loop_observes_empty_result_and_retries_relaxed_query():
    ctx = _ctx()
    reg = _registry()
    calls = []

    async def fake_read(intent: IntentAnalysisResult):
        calls.append((intent.tool_name, intent.parameters))
        if intent.parameters.get("query") == "siêu âm":
            return ["Tìm thấy 1 sản phẩm: Máy siêu âm 4D (ID 55)"]
        return ["Không tìm thấy sản phẩm nào khớp"]

    llm_responses = [
        json.dumps({
            "thought": "Query quá cụ thể trả về 0 kết quả; thử rút gọn còn 'siêu âm'.",
            "decision": "call_tools",
            "tool_calls": [{"tool_name": "odoo.products.search", "parameters": {"query": "siêu âm"}}],
        }),
        json.dumps({
            "thought": "Observation đã có đúng sản phẩm Máy siêu âm 4D.",
            "decision": "reply",
            "reply_text": "Tìm thấy sản phẩm: Máy siêu âm 4D (ID 55).",
        }),
    ]
    idx = {"i": 0}

    async def fake_llm(prompt, schema):
        assert "Không tìm thấy sản phẩm" in prompt or "siêu âm" in prompt
        out = llm_responses[idx["i"]]
        idx["i"] += 1
        return out

    loop = ReactAgentLoop(llm_caller=fake_llm)
    res = await loop.run(
        user_text="tìm máy siêu âm XYZ-9000",
        context=ctx,
        session_context={"history": []},
        registry=reg,
        execute_read=fake_read,
        execute_prepare_write=AsyncMock(),
        initial_steps=[("odoo.products.search", {"query": "máy siêu âm XYZ-9000"}, ["Không tìm thấy sản phẩm"])],
        deadline=time.time() + 20,
    )
    assert res == ["Tìm thấy sản phẩm: Máy siêu âm 4D (ID 55)."]
    assert calls == [("odoo.products.search", {"query": "siêu âm"})]


@pytest.mark.asyncio
async def test_react_loop_reads_observation_then_prepares_confirmed_write():
    ctx = _ctx()
    reg = _registry()

    async def fake_read(intent: IntentAnalysisResult):
        return ["Đã tìm thấy đối tác Anh Diệp (email: diep@example.com)"]

    async def fake_prepare_write(intent: IntentAnalysisResult):
        assert intent.tool_name == "workspace.gmail.write"
        assert intent.parameters["to"] == "diep@example.com"
        return [ConfirmationPreview("Gửi mail tới diep@example.com", "c_mail_1")]

    llm_caller = AsyncMock(return_value=json.dumps({
        "thought": "Đã quan sát thấy email của Anh Diệp là diep@example.com, giờ chuẩn bị gửi mail cần user xác nhận.",
        "decision": "prepare_write",
        "write_tool_name": "workspace.gmail.write",
        "write_parameters": {"to": "diep@example.com", "subject": "Nhắc lịch", "body": "Chào anh Diệp"},
    }))

    loop = ReactAgentLoop(llm_caller=llm_caller)
    res = await loop.run(
        user_text="tìm email anh Diệp rồi gửi mail nhắc lịch",
        context=ctx,
        session_context={},
        registry=reg,
        execute_read=fake_read,
        execute_prepare_write=fake_prepare_write,
        initial_steps=[("odoo.partners.search", {"query": "Anh Diệp"}, ["Đối tác: Anh Diệp · Email: diep@example.com"])],
        deadline=time.time() + 20,
    )
    assert len(res) == 1
    assert isinstance(res[0], ConfirmationPreview)
    assert res[0].confirmation_id == "c_mail_1"


@pytest.mark.asyncio
async def test_react_loop_blocks_write_inside_call_tools_and_deduplicates():
    ctx = _ctx()
    reg = _registry()
    read_calls = []

    async def fake_read(intent: IntentAnalysisResult):
        read_calls.append(intent.tool_name)
        return ['{"status":"ok","items":[{"id":65,"name":"P00065"}]}']

    llm_responses = [
        # Attempt 1 tries duplicate + unauthorized write inside call_tools
        json.dumps({
            "thought": "Thử gọi lại và tự gửi mail.",
            "decision": "call_tools",
            "tool_calls": [
                {"tool_name": "business.lookup", "parameters": {"kind": "purchases", "query": "P00065"}},
                {"tool_name": "workspace.gmail.write", "parameters": {"to": "a@b.com"}},
            ],
        }),
        json.dumps({
            "thought": "Trả kết quả tổng hợp sau khi bị chặn lặp/ghi.",
            "decision": "reply",
            "reply_text": "Đơn mua P00065 đang ở trạng thái nháp.",
        }),
    ]
    idx = {"i": 0}

    async def fake_llm(prompt, schema):
        out = llm_responses[idx["i"]]
        idx["i"] += 1
        return out

    loop = ReactAgentLoop(llm_caller=fake_llm)
    res = await loop.run(
        user_text="kiểm tra đơn mua P00065",
        context=ctx,
        session_context={},
        registry=reg,
        execute_read=fake_read,
        execute_prepare_write=AsyncMock(),
        initial_steps=[("business.lookup", {"kind": "purchases", "query": "P00065"}, ['{"status":"ok","items":[{"id":65,"name":"P00065"}]}'])],
        deadline=time.time() + 20,
    )
    assert read_calls == []
    # Either breaks safely on no-progress and synthesizes, or stops with existing data
    assert res


@pytest.mark.asyncio
async def test_unified_executor_runs_react_loop_when_enabled():
    reg = _registry()
    executor = UnifiedExecutor(registry=reg)
    executor._handle_read = AsyncMock(side_effect=[
        ["Không tìm thấy sản phẩm 'bàn ABC'"],
        ["Tìm thấy: Bàn làm việc ABC (ID 9)"],
    ])

    fake_settings = SimpleNamespace(ENABLE_REACT_LOOP=True, REACT_MAX_ITERATIONS=4, REACT_TIME_BUDGET_SECONDS=24.0)
    fake_loop_caller = AsyncMock(side_effect=[
        json.dumps({
            "thought": "Thử lại ngắn hơn",
            "decision": "call_tools",
            "tool_calls": [{"tool_name": "odoo.products.search", "parameters": {"query": "Bàn"}}],
        }),
        json.dumps({
            "thought": "Đã có kết quả",
            "decision": "reply",
            "reply_text": "Sản phẩm có sẵn: Bàn làm việc ABC (ID 9).",
        }),
    ])
    test_loop = ReactAgentLoop(llm_caller=fake_loop_caller)

    with patch("app.core.config.get_settings", return_value=fake_settings), \
         patch("app.services.react_loop.react_agent_loop", test_loop):
        out = await executor.execute(
            IntentAnalysisResult(intent_type=IntentType.READ, tool_name="odoo.products.search", parameters={"query": "bàn ABC"}),
            _ctx(),
            100,
            200,
            {"_user_text": "tìm bàn ABC", "history": []},
            time.time() + 25,
            {},
        )
    assert out == ["Sản phẩm có sẵn: Bàn làm việc ABC (ID 9)."]
    assert executor._handle_read.await_count == 2


@pytest.mark.asyncio
async def test_react_checklist_schema_validation_and_loop_breaker():
    """Verify Tool Schema Check blocks malformed calls before Action and Loop Breaker stops at 3 failures."""
    ctx = _ctx()
    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="odoo.products.search",
        category="odoo",
        action_type="read",
        description="Search products",
        required_permission="odoo.read",
        parameter_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ))
    exec_calls = []

    async def fake_read(intent: IntentAnalysisResult):
        exec_calls.append(intent.parameters)
        return ["Không tìm thấy sản phẩm"]

    # Step 1 initial fails -> Step 2 schema check catches limit="abc" (not executed!) -> Step 3 fails -> Loop Breaker triggers!
    llm_responses = [
        json.dumps({
            "thought": "Thử gọi với limit sai kiểu",
            "decision": "call_tools",
            "tool_calls": [{"tool_name": "odoo.products.search", "parameters": {"query": "test", "limit": "abc"}}],
        }),
        json.dumps({
            "thought": "Sửa lại limit=5",
            "decision": "call_tools",
            "tool_calls": [{"tool_name": "odoo.products.search", "parameters": {"query": "test2", "limit": 5}}],
        }),
    ]
    idx = {"i": 0}

    async def fake_llm(prompt, schema):
        if idx["i"] == 1:
            assert "<failed_attempts>" in prompt
            assert "SAI_THAM_SỐ" in prompt or "Lỗi schema" in prompt
        out = llm_responses[idx["i"]]
        idx["i"] += 1
        return out

    loop = ReactAgentLoop(llm_caller=fake_llm)
    res = await loop.run(
        user_text="tìm sản phẩm Z",
        context=ctx,
        session_context={},
        registry=reg,
        execute_read=fake_read,
        execute_prepare_write=AsyncMock(),
        initial_steps=[("odoo.products.search", {"query": "Z"}, ["Không tìm thấy sản phẩm"])],
        deadline=time.time() + 20,
    )
    assert len(res) == 1
    assert "3 bước liên tiếp" in res[0]
    # limit="abc" was caught by Tool Schema Check and NEVER passed to execute_read
    assert exec_calls == [{"query": "test2", "limit": 5}]


@pytest.mark.asyncio
async def test_react_checklist_anti_hallucination_blocks_fabricated_doc_and_email():
    """Verify Anti-Hallucination rejects replies inventing unseen PO codes/emails and forces grounded reply."""
    ctx = _ctx()
    reg = _registry()

    llm_responses = [
        # Hallucinated draft citing unseen S99999 and fake@evil.com
        json.dumps({
            "thought": "Trả lời nhanh",
            "decision": "reply",
            "grounded_check": True,
            "reply_text": "Báo giá S99999 đã gửi cho fake@evil.com rồi nhé.",
        }),
        # Corrected grounded reply after anti-hallucination warning
        json.dumps({
            "thought": "Sửa lại chỉ dựa trên Observation thật P00065.",
            "decision": "reply",
            "grounded_check": True,
            "reflection_note": "100% căn cứ Observation",
            "reply_text": "Đơn mua P00065 hiện đang ở trạng thái nháp.",
        }),
    ]
    idx = {"i": 0}

    async def fake_llm(prompt, schema):
        if idx["i"] == 1:
            assert "CHỐNG ẢO GIÁC" in prompt
        out = llm_responses[idx["i"]]
        idx["i"] += 1
        return out

    loop = ReactAgentLoop(llm_caller=fake_llm)
    res = await loop.run(
        user_text="kiểm tra P00065",
        context=ctx,
        session_context={},
        registry=reg,
        execute_read=AsyncMock(),
        execute_prepare_write=AsyncMock(),
        initial_steps=[("business.lookup", {"kind": "purchases", "query": "P00065"}, ['{"status":"ok","items":[{"name":"P00065","state":"draft"}]}'])],
        deadline=time.time() + 20,
    )
    assert res == ["Đơn mua P00065 hiện đang ở trạng thái nháp."]
    assert idx["i"] == 2
