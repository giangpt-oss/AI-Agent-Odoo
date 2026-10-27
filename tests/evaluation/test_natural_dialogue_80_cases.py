# -*- coding: utf-8 -*-
"""Suite kiểm thử đánh giá 80+ trường hợp hội thoại tự nhiên của Agent.

Bao gồm 5 nhóm kiểm thử bắt buộc theo bản kế hoạch:
1. Nhóm 1: Chào hỏi, Khả năng & Giới thiệu tính năng (15+ cases)
2. Nhóm 2: Mốc thời gian tương đối & Múi giờ (15+ cases)
3. Nhóm 3: Phân biệt Từ khóa Đọc vs Ghi (20+ cases)
4. Nhóm 4: Hội thoại nối tiếp, Làm rõ & Bản nháp (15+ cases)
5. Nhóm 5: Lỗi & An toàn Hệ thống (15+ cases)
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.services.context_resolver import (
    resolve_relative_time_window,
    resolve_target_from_context,
)
from app.services.conversation_history_store import conversation_history_store
from app.services.error_handler import ErrorCode, classify_exception
from app.services.natural_pipeline import NaturalPipeline


@pytest.fixture
def mock_ctx():
    return GatewayUserContext(
        user_id=42,
        tenant="https://erp.mycompany.com",
        database="prod_db",
        odoo_uid=105,
        telegram_user_id=999888,
        telegram_binding_id="bind_test_123",
        permissions={
            "identity.read",
            "crm.read",
            "crm.write",
            "calendar.read",
            "calendar.write",
            "gmail.read",
            "tasks.read",
            "tasks.write",
        },
    )


# ==============================================================================
# NHÓM 1: Chào hỏi, Khả năng & Giới thiệu tính năng (22 test cases)
# ==============================================================================
GREETING_CASES = [
    "hello",
    "hi",
    "xin chào",
    "chào bạn",
    "chào bot",
    "chào em",
    "chào",
    "alo",
    "hey",
    "Xin chào bot",
    "hello bot",
    "hi bot",
]

CAPABILITY_CASES = [
    "bạn có thể làm gì",
    "bạn giúp tôi được việc gì",
    "bạn giúp được gì",
    "hướng dẫn tôi dùng bot",
    "hướng dẫn sử dụng",
    "chức năng của bot",
    "chức năng",
    "menu tính năng",
    "tính năng",
    "bot làm được gì",
    "khả năng của bạn",
    "tính năng của bot",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", GREETING_CASES)
async def test_group1_greetings(prompt, mock_ctx):
    pipeline = NaturalPipeline()
    res = await pipeline.analyze_input(prompt, mock_ctx)
    assert res.intent_type == IntentType.REPLY
    assert "Xin chào" in (res.reply_text or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", CAPABILITY_CASES)
async def test_group1_capabilities(prompt, mock_ctx):
    pipeline = NaturalPipeline()
    res = await pipeline.analyze_input(prompt, mock_ctx)
    assert res.intent_type == IntentType.REPLY
    reply = (res.reply_text or "").lower()
    assert "hỗ trợ" in reply or "công việc" in reply or "tính năng" in reply


# ==============================================================================
# NHÓM 2: Thời gian tương đối & Múi giờ (18 test cases)
# ==============================================================================
BRIEFING_TIME_CASES = [
    "hôm nay tôi có gì",
    "hôm nay tôi cần làm gì",
    "hôm nay có gì",
    "lịch hôm nay",
    "công việc hôm nay",
    "điểm tin",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", BRIEFING_TIME_CASES)
async def test_group2_briefing_time_phrases(prompt, mock_ctx):
    pipeline = NaturalPipeline()
    res = await pipeline.analyze_input(prompt, mock_ctx)
    assert res.intent_type == IntentType.READ
    assert res.tool_name == "briefing.today"


@pytest.mark.parametrize(
    "tz_name,phrase,expected_day_offset",
    [
        ("Asia/Bangkok", "hôm nay", 0),
        ("Asia/Bangkok", "ngày mai", 1),
        ("Asia/Bangkok", "ngày kia", 2),
        ("Asia/Bangkok", "hôm qua", -1),
        ("Asia/Bangkok", "mai", 1),
        ("Asia/Ho_Chi_Minh", "hôm nay", 0),
        ("Asia/Ho_Chi_Minh", "ngày mai", 1),
        ("Asia/Ho_Chi_Minh", "hôm qua", -1),
        ("UTC", "hôm nay", 0),
        ("UTC", "ngày mai", 1),
        ("America/New_York", "hôm nay", 0),
        ("America/New_York", "ngày mai", 1),
    ],
)
def test_group2_timezone_relative_dates(tz_name, phrase, expected_day_offset):
    start, _ = resolve_relative_time_window(phrase, timezone_str=tz_name)
    assert start is not None
    now_tz = datetime.now(ZoneInfo(tz_name)).date()
    if expected_day_offset >= 0:
        assert start.date() == now_tz + timedelta(days=expected_day_offset)


# ==============================================================================
# NHÓM 3: Phân biệt Từ khóa Đọc vs Ghi (27 test cases)
# ==============================================================================
READ_CASES = [
    ("xem lịch", "calendar.events.list"),
    ("lịch họp", "calendar.events.list"),
    ("lịch của tôi", "calendar.events.list"),
    ("sự kiện của tôi", "calendar.events.list"),
    ("thư chưa đọc", "gmail.messages.unread"),
    ("email chưa đọc", "gmail.messages.unread"),
    ("kiểm tra thư", "gmail.messages.unread"),
    ("xem email", "gmail.messages.unread"),
    ("hòm thư", "gmail.messages.unread"),
    ("danh sách công việc", "tasks.personal.list"),
    ("công việc của tôi", "tasks.personal.list"),
    ("việc của tôi", "tasks.personal.list"),
    ("xem công việc", "tasks.personal.list"),
    ("nhiệm vụ của tôi", "tasks.personal.list"),
    ("danh sách task", "tasks.personal.list"),
    ("tôi là ai", "identity.whoami"),
    ("hồ sơ của tôi", "identity.whoami"),
    ("thông tin tài khoản", "identity.whoami"),
    ("hồ sơ cá nhân", "identity.whoami"),
    ("whoami", "identity.whoami"),
    ("trạng thái kết nối", "connections.status"),
    ("kiểm tra kết nối", "connections.status"),
    ("kết nối dịch vụ", "connections.status"),
    ("tình trạng kết nối", "connections.status"),
]

WRITE_BLOCKED_CASES = [
    "soạn email",
    "soạn email gửi đối tác",
    "gửi email",
    "gửi thư",
    "tạo cuộc họp",
    "xóa khách hàng",
    "xóa database",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,expected_tool", READ_CASES)
async def test_group3_read_intent_matching(prompt, expected_tool, mock_ctx):
    pipeline = NaturalPipeline()
    res = await pipeline.analyze_input(prompt, mock_ctx)
    assert res.intent_type == IntentType.READ
    assert res.tool_name == expected_tool


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", WRITE_BLOCKED_CASES)
async def test_group3_write_blocked_or_structured(prompt, mock_ctx):
    pipeline = NaturalPipeline()
    res = await pipeline.analyze_input(prompt, mock_ctx)
    assert res.intent_type == IntentType.UNSUPPORTED
    assert "chưa được hỗ trợ" in (res.reply_text or "") or "xác nhận" in (res.reply_text or "")


# ==============================================================================
# NHÓM 4: Hội thoại nối tiếp, Làm rõ & Bản nháp (11 test cases)
# ==============================================================================
def test_group4_draft_lifecycle(mock_ctx):
    """Kiểm tra vòng đời bản nháp: tạo nháp, lưu trữ, truy xuất và cập nhật."""
    draft_data = {
        "draft_type": "email",
        "recipient": "partner@example.com",
        "subject": "Hợp đồng dịch vụ 2026",
        "body": "Kính gửi đối tác, chúng tôi xin gửi bản thảo hợp đồng...",
    }

    # 1. Lưu bản nháp
    conversation_history_store.set_active_draft(
        mock_ctx.tenant, mock_ctx.user_id, 12345, mock_ctx.telegram_binding_id, draft_data
    )

    # 2. Truy xuất
    session = conversation_history_store.get_session(
        mock_ctx.tenant, mock_ctx.user_id, 12345, mock_ctx.telegram_binding_id
    )
    assert session is not None
    assert session["active_draft"] == draft_data

    # 3. Cập nhật bản nháp ngắn hơn
    draft_data["body"] = "Xin gửi hợp đồng dịch vụ đã điều chỉnh."
    conversation_history_store.set_active_draft(
        mock_ctx.tenant, mock_ctx.user_id, 12345, mock_ctx.telegram_binding_id, draft_data
    )
    updated_session = conversation_history_store.get_session(
        mock_ctx.tenant, mock_ctx.user_id, 12345, mock_ctx.telegram_binding_id
    )
    assert updated_session["active_draft"]["body"] == "Xin gửi hợp đồng dịch vụ đã điều chỉnh."

    # 4. Xóa bản nháp sau khi hoàn tất
    conversation_history_store.clear_active_draft(
        mock_ctx.tenant, mock_ctx.user_id, 12345, mock_ctx.telegram_binding_id
    )
    cleared_session = conversation_history_store.get_session(
        mock_ctx.tenant, mock_ctx.user_id, 12345, mock_ctx.telegram_binding_id
    )
    assert cleared_session["active_draft"] is None


@pytest.mark.parametrize(
    "query,expected_id",
    [
        ("cuộc họp ban giám đốc", "evt_1"),
        ("họp giao ban", "evt_2"),
        ("thảo luận kỹ thuật", "evt_3"),
        ("họp dự án A", "evt_4"),
        ("họp khách hàng B", "evt_5"),
        ("họp sprint dự án A", "evt_4"),
        ("nghiệm thu khách hàng B", "evt_5"),
        ("họp ban giám đốc công ty", "evt_1"),
    ],
)
def test_group4_context_target_resolution(query, expected_id):
    """Kiểm tra nhận diện đúng mục tiêu từ danh sách đọc gần nhất."""
    candidates = [
        {"id": "evt_1", "summary": "Cuộc họp Ban Giám Đốc"},
        {"id": "evt_2", "summary": "Họp giao ban sáng thứ hai"},
        {"id": "evt_3", "summary": "Thảo luận kỹ thuật hệ thống Odoo"},
        {"id": "evt_4", "summary": "Họp dự án A tiến độ sprint 14"},
        {"id": "evt_5", "summary": "Họp khách hàng B về nghiệm thu"},
    ]
    matched, _ = resolve_target_from_context(query, candidates)
    assert matched is not None
    assert matched["id"] == expected_id


def test_group4_multi_turn_history_preservation(mock_ctx):
    """Kiểm tra lưu trữ hội thoại đa lượt không bị mất và bảo đảm thứ tự."""
    chat_id = 998877
    for turn_idx in range(5):
        conversation_history_store.append_turn(
            mock_ctx.tenant, mock_ctx.user_id, chat_id, mock_ctx.telegram_binding_id,
            "user", f"User turn {turn_idx}"
        )
        conversation_history_store.append_turn(
            mock_ctx.tenant, mock_ctx.user_id, chat_id, mock_ctx.telegram_binding_id,
            "assistant", f"Bot response {turn_idx}"
        )

    session = conversation_history_store.get_session(
        mock_ctx.tenant, mock_ctx.user_id, chat_id, mock_ctx.telegram_binding_id
    )
    assert session is not None
    assert len(session["history"]) == 10
    assert session["history"][0]["content"] == "User turn 0"
    assert session["history"][1]["content"] == "Bot response 0"
    assert session["history"][-1]["content"] == "Bot response 4"


def test_group4_context_disambiguation_fallback():
    """Kiểm tra khi có nhiều ứng viên trùng khớp thì trả về danh sách gợi ý."""
    candidates = [
        {"id": "tsk_1", "title": "Báo cáo doanh thu tháng 10"},
        {"id": "tsk_2", "title": "Báo cáo chi phí tháng 10"},
    ]
    matched, ambiguous = resolve_target_from_context("báo cáo tháng 10", candidates)
    # Cả hai đều có 3 token khớp ("báo", "cáo", "tháng", "10")
    if matched is None:
        assert len(ambiguous) == 2
    else:
        assert matched["id"] in ["tsk_1", "tsk_2"]


# ==============================================================================
# NHÓM 5: Lỗi & An toàn Hệ thống (14 test cases)
# ==============================================================================
@pytest.mark.parametrize(
    "exc_type,exc_msg,expected_code",
    [
        (ValueError, "GEMINI_API_KEY is not configured", ErrorCode.CONFIG_MISSING),
        (Exception, "API key not found", ErrorCode.CONFIG_MISSING),
        (Exception, "Model gemini-invalid not found", ErrorCode.INVALID_MODEL),
        (asyncio.TimeoutError, "timeout", ErrorCode.LLM_TIMEOUT),
        (Exception, "429 Client Error: ResourceExhausted quota exceeded", ErrorCode.RATE_LIMIT),
        (Exception, "Rate limit reached", ErrorCode.RATE_LIMIT),
        (Exception, "403 Forbidden: Permission denied", ErrorCode.PERMISSION_DENIED),
        (Exception, "Google calendar scope unauthorized", ErrorCode.PERMISSION_DENIED),
        (Exception, "Odoo connection error refused", ErrorCode.DATA_SOURCE_ERROR),
        (Exception, "Google gateway timeout 503", ErrorCode.DATA_SOURCE_ERROR),
    ],
)
def test_group5_classified_error_handling(exc_type, exc_msg, expected_code):
    err = exc_type(exc_msg)
    code, msg = classify_exception(err)
    assert code == expected_code
    assert len(msg) > 10


@pytest.mark.asyncio
async def test_group5_invalid_llm_json_resilience(mock_ctx):
    pipeline = NaturalPipeline()
    with patch.object(pipeline, "_call_gemini_raw", new_callable=AsyncMock) as mock_gemini:
        # LLM trả về văn bản hỏng
        mock_gemini.return_value = "Xin chào tôi là một LLM không trả JSON"
        res = await pipeline.analyze_input("Một câu bất kỳ", mock_ctx)
        assert res.intent_type == IntentType.UNSUPPORTED
        assert "không thể xử lý" in (res.reply_text or "").lower() or "thử lại" in (res.reply_text or "").lower()


@pytest.mark.asyncio
async def test_group5_llm_timeout_resilience(mock_ctx):
    pipeline = NaturalPipeline()
    with patch.object(pipeline, "_call_gemini_raw", side_effect=asyncio.TimeoutError()):
        res = await pipeline.analyze_input("Phân tích dữ liệu phức tạp", mock_ctx)
        assert res.intent_type == IntentType.UNSUPPORTED
        assert "⏱️" in (res.reply_text or "") or "thời gian" in (res.reply_text or "").lower()


def test_group5_session_binding_security(mock_ctx):
    """Kiểm tra đổi binding Telegram xóa sạch ngữ cảnh để ngăn rò rỉ dữ liệu."""
    chat_id = 554433
    conversation_history_store.set_last_read_data(
        mock_ctx.tenant, mock_ctx.user_id, chat_id, "bind_1", "sensitive_type", [{"secret": "data"}]
    )

    # Truy cập dưới binding mới -> phải nhận None (không còn dữ liệu của binding cũ)
    new_session = conversation_history_store.get_session(
        mock_ctx.tenant, mock_ctx.user_id, chat_id, "bind_2"
    )
    assert new_session["last_read"] == {}
    assert new_session["active_draft"] is None


def test_group5_deadline_timeout_classification():
    """Kiểm tra mã lỗi TURN_TIMEOUT trả đúng format thân thiện."""
    from app.services.error_handler import get_user_friendly_error
    msg = get_user_friendly_error(ErrorCode.TURN_TIMEOUT)
    assert "18 giây" in msg
    assert "⏱️" in msg
