#!/usr/bin/env python3
"""
Script kiểm tra kết nối tới Odoo Cloud ERP cho dự án Unified AI-Agent.
Cách chạy từ thư mục gốc:
    .\.venv\Scripts\python.exe scripts/test_odoo_connection.py
"""
import sys
import os
import asyncio
from pathlib import Path

# Hỗ trợ hiển thị tiếng Việt trên Windows console
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Thêm thư mục gốc vào PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.connectors.odoo.client import OdooAsyncClient

async def test_connection():
    settings = get_settings()
    print("=" * 60)
    print("🔍 ĐANG KIỂM TRA CẤU HÌNH VÀ KẾT NỐI ODOO CLOUD (UNIFIED PROJECT)...")
    print("=" * 60)
    
    print(f"📍 ODOO_URL      : {settings.ODOO_URL or '(Chưa cấu hình)'}")
    print(f"📍 ODOO_DB       : {settings.ODOO_DB or '(Chưa cấu hình)'}")
    print(f"📍 ODOO_USER     : {settings.odoo_user or '(Chưa cấu hình)'}")
    print(f"📍 ODOO_API_KEY  : {'*' * 8 + settings.ODOO_API_KEY[-4:] if len(settings.ODOO_API_KEY) > 4 else '(Chưa cấu hình)'}")
    print("-" * 60)

    # 1. Kiểm tra biến môi trường
    missing = []
    if not settings.ODOO_URL: missing.append("ODOO_URL")
    if not settings.ODOO_DB: missing.append("ODOO_DB")
    if not settings.odoo_user: missing.append("ODOO_USERNAME / ODOO_ADMIN_USERNAME")
    if not settings.ODOO_API_KEY: missing.append("ODOO_API_KEY")

    if missing:
        print(f"❌ THIẾU THÔNG TIN: Vui lòng điền các biến sau vào file .env: {', '.join(missing)}")
        return False

    try:
        odoo = OdooAsyncClient(
            base_url=settings.ODOO_URL,
            db=settings.ODOO_DB,
            username=settings.odoo_user,
            api_key=settings.ODOO_API_KEY,
        )
        
        # 2. Kiểm tra xác thực (Authentication)
        print("⏳ Bước 1: Đang xác thực tài khoản qua Odoo JSON-RPC...")
        uid = await odoo.authenticate()
        print(f"✅ Xác thực thành công! User ID (UID) trong Odoo: {uid}")

        # 3. Kiểm tra quyền truy vấn dữ liệu CRM (crm.lead)
        print("⏳ Bước 2: Đang kiểm tra quyền đọc CRM (crm.lead)...")
        leads = await odoo.execute_kw(
            model='crm.lead',
            method='search_read',
            args=[[['type', '=', 'opportunity']]],
            kwargs={
                'fields': ['name', 'expected_revenue', 'stage_id'],
                'limit': 3
            }
        )
        print(f"✅ Truy vấn CRM thành công! Tìm thấy mẫu {len(leads)} cơ hội (opportunities).")
        for i, lead in enumerate(leads, 1):
            rev = lead.get('expected_revenue') or 0
            stage = lead.get('stage_id')
            stage_name = stage[1] if isinstance(stage, (list, tuple)) and len(stage) > 1 else str(stage)
            print(f"   [{i}] {lead.get('name')} | Doanh thu: {rev:,.0f} | Giai đoạn: {stage_name}")

        # 4. Kiểm tra quyền truy vấn Khách hàng (res.partner)
        print("⏳ Bước 3: Đang kiểm tra quyền đọc Khách hàng (res.partner)...")
        partners = await odoo.execute_kw(
            model='res.partner',
            method='search_read',
            args=[[['customer_rank', '>', 0]]],
            kwargs={
                'fields': ['name', 'email'],
                'limit': 2
            }
        )
        print(f"✅ Truy vấn Khách hàng thành công! Tìm thấy {len(partners)} khách hàng mẫu.")

        # 5. Kiểm tra quyền truy vấn Nhân viên (hr.employee)
        print("⏳ Bước 4: Đang kiểm tra quyền đọc Nhân sự (hr.employee)...")
        employees = await odoo.execute_kw(
            model='hr.employee',
            method='search_read',
            args=[[]],
            kwargs={
                'fields': ['name', 'work_email', 'job_title'],
                'limit': 3
            }
        )
        print(f"✅ Truy vấn Nhân sự thành công! Tìm thấy {len(employees)} nhân viên mẫu.")

        print("=" * 60)
        print("🎉 KẾT NỐI ODOO CLOUD HOÀN TOÀN SẴN SÀNG ĐỂ CHẠY HỆ THỐNG AI-AGENT!")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n❌ KẾT NỐI THẤT BẠI: {e}")
        print("\n💡 GỢI Ý KHẮC PHỤC:")
        print("1. Kiểm tra lại ODOO_URL (phải là URL gốc như https://congty.odoo.com, không có /odoo hay /web ở cuối)")
        print("2. ODOO_DB chính là tên database (thường là subdomain, ví dụ congty)")
        print("3. ODOO_API_KEY phải là API Key được tạo trong phần 'Account Security', không phải mật khẩu đăng nhập thông thường")
        print("4. Đảm bảo tài khoản Odoo có quyền truy cập vào phân hệ CRM và Khách hàng.")
        return False

if __name__ == '__main__':
    asyncio.run(test_connection())
