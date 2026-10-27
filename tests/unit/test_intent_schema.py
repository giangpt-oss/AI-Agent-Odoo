import pytest
from pydantic import ValidationError

from app.domain.intent_schema import (
    DraftPayload,
    IntentAnalysisResult,
    IntentType,
)


def test_intent_schema_valid_reply():
    res = IntentAnalysisResult(
        intent_type=IntentType.REPLY,
        reply_text="Xin chào, tôi là trợ lý AI.",
    )
    assert res.intent_type == IntentType.REPLY
    assert res.reply_text == "Xin chào, tôi là trợ lý AI."


def test_intent_schema_valid_read():
    res = IntentAnalysisResult(
        intent_type=IntentType.READ,
        tool_name="calendar.events.list",
        parameters={"time_min": "2026-10-27T00:00:00Z"},
    )
    assert res.intent_type == IntentType.READ
    assert res.tool_name == "calendar.events.list"


def test_intent_schema_read_missing_tool_fails():
    with pytest.raises(ValidationError):
        IntentAnalysisResult(
            intent_type=IntentType.READ,
            # tool_name bị thiếu
        )


def test_intent_schema_valid_draft():
    draft = DraftPayload(
        draft_type="email",
        draft_action="create",
        recipient="nam@example.com",
        subject="Kế hoạch tuần",
        content="Nội dung tóm tắt kế hoạch...",
    )
    res = IntentAnalysisResult(
        intent_type=IntentType.DRAFT,
        draft=draft,
    )
    assert res.intent_type == IntentType.DRAFT
    assert res.draft.recipient == "nam@example.com"


def test_intent_schema_valid_clarify():
    res = IntentAnalysisResult(
        intent_type=IntentType.CLARIFY,
        clarification_prompt="Bạn muốn dời cuộc họp nào trong số 2 cuộc họp sau?",
        candidate_options=["1. Họp dự án A lúc 10h", "2. Họp dự án B lúc 14h"],
    )
    assert res.intent_type == IntentType.CLARIFY
    assert len(res.candidate_options) == 2


def test_intent_schema_clarify_missing_prompt_fails():
    with pytest.raises(ValidationError):
        IntentAnalysisResult(
            intent_type=IntentType.CLARIFY,
            # clarification_prompt bị thiếu
        )


def test_intent_schema_prepare_write():
    res = IntentAnalysisResult(
        intent_type=IntentType.PREPARE_WRITE,
        tool_name="calendar.events.create",
        parameters={"summary": "Họp chiến lược", "start_time": "2026-10-28T14:00:00Z"},
    )
    assert res.intent_type == IntentType.PREPARE_WRITE
    assert res.tool_name == "calendar.events.create"
