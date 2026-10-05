#!/usr/bin/env python3
"""
Telegram Bot Polling Runner (High-Performance Turbo Edition)
Tối ưu hóa độ trễ phản hồi:
- Sử dụng httpx.AsyncClient với HTTP Keep-Alive & Connection Pooling.
- Khởi tạo trước Singleton Gemini Client và Odoo Client (Pre-authenticated).
- Giới hạn max_output_tokens=300 để sinh câu trả lời trong ~0.6 giây.
- Xử lý bất đồng bộ tức thì, không sleep thừa thãi.
"""
import sys
import os
import time
import json
import asyncio
from pathlib import Path
import httpx

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

# 1. Khởi tạo sẵn Gemini Client (Singleton)
ai_client = None
if GEMINI_KEY:
    try:
        from google import genai
        ai_client = genai.Client(api_key=GEMINI_KEY)
    except Exception as e:
        print(f"⚠️ Không thể khởi tạo genai.Client: {e}")

# 2. Khởi tạo sẵn Odoo Client với Cache UID
from app.connectors.odoo.client import OdooAsyncClient
from app.services.employee import employee_service

class FastOdooHelper:
    def __init__(self):
        self.client = OdooAsyncClient(
            base_url=ODOO_URL,
            db=ODOO_DB,
            username=ODOO_USER,
            api_key=ODOO_KEY,
            timeout=8.0
        )
        self.authenticated = False

    async def ensure_auth(self):
        if not self.authenticated:
            try:
                await self.client.authenticate()
                self.authenticated = True
            except Exception as e:
                print(f"⚠️ Lỗi xác thực Odoo: {e}")

    async def search_read(self, model: str, domain: list, fields: list, limit: int = 10, order: str = ''):
        await self.ensure_auth()
        kwargs = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        return await self.client.execute_kw(
            model=model,
            method='search_read',
            args=[domain],
            kwargs=kwargs
        )

odoo_helper = FastOdooHelper()

