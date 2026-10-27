from datetime import datetime, timedelta
import zoneinfo
from typing import Any, Optional


def _parse_datetime(dt_val: str | datetime, tz: zoneinfo.ZoneInfo) -> datetime:
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=tz)
        return dt_val.astimezone(tz)
    clean_s = str(dt_val).strip().replace("Z", "+00:00")
    try:
        res = datetime.fromisoformat(clean_s)
        if res.tzinfo is None:
            return res.replace(tzinfo=tz)
        return res.astimezone(tz)
    except Exception as e:
        raise ValueError(f"Không thể phân tích ngày giờ: {dt_val}") from e


def validate_and_adjust_event_times(
    start_time: str | datetime,
    end_time: Optional[str | datetime] = None,
    existing_snapshot: Optional[dict[str, Any]] = None,
    timezone_str: str = "Asia/Bangkok",
    default_duration_minutes: int = 60,
) -> tuple[datetime, datetime]:
    """Kiểm tra và chuẩn hóa thời gian sự kiện:
    - Bắt buộc end > start; không tự đặt end == start.
    - Khi dời lịch chỉ có start mới, giữ nguyên duration từ snapshot cũ.
    """
    try:
        tz = zoneinfo.ZoneInfo(timezone_str)
    except Exception:
        tz = zoneinfo.ZoneInfo("Asia/Bangkok")

    start_dt = _parse_datetime(start_time, tz)

    if end_time is not None:
        end_dt = _parse_datetime(end_time, tz)
        if end_dt <= start_dt:
            raise ValueError(
                "Thời gian kết thúc (end_time) phải sau thời gian bắt đầu (start_time). Không tự đặt giờ kết thúc bằng giờ bắt đầu."
            )
        return start_dt, end_dt

    # Khi dời lịch chỉ có giờ bắt đầu mới, giữ thời lượng cũ từ snapshot
    if existing_snapshot:
        old_start_raw = existing_snapshot.get("start_time") or existing_snapshot.get("start")
        old_end_raw = existing_snapshot.get("end_time") or existing_snapshot.get("end")
        if old_start_raw and old_end_raw:
            try:
                old_start = _parse_datetime(old_start_raw, tz)
                old_end = _parse_datetime(old_end_raw, tz)
                old_duration = old_end - old_start
                if old_duration > timedelta(0):
                    return start_dt, start_dt + old_duration
            except Exception:
                pass

    return start_dt, start_dt + timedelta(minutes=default_duration_minutes)


def resolve_relative_time_window(
    expr: Optional[str],
    timezone_str: str = "Asia/Bangkok",
    reference_now: Optional[datetime] = None,
    allow_default_7_days: bool = False,
    half_open: Optional[bool] = None,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Tính toán khoảng thời gian (start, end) dựa trên biểu thức tiếng Việt và múi giờ người dùng.

    Hỗ trợ chuẩn nửa mở [time_min, time_max). Mặc định 7 ngày chỉ dùng khi không chỉ định thời gian.
    Biểu thức không hiểu sẽ trả về (None, None) để hỏi lại người dùng.
    """
    try:
        tz = zoneinfo.ZoneInfo(timezone_str)
    except Exception:
        tz = zoneinfo.ZoneInfo("Asia/Bangkok")

    if reference_now is not None:
        if reference_now.tzinfo is None:
            now = reference_now.replace(tzinfo=tz)
        else:
            now = reference_now.astimezone(tz)
    else:
        now = datetime.now(tz)

    if half_open is None:
        half_open = (reference_now is not None)

    if not expr or not expr.strip():
        if allow_default_7_days:
            start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz)
            end = start + timedelta(days=7)
            return start, end
        return None, None

    lower = expr.lower().strip()

    if "ngày mai" in lower or lower == "mai" or lower.startswith("mai ") or lower.endswith(" mai"):
        target_date = now.date() + timedelta(days=1)
        start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
        if half_open:
            end = start + timedelta(days=1)
        else:
            end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    if "ngày kia" in lower:
        target_date = now.date() + timedelta(days=2)
        start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
        if half_open:
            end = start + timedelta(days=1)
        else:
            end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    if "hôm qua" in lower:
        target_date = now.date() - timedelta(days=1)
        start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=tz)
        if half_open:
            end = start + timedelta(days=1)
        else:
            end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    if "hôm nay" in lower:
        start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz)
        if half_open:
            end = start + timedelta(days=1)
        else:
            end = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=tz)
        return start, end

    if "tuần này" in lower:
        start_date = now.date() - timedelta(days=now.weekday())  # Thứ Hai
        start = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, tzinfo=tz)
        if half_open:
            end = start + timedelta(days=7)
        else:
            end_date = start_date + timedelta(days=6)
            end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    if "tuần tới" in lower or "tuần sau" in lower:
        start_date = now.date() - timedelta(days=now.weekday()) + timedelta(days=7)  # Thứ Hai tuần sau
        start = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, tzinfo=tz)
        if half_open:
            end = start + timedelta(days=7)
        else:
            end_date = start_date + timedelta(days=6)
            end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=tz)
        return start, end

    # Biểu thức không hiểu: trả về (None, None) để hỏi lại người dùng, không tự chuyển thành hôm nay
    return None, None


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

    # 1. Ưu tiên khớp chính xác toàn bộ cụm từ
    exact_matches = [
        rec for rec in last_read_records
        if ref_clean in str(rec.get("summary") or rec.get("name") or rec.get("title") or rec.get("subject") or "").lower()
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], []
    if len(exact_matches) > 1:
        return None, exact_matches

    # 2. Khớp theo điểm số token (chọn ứng viên có nhiều token trùng khớp nhất)
    scored: list[tuple[int, dict[str, Any]]] = []
    for rec in last_read_records:
        title = str(rec.get("summary") or rec.get("name") or rec.get("title") or rec.get("subject") or "").lower()
        score = sum(1 for token in tokens if token in title)
        if score > 0:
            scored.append((score, rec))

    if not scored:
        return None, []

    max_score = max(s[0] for s in scored)
    best_matches = [rec for s, rec in scored if s == max_score]

    if len(best_matches) == 1:
        return best_matches[0], []
    return None, best_matches
