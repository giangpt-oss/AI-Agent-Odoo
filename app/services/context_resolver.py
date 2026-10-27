from datetime import datetime, timedelta
import zoneinfo
from typing import Any, Optional


def resolve_relative_time_window(expr: str, timezone_str: str = "Asia/Bangkok") -> tuple[datetime, datetime]:
    """Tính toán khoảng thời gian (start, end) dựa trên biểu thức tiếng Việt và múi giờ người dùng."""
    try:
        tz = zoneinfo.ZoneInfo(timezone_str)
    except Exception:
        tz = zoneinfo.ZoneInfo("Asia/Bangkok")

    now = datetime.now(tz)
    lower = expr.lower().strip()

    if "ngày mai" in lower or "mai" in lower:
        target_date = now.date() + timedelta(days=1)
        start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
        end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    if "ngày kia" in lower:
        target_date = now.date() + timedelta(days=2)
        start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
        end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    if "tuần này" in lower:
        start_date = now.date() - timedelta(days=now.weekday())
        end_date = start_date + timedelta(days=6)
        start = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, tzinfo=tz)
        end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    if "tuần tới" in lower or "tuần sau" in lower:
        start_date = now.date() - timedelta(days=now.weekday()) + timedelta(days=7)
        end_date = start_date + timedelta(days=6)
        start = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, tzinfo=tz)
        end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    # Mặc định là hôm nay
    start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz)
    end = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=tz)
    return start, end


def resolve_target_from_context(
    reference: str,
    last_read_records: list[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """Tìm đối tượng được tham chiếu trong danh sách các record vừa đọc.
    
    Returns:
    - (matched_record, []): nếu tìm thấy chính xác 1 đối tượng.
    - (None, candidate_records): nếu có nhiều hơn 1 đối tượng phù hợp (cần clarify).
    - (None, []): nếu không tìm thấy đối tượng nào.
    """
    if not last_read_records:
        return None, []

    ref_clean = reference.lower().strip()
    stopwords = {
        "cuộc họp", "họp", "lịch", "sự kiện", "email", "thư", "cơ hội",
        "lead", "công việc", "task", "đó", "vừa rồi", "này", "với", "cho", "của", "tại", "vào"
    }
    for sw in sorted(stopwords, key=len, reverse=True):
        ref_clean = ref_clean.replace(sw, " ").strip()

    tokens = [t for t in ref_clean.split() if len(t) > 1 and t not in stopwords]
    if not tokens:
        # Nếu người dùng chỉ nói "cuộc họp đó", "email đó" không kèm tên cụ thể
        # Nếu có duy nhất 1 record gần nhất thì chọn, nhiều hơn thì clarify
        if len(last_read_records) == 1:
            return last_read_records[0], []
        return None, last_read_records

    matches: list[dict[str, Any]] = []
    for rec in last_read_records:
        title = str(rec.get("summary") or rec.get("name") or rec.get("title") or rec.get("subject") or "").lower()
        if ref_clean in title or any(token in title for token in tokens):
            matches.append(rec)

    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, matches
    return None, []
