from datetime import datetime
import pytest
import zoneinfo

from app.services.context_resolver import (
    resolve_relative_time_window,
    resolve_target_from_context,
)


def test_resolve_relative_time_today():
    tz = "Asia/Bangkok"
    start, end = resolve_relative_time_window("hôm nay", tz)
    now_tz = datetime.now(zoneinfo.ZoneInfo(tz))
    assert start.day == now_tz.day
    assert start.hour == 0 and start.minute == 0
    assert end.hour == 23 and end.minute == 59


def test_resolve_relative_time_tomorrow():
    tz = "Asia/Bangkok"
    start, end = resolve_relative_time_window("ngày mai", tz)
    # Start của ngày mai phải sau start của hôm nay
    start_today, _ = resolve_relative_time_window("hôm nay", tz)
    assert start > start_today
    assert (start - start_today).days == 1


def test_resolve_target_single_match():
    records = [
        {"id": "ev_1", "summary": "Họp dự án Alpha"},
        {"id": "ev_2", "summary": "Gặp đối tác Beta"},
    ]
    # Tham chiếu rõ "dự án Alpha"
    match, candidates = resolve_target_from_context("cuộc họp Alpha", records)
    assert match is not None
    assert match["id"] == "ev_1"
    assert len(candidates) == 0


def test_resolve_target_multiple_matches_triggers_clarification():
    records = [
        {"id": "ev_1", "summary": "Họp chiến lược ban giám đốc"},
        {"id": "ev_2", "summary": "Họp chiến lược kinh doanh"},
    ]
    # Người dùng nói "cuộc họp chiến lược" -> trùng cả 2
    match, candidates = resolve_target_from_context("cuộc họp chiến lược", records)
    assert match is None
    assert len(candidates) == 2


def test_resolve_target_no_match():
    records = [
        {"id": "ev_1", "summary": "Họp đối tác A"},
    ]
    match, candidates = resolve_target_from_context("Họp dự án B", records)
    assert match is None
    assert len(candidates) == 0
