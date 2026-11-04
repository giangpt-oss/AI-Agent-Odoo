"""Exact, conservative report requests remain usable during AI provider outages."""
import calendar
import re
from datetime import date


def interpret(text, session, today):
    value = text.strip().rstrip('.!?').strip().casefold()
    previous = (session.get('last_read') or {}).get('business_report') or {}
    if re.fullmatch(r'(?:báo cáo )(?:xong chưa|đến đâu rồi|trạng thái|tiến độ)', value):
        return ('reports.status', {})
    output_format = None
    match = re.search(r'(?: bằng| dạng| file) (word|docx|excel|xlsx|cả word và excel|cả hai)$', value)
    if match:
        output_format = {'word': 'docx', 'docx': 'docx', 'excel': 'xlsx', 'xlsx': 'xlsx'}.get(match[1], 'both')
        value = value[:match.start()].strip()
    if previous.get('report_id') and output_format and value in {'xuất lại', 'gửi lại', 'đổi sang', 'cho tôi'}:
        return ('reports.file', {'output_format': output_format})
    report = re.fullmatch(r'(?:(?:cho tôi|tạo|lập|viết|xuất|lấy|xem)(?: cho tôi)? )?báo cáo (bán hàng|doanh thu|báo giá|mua hàng|rfq|crm|liên hệ|tồn kho|nhập xuất kho)(.*)', value)
    params = {}
    if report:
        params['report_type'] = {'bán hàng': 'sales', 'doanh thu': 'revenue', 'báo giá': 'quotations', 'mua hàng': 'purchases', 'rfq': 'purchase_rfqs', 'crm': 'crm', 'liên hệ': 'contacts', 'tồn kho': 'inventory', 'nhập xuất kho': 'transfers'}[report[1]]
        remainder = report[2].strip()
    elif previous.get('request') and not previous.get('report_id'):
        remainder = value
    else:
        return None
    if output_format:
        params['output_format'] = output_format
    if not remainder or remainder in {'hiện tại', 'hiện trạng'}:
        return ('reports.create', params) if report else None
    month = re.fullmatch(r'tháng (\d{1,2})(?:/| năm )(\d{4})', remainder)
    try:
        if month or remainder in {'tháng này', 'tháng trước'}:
            year, month_number = (int(month[2]), int(month[1])) if month else (today.year, today.month)
            if remainder == 'tháng trước':
                month_number -= 1
                if month_number == 0:
                    year, month_number = year - 1, 12
            start = date(year, month_number, 1)
            end = date(year, month_number, calendar.monthrange(year, month_number)[1])
        else:
            period = re.fullmatch(r'(?:từ )?(\d{1,2})/(\d{1,2})/(\d{4})(?: đến |\s*[–-]\s*)(\d{1,2})/(\d{1,2})/(\d{4})', remainder)
            if not period:
                return None
            start, end = date(int(period[3]), int(period[2]), int(period[1])), date(int(period[6]), int(period[5]), int(period[4]))
        if start > end or params.get('report_type', previous.get('request', {}).get('report_type')) == 'inventory':
            return None
    except ValueError:
        return None
    params.update(date_from=start.isoformat(), date_to=end.isoformat())
    return ('reports.create', params)
