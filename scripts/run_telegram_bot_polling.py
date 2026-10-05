#!/usr/bin/env python3
"""
Telegram Bot Polling Runner (Local Test Mode)
Không cần cài Ngrok hay mở port! Chạy script này là bạn có thể chat trực tiếp với Bot trên điện thoại.
"""
import sys
import os
import time
import json
import asyncio
import urllib.request
import urllib.parse
import xmlrpc.client
from pathlib import Path

# Thêm project root vào path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Đảm bảo UTF-8 cho console Windows
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Đọc file .env
ENV_FILE = ROOT_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("❌ Lỗi: TELEGRAM_BOT_TOKEN chưa được cấu hình trong file .env!")
    sys.exit(1)

ODOO_URL = os.getenv("ODOO_URL", "")
ODOO_DB = os.getenv("ODOO_DB", "")
ODOO_USER = os.getenv("ODOO_ADMIN_USERNAME") or os.getenv("ODOO_USERNAME", "")
ODOO_KEY = os.getenv("ODOO_API_KEY", "")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("DEFAULT_LLM_MODEL", "gemini-3.5-flash-lite")

API_BASE = f"https://api.telegram.org/bot{TOKEN}"

def api_call(method: str, params: dict = None) -> dict:
    url = f"{API_BASE}/{method}"
    data = None
    if params:
        data = urllib.parse.urlencode(params).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Telegram API call error ({method}): {e}")
        return {"ok": False, "error": str(e)}

class OdooHelper:
    def __init__(self):
        self.url = ODOO_URL.rstrip('/')
        if self.url.endswith('/odoo'):
            self.url = self.url[:-5]
        self.db = ODOO_DB
        self.user = ODOO_USER
        self.key = ODOO_KEY
        self.uid = None
        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

    def auth(self):
        if not self.uid:
            self.uid = self._common.authenticate(self.db, self.user, self.key, {})
        return self.uid

    def search_read(self, model: str, domain: list, fields: list, limit: int = 10, order: str = ''):
        uid = self.auth()
        kwargs = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        return self._models.execute_kw(self.db, uid, self.key, model, 'search_read', [domain], kwargs)

async def handle_user_query(text: str, user_name: str) -> str:
    """Xử lý câu hỏi từ người dùng và truy vấn Odoo Cloud."""
    query = (text or "").strip().lower()
    
    # 1. Chào hỏi
    if query in ("/start", "start", "hi", "hello", "xin chào", "chào"):
        return (
            f"👋 Xin chào {user_name}!\n\n"
            "Tôi là **Hopita AI Agent** - Trợ lý Điều hành tích hợp trực tiếp với **Odoo Cloud**.\n\n"
            "💡 Bạn có thể hỏi tôi:\n"
            "• *Tình hình cơ hội / pipeline thế nào?*\n"
            "• *Cung cấp cho tôi số nhân viên hiện có?*\n"
            "• *Danh sách khách hàng gần đây?*\n"
            "• *Báo cáo doanh số và các deal lớn?*"
        )

    # 2. Tra cứu cơ hội / Pipeline Odoo
    if any(k in query for k in ["cơ hội", "pipeline", "deal", "leads", "kinh doanh", "bán hàng"]):
        try:
            odoo = OdooHelper()
            leads = odoo.search_read(
                'crm.lead',
                domain=[],
                fields=['name', 'expected_revenue', 'probability', 'stage_id'],
                limit=10,
                order='expected_revenue desc'
            )
            if not leads:
                return "📊 Hiện tại trong CRM chưa có cơ hội (deal) nào được ghi nhận."
            
            lines = [f"📊 **BÁO CÁO CÁC CƠ HỘI KINH DOANH (ODOO CRM)**", f"Tìm thấy **{len(leads)}** cơ hội gần nhất:\n"]
            total_rev = 0
            for idx, l in enumerate(leads, 1):
                name = l.get('name') or 'Chưa đặt tên'
                rev = l.get('expected_revenue') or 0
                total_rev += rev
                prob = l.get('probability') or 0
                stage = l.get('stage_id')
                stage_name = stage[1] if isinstance(stage, (list, tuple)) and len(stage) > 1 else str(stage or 'Mới')
                lines.append(f"{idx}. **{name}**\n   💰 Doanh thu: `{rev:,.0f} VNĐ` | Xác suất: `{prob}%`\n   📌 Tiến độ: *{stage_name}*\n")
            
            lines.append(f"📈 **Tổng doanh thu dự kiến:** `{total_rev:,.0f} VNĐ`")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Lỗi khi truy vấn Odoo CRM: {e}"

    # 3. Tra cứu Khách hàng / Đối tác Odoo
    if any(k in query for k in ["khách hàng", "đối tác", "customer", "partner"]):
        try:
            odoo = OdooHelper()
            partners = odoo.search_read(
                'res.partner',
                domain=[],
                fields=['name', 'email', 'phone', 'city'],
                limit=5,
                order='id desc'
            )
            if not partners:
                return "👥 Hiện chưa có danh sách khách hàng trong Odoo."
            
            lines = [f"👥 **DANH SÁCH KHÁCH HÀNG (ODOO CONTACTS)**:\n"]
            for idx, p in enumerate(partners, 1):
                name = p.get('name') or 'N/A'
                email = p.get('email') or 'Chưa có email'
                phone = p.get('phone') or 'Chưa có SĐT'
                lines.append(f"{idx}. **{name}**\n   📧 {email} | 📞 {phone}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Lỗi khi truy vấn Khách hàng Odoo: {e}"

    # 4. Tra cứu Nhân sự / Số lượng nhân viên Odoo (HR)
    if any(k in query for k in ["nhân viên", "nhân sự", "employee", "phòng ban", "bao nhiêu người", "quân số", "người"]):
        try:
            odoo = OdooHelper()
            emps = odoo.search_read(
                'hr.employee',
                domain=[],
                fields=['name', 'job_title', 'department_id', 'work_email'],
                limit=15
            )
            if not emps:
                return "👔 Hiện tại trong Odoo HR chưa có dữ liệu nhân viên nào."
            
            lines = [f"👔 **BÁO CÁO NHÂN SỰ (ODOO HR)**", f"Tổng số nhân sự hiện có trên hệ thống: **{len(emps)} nhân viên**\n"]
            for idx, e in enumerate(emps, 1):
                name = e.get('name') or 'N/A'
                job = e.get('job_title') or 'Nhân viên'
                dept = e.get('department_id')
                dept_name = dept[1] if isinstance(dept, (list, tuple)) and len(dept) > 1 else 'Chưa phân bổ'
                lines.append(f"{idx}. **{name}**\n   💼 Chức vụ: *{job}* | 🏢 Phòng ban: *{dept_name}*")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Lỗi khi truy vấn Odoo HR: {e}"

    # 5. Tra cứu chung hoặc dùng Não AI Gemini
    if GEMINI_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_KEY)
            prompt = (
                f"Bạn là trợ lý điều hành AI của công ty Hopita, hỗ trợ trả lời câu hỏi của sếp.\n"
                f"Câu hỏi: {text}\n"
                f"Hãy trả lời ngắn gọn, lịch sự, chuyên nghiệp bằng tiếng Việt."
            )
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            if response and response.text:
                return response.text
        except Exception as e:
            print(f"Gemini generation error: {e}")

    return (
        f"🤖 Tôi đã nhận được tin nhắn: '{text}'.\n"
        "Hiện tại tôi được cấu hình chuyên sâu để tra cứu **Cơ hội kinh doanh (Pipeline)**, **Nhân sự** và **Khách hàng** từ Odoo Cloud.\n"
        "Hãy thử gõ: *'Tình hình pipeline'* hoặc *'Bao nhiêu nhân viên'* nhé!"
    )

