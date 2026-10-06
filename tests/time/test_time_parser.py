import pytest
from app.services.time_parser import time_parser
from datetime import datetime
import zoneinfo

def test_parse_iso_time():
    res = time_parser.parse_semantic_time("2026-10-15T15:00:00Z", "Asia/Ho_Chi_Minh")
    assert res["is_ambiguous"] is False
    assert res["timezone"] == "UTC"
    dt = datetime.fromisoformat(res["datetime"])
    assert dt.year == 2026
    
def test_parse_semantic_fallback():
    res = time_parser.parse_semantic_time("chiều mai", "Asia/Ho_Chi_Minh")
    assert res["is_ambiguous"] is True
    assert res["confidence"] == 0.0

def test_normalize_timezone():
    dt = datetime(2026, 10, 15, 10, 0, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
    local_dt = time_parser.normalize_timezone(dt, "Asia/Ho_Chi_Minh")
    assert local_dt.hour == 17
