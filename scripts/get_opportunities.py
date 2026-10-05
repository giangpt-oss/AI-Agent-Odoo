#!/usr/bin/env python3
"""
CLI Script truy vấn cơ hội kinh doanh (crm.lead) theo tháng từ Odoo Cloud.
Cách chạy:
    .\.venv\Scripts\python.exe scripts/get_opportunities.py --months 1,2,3 --year 2026
"""
import sys
import os
import asyncio
import json
from datetime import date
from pathlib import Path

# Hỗ trợ tiếng Việt trên Windows console
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Thêm thư mục gốc vào PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.connectors.odoo.client import OdooAsyncClient


def month_date_range(year: int, month: int):
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


async def fetch_for_months(months, year=None, limit=0):
    year = year or date.today().year
    settings = get_settings()
    odoo = OdooAsyncClient(
        base_url=settings.ODOO_URL,
        db=settings.ODOO_DB,
        username=settings.odoo_user,
        api_key=settings.ODOO_API_KEY,
    )
    results = {}
    for m in months:
        start, end = month_date_range(year, m)
        domain = [['type', '=', 'opportunity'], ['date_deadline', '>=', start], ['date_deadline', '<', end]]
        fields = ['name', 'partner_id', 'expected_revenue', 'probability', 'stage_id', 'date_deadline']
        rows = await odoo.execute_kw(
            model='crm.lead',
            method='search_read',
            args=[domain],
            kwargs={'fields': fields, 'limit': limit, 'order': 'expected_revenue desc'}
        )
        results[str(m)] = rows
    return results


def main():
    import argparse
    p = argparse.ArgumentParser(description="Lấy danh sách cơ hội CRM Odoo theo tháng")
    p.add_argument('--months', default='1,2,3,4,5,6,7,8,9,10,11,12', help='Danh sách tháng cách nhau bởi dấu phẩy')
    p.add_argument('--year', type=int, default=None)
    p.add_argument('--limit', type=int, default=10)
    args = p.parse_args()
    months = [int(x) for x in args.months.split(',') if x.strip()]

    try:
        data = asyncio.run(fetch_for_months(months, year=args.year, limit=args.limit))
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        print('Error:', e)


if __name__ == '__main__':
    main()