async def main():
    print("=" * 60)
    print("🤖 ĐANG KHỞI ĐỘNG TELEGRAM BOT (POLLING MODE)...")
    print("=" * 60)
    
    # 1. Lấy thông tin bot
    me = api_call("getMe")
    if not me.get("ok"):
        print(f"❌ Không thể kết nối với Telegram API: {me.get('error')}")
        return
    
    bot_info = me["result"]
    print(f"✅ Bot Name    : {bot_info.get('first_name')}")
    print(f"✅ Bot Username: @{bot_info.get('username')}")
    print(f"✅ Odoo Target : {ODOO_URL}")
    print(f"✅ AI Provider : Google Gemini ({GEMINI_MODEL})")
    print("-" * 60)
    print("⚡ BOT ĐANG CHẠY TRỰC TIẾP! BẠN CÓ THỂ MỞ TELEGRAM VÀ CHAT NGAY BÂY GIỜ.")
    print("👉 Tìm bot: @" + bot_info.get('username') + " trên Telegram và gõ /start")
    print("Nhấn Ctrl + C để dừng.")
    print("=" * 60)

    # 2. Xóa webhook cũ để dùng polling
    api_call("deleteWebhook")

    offset = 0
    while True:
        try:
            updates = api_call("getUpdates", {"offset": offset, "timeout": 10})
            if updates.get("ok"):
                for item in updates.get("result", []):
                    offset = item["update_id"] + 1
                    msg = item.get("message")
                    if not msg or "text" not in msg:
                        continue
                    
                    chat_id = msg["chat"]["id"]
                    user_name = msg["from"].get("first_name") or msg["from"].get("username") or "Sếp"
                    text = msg.get("text", "")

                    print(f"\n📩 [Tin nhắn mới từ {user_name} (Chat ID: {chat_id})]: {text}")
                    
                    # Báo trạng thái 'đang gõ'
                    api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
                    
                    # Xử lý câu trả lời
                    reply = await handle_user_query(text, user_name)
                    
                    # Gửi câu trả lời
                    api_call("sendMessage", {
                        "chat_id": chat_id,
                        "text": reply,
                        "parse_mode": "Markdown"
                    })
                    print(f"📤 [Đã trả lời]:\n{reply[:100]}...")
            
            await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Đã dừng Bot Telegram.")
            break
        except Exception as e:
            print(f"Polling loop error: {e}")
            await asyncio.sleep(2)

if __name__ == '__main__':
    asyncio.run(main())
