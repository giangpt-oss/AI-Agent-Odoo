from datetime import date

from app.business.report_local import interpret


def test_report_types_dates_and_format():
    assert interpret('Cho tôi báo cáo bán hàng tháng 10/2026 bằng Word', {}, date(2026, 11, 4)) == ('reports.create', {'report_type': 'sales', 'date_from': '2026-10-01', 'date_to': '2026-10-31', 'output_format': 'docx'})
    assert interpret('Báo cáo tồn kho hiện tại', {}, date(2026, 11, 4)) == ('reports.create', {'report_type': 'inventory'})
    assert interpret('Lấy báo cáo CRM', {}, date(2026, 11, 4)) == ('reports.create', {'report_type': 'crm'})
    assert interpret('báo cáo mua hàng tháng trước bằng Excel', {}, date(2026, 1, 2))[1]['date_from'] == '2025-12-01'


def test_period_followup_requires_actual_pending_report():
    pending = {'last_read': {'business_report': {'request': {'report_type': 'sales'}}}}
    assert interpret('từ 1/10/2026 đến 31/10/2026', pending, date.today()) == ('reports.create', {'date_from': '2026-10-01', 'date_to': '2026-10-31'})
    assert interpret('từ 1/10/2026 đến 31/10/2026', {}, date.today()) is None


def test_no_silently_dropped_filters_or_historical_inventory():
    for text in ['báo cáo bán hàng khách Nguyễn A', 'báo cáo tồn kho tháng 10/2026', 'không tạo báo cáo CRM', 'báo cáo bán hàng tháng 13/2026', 'báo cáo CRM của tôi']:
        assert interpret(text, {}, date.today()) is None


def test_status_and_format_reuse():
    state = {'last_read': {'business_report': {'report_id': 'rpt_test'}}}
    assert interpret('báo cáo xong chưa?', state, date.today()) == ('reports.status', {})
    assert interpret('xuất lại bằng Word', state, date.today()) == ('reports.file', {'output_format': 'docx'})