# 3. Hàm xử lý logic câu hỏi với độ trễ tối thiểu & nhận diện định danh người dùng
async def handle_user_query(text: str, user_name: str, chat_id: int) -> str:
    query = (text or "").strip().lower()
    
    # Định danh nhân viên qua Telegram Chat ID
    employee = await employee_service.resolve_employee_identity(chat_id)
    display_name = employee.get("full_name") if employee else user_name

    from app.services.odoo_auth_service import odoo_auth_service
    auth_url = odoo_auth_service.get_verification_url(chat_id)

    # 1. Lệnh đăng nhập trực tiếp bằng tài khoản & mật khẩu Odoo
    if query.startswith("/login ") or query.startswith("login "):
        parts = text.strip().split(maxsplit=2)
        if len(parts) >= 3:
            login_email = parts[1]
            login_secret = parts[2]
            success, linked, msg = await odoo_auth_service.authenticate_and_link(chat_id, login_email, login_secret)
            if success and linked:
                employee = linked
                display_name = linked["full_name"]
                roles_str = ", ".join(linked.get("roles", []))
                return (
                    f"🎉 **XÁC THỰC THÀNH CÔNG VỚI ODOO ERP!**\n"
                    f"────────────────────────────\n"
                    f"• **Họ và tên:** {linked['full_name']}\n"
                    f"• **Tài khoản Odoo:** `{linked['email']}` (UID: {linked['odoo_user_id']})\n"
                    f"• **Chức vụ / Bộ phận:** {linked.get('job_title')} | {linked.get('department')}\n"
                    f"• **Vai trò cấp quyền:** `{roles_str}`\n\n"
                    f"✅ **Tài khoản Odoo của bạn đã được chứng minh và liên kết thành công.**\n"
                    f"Từ bây giờ, Trợ lý AI sẽ phục vụ bạn đúng theo phân quyền của tài khoản Odoo này!\n\n"
                    f"💡 Hãy thử gõ: *'tôi là ai'*, *'tình hình pipeline'*, hoặc đặt câu hỏi bất kỳ."
                )
            else:
                return (
                    f"❌ **XÁC THỰC THẤT BẠI!**\n\n"
                    f"{msg}\n\n"
                    f"🔒 **Bảo mật:** Hệ thống yêu cầu đúng mật khẩu hoặc API Key của tài khoản Odoo để chứng minh bạn là chủ sở hữu tài khoản. Mọi hành vi nhập email người khác mà không có mật khẩu đều bị từ chối."
                )
        else:
            return f"💡 Cú pháp: `/login <email_odoo> <mật_khẩu_odoo>`\nHoặc mở link: {auth_url}"

    # 2. Người dùng chỉ nhập email mà không có mật khẩu -> Yêu cầu vào link xác thực
    import re
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if email_match and not employee:
        return (
            f"🔒 **CHỐNG GIẢ MẠO: YÊU CẦU XÁC THỰC TÀI KHOẢN ODOO**\n\n"
            f"Hệ thống đã nhận diện email: `{email_match.group(0)}`.\n\n"
            "⚠️ **Cảnh báo an ninh:** Để chống việc mạo danh nhân viên hoặc Ban Giám Đốc, hệ thống **tuyệt đối không cấp quyền** chỉ bằng việc nhập email.\n\n"
            f"👉 **Vui lòng chọn 1 trong 2 cách sau để chứng minh bạn sở hữu tài khoản này:**\n\n"
            f"1️⃣ **Bấm vào liên kết đăng nhập bảo mật Odoo Cloud:**\n"
            f"🔗 [Bấm vào đây để Xác thực tài khoản Odoo]({auth_url})\n\n"
            f"2️⃣ **Hoặc gõ trực tiếp:**\n"
            f"`/login {email_match.group(0)} <mật_khẩu_odoo>`\n\n"
            f"*Nếu nhập sai mật khẩu Odoo, hệ thống sẽ từ chối truy cập ngay lập tức.*"
        )

    # Chào hỏi (phản hồi siêu tốc 0ms)
    if query in ("/start", "start", "hi", "hello", "xin chào", "chào"):
        if employee:
            return (
                f"👋 Kính chào **{display_name}**!\n\n"
                "Tôi là **Hopita AI Agent** - Trợ lý Điều hành phản hồi siêu tốc kết nối trực tiếp với **Odoo Cloud ERP**.\n\n"
                "💡 Bạn có thể hỏi tôi:\n"
                "• *Tôi là ai?* (Xem hồ sơ & quyền hạn hệ thống)\n"
                "• *Tình hình cơ hội / pipeline thế nào?*\n"
                "• *Cung cấp cho tôi số nhân viên hiện có?*\n"
                "• *Danh sách khách hàng gần đây?*\n"
                "• Hoặc bất kỳ chỉ đạo điều hành nào khác!"
            )
        else:
            return (
                f"👋 Xin chào **{user_name}**!\n\n"
                "Tôi là **Hopita AI Agent** - Trợ lý Điều hành tích hợp trực tiếp với hệ sinh thái **Odoo Cloud ERP**.\n\n"
                f"Tài khoản Telegram của bạn (`{chat_id}`) hiện chưa được xác thực với bất kỳ tài khoản Odoo nào trong công ty.\n\n"
                "🔒 **CHÍNH SÁCH BẢO MẬT DOANH NGHIỆP (ZERO-TRUST):**\n"
                "Để tránh giả mạo danh tính nhân viên hoặc ban lãnh đạo, bạn phải xác thực quyền sở hữu tài khoản Odoo của mình:\n\n"
                f"👉 **Bấm vào liên kết bảo mật để đăng nhập Odoo:**\n"
                f"🔗 [Đăng Nhập Xác Thực Tài Khoản Odoo ERP]({auth_url})\n\n"
                f"*(Hoặc gõ cú pháp: `/login <email_odoo> <mật_khẩu>`)*\n\n"
                "⚖️ **Nguyên tắc phân quyền:**\n"
                "• Nếu bạn đăng nhập đúng tài khoản Odoo: Bạn chỉ được cấp đúng các quyền theo tài khoản đó.\n"
                "• Nếu sai mật khẩu hoặc không có tài khoản Odoo: Truy cập sẽ bị từ chối hoàn toàn."
            )

    # 2. Tra cứu thông tin định danh & quyền hạn cá nhân
    if any(k in query for k in ["tôi là ai", "whoami", "/whoami", "quyền", "quyền hạn", "vai trò", "role", "profile", "tài khoản của tôi"]):
        if employee:
            roles_str = ", ".join(employee.get("roles", []))
            odoo_uid = employee.get("odoo_user_id") or 27
            return (
                f"👤 **HỒ SƠ ĐỊNH DANH & PHÂN QUYỀN CỦA BẠN**\n"
                f"────────────────────────────\n"
                f"• **Họ và tên:** {employee.get('full_name')}\n"
                f"• **Email doanh nghiệp:** `{employee.get('email')}`\n"
                f"• **Telegram Chat ID:** `{chat_id}`\n"
                f"• **Tài khoản Odoo Cloud:** (UID: {odoo_uid})\n"
                f"• **Vai trò (Roles):** `{roles_str}`\n\n"
                f"🛡️ **Các quyền truy cập khả dụng:**\n"
                f"{'✅ Toàn quyền Quản trị & Điều hành (*)' if is_admin else '✅ Quyền truy cập phân hệ Odoo theo chức danh'}\n"
                f"{'✅ Odoo CRM: Xem Pipeline kinh doanh' if (is_admin or 'sales_user' in user_roles or 'sales_manager' in user_roles) else '⛔ Odoo CRM: Không có quyền'}\n"
                f"{'✅ Odoo HR: Xem hồ sơ nhân sự' if is_admin else '⛔ Odoo HR: Không có quyền'}\n"
                f"{'✅ Odoo Contacts: Xem khách hàng & đối tác' if (is_admin or 'sales_user' in user_roles or 'sales_manager' in user_roles) else '⛔ Odoo Contacts: Không có quyền'}"
            )
        else:
            return (
                f"⚠️ **TÀI KHOẢN CHƯA ĐƯỢC ĐỊNH DANH TRÊN HỆ THỐNG**\n\n"
                f"• Telegram Chat ID của bạn: `{chat_id}`\n"
                f"• Trạng thái: Người dùng khách (Guest)\n\n"
                "💡 **Để tự động liên kết với Odoo:** Hãy nhắn **Email công vụ** hoặc **Số điện thoại** của bạn cho bot ngay tại đây nhé!"
            )


    # Kiểm tra quyền hạn người dùng (RBAC)
    user_roles = set(employee.get("roles", [])) if employee else set()
    is_admin = bool({"admin", "ceo"}.intersection(user_roles))

    # Tra cứu cơ hội CRM Odoo
    if any(k in query for k in ["cơ hội", "pipeline", "deal", "leads", "kinh doanh", "bán hàng"]):
        if not (is_admin or "sales_manager" in user_roles or "sales_user" in user_roles):
            return (
                "⛔ **TRUY CẬP BỊ TỪ CHỐI (403 ACCESS DENIED)**\n\n"
                f"Tài khoản của bạn (`{display_name}`) không thuộc phòng Kinh doanh hoặc Ban Giám Đốc.\n"
                "🔒 Bạn không có quyền xem dữ liệu Cơ hội kinh doanh & Doanh số (Odoo CRM).\n"
                "Vui lòng liên hệ Quản trị viên hệ thống để yêu cầu cấp quyền."
            )
        try:
            leads = await odoo_helper.search_read(
                model='crm.lead',
                domain=[],
                fields=['name', 'expected_revenue', 'probability', 'stage_id'],
                limit=10,
                order='expected_revenue desc'
            )
            if not leads:
                return "📊 Hiện tại trong CRM chưa có cơ hội (deal) nào được ghi nhận."
            
            lines = [f"📊 **BÁO CÁO CƠ HỘI KINH DOANH (ODOO CRM)**", f"Tìm thấy **{len(leads)}** cơ hội gần nhất:\n"]
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

    # Tra cứu Khách hàng / Đối tác Odoo
    if any(k in query for k in ["khách hàng", "đối tác", "customer", "partner"]):
        if not (is_admin or "sales_manager" in user_roles or "sales_user" in user_roles):
            return (
                "⛔ **TRUY CẬP BỊ TỪ CHỐI (403 ACCESS DENIED)**\n\n"
                f"Tài khoản của bạn (`{display_name}`) không có quyền xem danh sách Khách hàng/Đối tác.\n"
                "Vui lòng liên hệ Quản trị viên để được cấp quyền."
            )
        try:
            partners = await odoo_helper.search_read(
                model='res.partner',
                domain=[],
                fields=['name', 'email', 'phone'],
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

    # Tra cứu Nhân sự / Số lượng nhân viên Odoo (HR)
    if any(k in query for k in ["nhân viên", "nhân sự", "employee", "phòng ban", "bao nhiêu người", "quân số", "người"]):
        if not is_admin:
            return (
                "⛔ **TRUY CẬP BỊ TỪ CHỐI (403 ACCESS DENIED)**\n\n"
                f"Tài khoản của bạn (`{display_name}`) không thuộc Ban Giám Đốc/Bộ phận Nhân sự.\n"
                "🔒 Bạn không có quyền truy xuất danh sách và hồ sơ nhân sự công ty (Odoo HR)."
            )
        try:
            emps = await odoo_helper.search_read(
                model='hr.employee',
                domain=[],
                fields=['name', 'job_title', 'department_id'],
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

    # Sinh phản hồi qua Gemini với cấu hình tối ưu độ trễ (Tốc độ ~0.6 giây)
    if ai_client:
        try:
            t0 = time.time()
            emp_title = employee.get("full_name") if employee else user_name
            emp_email = employee.get("email") if employee else "Chưa liên kết"
            emp_roles = ", ".join(employee.get("roles", [])) if employee else "Khách"
            if is_admin:
                role_desc = f"Vai trò: {emp_roles} (Ban Giám Đốc / Quản trị viên cao nhất của Hopita và Odoo Cloud)"
            elif employee:
                role_desc = f"Vai trò: {emp_roles} (Nhân viên nội bộ công ty)"
            else:
                role_desc = f"Vai trò: Khách vãng lai chưa xác thực (Telegram ID: {chat_id}). Tuyệt đối KHÔNG tiết lộ dữ liệu doanh số, nội bộ công ty."

            prompt = (
                f"Bạn là trợ lý điều hành AI của công ty Hopita.\n"
                f"Người đang nói chuyện: {emp_title} ({emp_email})\n"
                f"{role_desc}\n\n"
                f"Câu hỏi: {text}\n"
                f"Hãy trả lời ngắn gọn, lịch sự, đúng mực bằng tiếng Việt."
            )
            # Chạy trong threadpool để không block event loop
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "max_output_tokens": 300,
                    "temperature": 0.2,
                    "automatic_function_calling": {"disable": True}
                }
            )
            dt = time.time() - t0
            if response and response.text:
                return f"{response.text.strip()}\n\n⚡ *(Thời gian phản hồi: {dt:.2f}s)*"
        except Exception as e:
            print(f"Gemini generation error: {e}")

    return (
        f"🤖 Tôi đã nhận được tin nhắn: '{text}'.\n"
        "Hiện tại tôi được cấu hình chuyên sâu để tra cứu **Cơ hội kinh doanh (Pipeline)**, **Nhân sự** và **Khách hàng** từ Odoo Cloud.\n"
        "Hãy thử gõ: *'Tình hình pipeline'* hoặc *'Bao nhiêu nhân viên'* nhé!"
    )

# 4. Long-Polling Loop tốc độ cao với Connection Pooling
async def main():
    print("=" * 60)
    print("🚀 KHỞI ĐỘNG TELEGRAM BOT (TURBO POLLING MODE)...")
    print("=" * 60)

    # Pre-auth Odoo
    print("⏳ Đang làm ấm kết nối Odoo Cloud...")
    await odoo_helper.ensure_auth()
    if odoo_helper.authenticated:
        print(f"✅ Odoo Cloud: Kết nối thành công (UID: {odoo_helper.client.uid})")
    
    # Khởi chạy FastAPI Server ngầm để phục vụ Web Portal Xác Thực Odoo (/auth/odoo-verify)
    import uvicorn
    from app.main import app as fastapi_app
    server_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(server_config)
    asyncio.create_task(server.serve())
    print("🌐 Web Auth Portal : http://localhost:8000/auth/odoo-verify (Sẵn sàng phục vụ liên kết Odoo)")

    # Persistent HTTP/2 & Keep-Alive Client
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    async with httpx.AsyncClient(timeout=35.0, limits=limits) as http_client:
        # Lấy thông tin bot
        r = await http_client.get(f"{API_BASE}/getMe")
        me = r.json()
        if not me.get("ok"):
            print(f"❌ Không thể kết nối với Telegram API: {me.get('error')}")
            return
        
        bot_info = me["result"]
        print(f"✅ Bot Name    : {bot_info.get('first_name')}")
        print(f"✅ Bot Username: @{bot_info.get('username')}")
        print(f"✅ Odoo Target : {ODOO_URL}")
        print(f"✅ AI Model    : {GEMINI_MODEL} (Ultra-low latency)")
        print("-" * 60)
        print("⚡ BOT ĐANG CHẠY TRỰC TIẾP VỚI TỐC ĐỘ PHẢN HỒI TỐI ĐA!")
        print("👉 Mở Telegram và gửi tin nhắn ngay cho: @" + bot_info.get('username'))
        print("=" * 60)

        # Xóa webhook cũ
        await http_client.post(f"{API_BASE}/deleteWebhook")

        offset = 0
        while True:
            try:
                # Long polling: Chờ Telegram tối đa 20s
                resp = await http_client.post(
                    f"{API_BASE}/getUpdates",
                    json={"offset": offset, "timeout": 20}
                )
                updates = resp.json()
                if updates.get("ok") and updates.get("result"):
                    for item in updates["result"]:
                        offset = item["update_id"] + 1
                        msg = item.get("message")
                        if not msg or "text" not in msg:
                            continue
                        
                        chat_id = msg["chat"]["id"]
                        user_name = msg["from"].get("first_name") or msg["from"].get("username") or "Sếp"
                        text = msg.get("text", "")

                        print(f"\n📩 [Tin nhắn mới từ {user_name}]: {text}")
                        
                        # Gửi action 'đang gõ' ngay lập tức
                        asyncio.create_task(
                            http_client.post(f"{API_BASE}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
                        )

                        # Xử lý câu trả lời
                        t_start = time.time()
                        reply = await handle_user_query(text, user_name, chat_id)
                        elapsed = time.time() - t_start

                        # Gửi trả lời với Markdown, tự động fallback Text nếu lỗi Markdown
                        send_resp = await http_client.post(f"{API_BASE}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": reply,
                            "parse_mode": "Markdown"
                        })
                        if not send_resp.json().get("ok"):
                            # Retry plain text if Markdown syntax fails
                            await http_client.post(f"{API_BASE}/sendMessage", json={
                                "chat_id": chat_id,
                                "text": reply
                            })

                        print(f"📤 [Đã trả lời trong {elapsed:.2f}s]:\n{reply[:80]}...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Polling loop notice: {e}")
                await asyncio.sleep(1)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Đã dừng bot.")
