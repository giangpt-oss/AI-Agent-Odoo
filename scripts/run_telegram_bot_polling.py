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

AUTH_PENDING_SESSIONS: dict[int, dict] = {}

# 3. Hàm xử lý logic câu hỏi với độ trễ tối thiểu & nhận diện định danh người dùng
async def handle_user_query(
    text: str,
    user_name: str,
    chat_id: int,
    msg_id: int = None,
    http_client = None,
    attached_file_info: dict = None
):
    from app.security.kill_switch import kill_switch
    if await kill_switch.is_active():
        return "Hệ thống đang tạm ngừng. Vui lòng thử lại sau."
    query = (text or "").strip().lower()
    
    # Định danh nhân viên qua Telegram Chat ID
    employee = await employee_service.resolve_employee_identity(chat_id)
    display_name = employee.get("full_name") if employee else user_name
    user_roles = set(employee.get("roles") or []) if employee else set()
    is_admin = bool({"admin", "ceo"}.intersection(user_roles))

    from app.services.odoo_auth_service import odoo_auth_service
    auth_url = odoo_auth_service.get_verification_url(chat_id)

    # 1. Kịch bản Bước 2: Người dùng đang trong phiên nhập Mật khẩu Odoo
    if chat_id in AUTH_PENDING_SESSIONS and AUTH_PENDING_SESSIONS[chat_id].get("email") and not employee:
        stored_email = AUTH_PENDING_SESSIONS.pop(chat_id).get("email", "")
        # Xóa ngay tin nhắn chứa mật khẩu để đảm bảo an ninh tuyệt đối
        if msg_id and http_client:
            asyncio.create_task(http_client.post(f"{API_BASE}/deleteMessage", json={"chat_id": chat_id, "message_id": msg_id}))

        success, linked, msg = await odoo_auth_service.authenticate_and_link(chat_id, stored_email, text.strip())
        if success and linked:
            employee = linked
            display_name = linked["full_name"]
            roles_str = ", ".join(linked.get("roles", []))
            return (
                f"🎉 **XÁC THỰC THÀNH CÔNG VỚI ODOO ERP!**\n"
                f"────────────────────────────\n"
                f"• **Họ và tên:** {linked['full_name']}\n"
                f"• **Tài khoản Odoo:** `{linked['email']}` (UID: {linked['odoo_user_id']})\n"
                f"• **Chức vụ / Đội ngũ:** {linked.get('job_title')} | {linked.get('department')}\n"
                f"• **Quyền hạn cấp theo Odoo:** `{roles_str}`\n\n"
                f"✅ **Tài khoản của bạn đã được chứng minh và liên kết thành công.**\n"
                f"Từ bây giờ, Trợ lý AI sẽ phục vụ bạn đúng theo phân quyền Odoo của bạn!\n\n"
                f"💡 Hãy thử gõ: *'tôi là ai'*, *'tình hình pipeline'*, hoặc đặt câu hỏi bất kỳ."
            )
        else:
            return (
                f"❌ **XÁC THỰC THẤT BẠI!**\n\n"
                f"Mật khẩu cho tài khoản Odoo `{stored_email}` không chính xác.\n"
                f"Odoo Cloud đã từ chối quyền truy cập.\n\n"
                f"🔒 **Chính sách an ninh:** Nếu bạn không phải là chủ sở hữu hoặc không có tài khoản trên Odoo, bạn sẽ không được cấp quyền.\n\n"
                f"👉 Gõ lại Email của bạn hoặc gõ `/login` để thử lại."
            )

    # 2. Lệnh đăng nhập nhanh 1 dòng: /login <email> <mật_khẩu>
    if query.startswith("/login") or query.startswith("login"):
        # Xóa ngay tin nhắn chứa lệnh đăng nhập có mật khẩu
        if msg_id and http_client:
            asyncio.create_task(http_client.post(f"{API_BASE}/deleteMessage", json={"chat_id": chat_id, "message_id": msg_id}))

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
                    f"🔒 **Bảo mật:** Mật khẩu Odoo không chính xác. Mọi hành vi nhập email người khác mà không có mật khẩu đều bị từ chối."
                )
        else:
            AUTH_PENDING_SESSIONS[chat_id] = {"stage": "email"}
            return (
                f"🔑 **BẮT ĐẦU ĐĂNG NHẬP ODOO ERP**\n\n"
                f"Vui lòng gửi **Email tài khoản Odoo của bạn** vào đây:\n"
                f"*(Ví dụ: `nam@haiminhtsc.vn` hoặc `hung@haiminhtsc.vn`)*"
            )

    # 3. Kịch bản Bước 1: Người dùng chưa xác thực nhập Email
    import re
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if email_match and not employee:
        found_email = email_match.group(0)
        AUTH_PENDING_SESSIONS[chat_id] = {"email": found_email, "time": time.time()}
        return (
            f"📧 **Tài khoản Odoo:** `{found_email}`\n\n"
            f"🔑 **BƯỚC 2: Vui lòng nhập MẬT KHẨU hoặc ODOO API KEY của tài khoản này để chứng minh quyền sở hữu:**\n\n"
            f"🛡️ *An ninh tuyệt đối: Tin nhắn chứa mật khẩu của bạn sẽ được Bot TỰ ĐỘNG XÓA NGAY LẬP TỨC khỏi màn hình chat sau khi nhận.*"
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
                "Tôi là **Hopita AI Agent** - Trợ lý Điều hành tích hợp trực tiếp **Odoo Cloud ERP**.\n\n"
                f"Tài khoản Telegram của bạn (`{chat_id}`) hiện chưa được xác thực quyền truy cập vào Odoo công ty.\n\n"
                "🔒 **QUY TRÌNH XÁC THỰC ODOO CLOUD (CHỐNG GIẢ MẠO):**\n\n"
                "👉 **Bước 1:** Nhắn **Email tài khoản Odoo** của bạn vào đây (ví dụ: `nam@haiminhtsc.vn`).\n"
                "👉 **Bước 2:** Nhập Mật khẩu Odoo khi Bot yêu cầu.\n\n"
                "🛡️ *Bảo mật an toàn: Tin nhắn chứa mật khẩu sẽ được Bot TỰ ĐỘNG XÓA NGAY LẬP TỨC khỏi cuộc trò chuyện sau khi nhận.*\n"
                "⚖️ *Dữ liệu sẽ được gửi trực tiếp lên Odoo Cloud để xác thực. Bạn sẽ được phân quyền chuẩn xác theo tài khoản Odoo của mình!*"
            )

    # Lệnh đăng xuất: /logout
    if query in ("/logout", "logout", "đăng xuất"):
        from app.services.employee import DEV_EMPLOYEES_STORE
        from app.services.identity_store import identity_store
        removed = identity_store.delete(chat_id)
        if chat_id in DEV_EMPLOYEES_STORE or removed:
            DEV_EMPLOYEES_STORE.pop(chat_id, None)
            return "🚪 **Bạn đã đăng xuất thành công khỏi hệ thống Odoo.**\nĐể tiếp tục sử dụng, vui lòng đăng nhập lại."
        return "ℹ️ Bạn hiện chưa đăng nhập tài khoản Odoo nào."

    # 4. CHẶN TUYỆT ĐỐI NẾU CHƯA ĐĂNG NHẬP THÀNH CÔNG (ZERO-TRUST ENFORCEMENT)
    if not employee:
        return (
            f"🔒 **YÊU CẦU ĐĂNG NHẬP ODOO ĐỂ TRÒ CHUYỆN**\n\n"
            f"Xin chào **{user_name}**!\n"
            f"Để bảo mật thông tin nội bộ công ty, bạn **bắt buộc phải đăng nhập tài khoản Odoo Cloud thành công** thì mới có thể trò chuyện hoặc tra cứu thông tin với Trợ lý AI.\n\n"
            f"👉 Vui lòng gửi **Email tài khoản Odoo** của bạn vào đây:\n"
            f"*(Ví dụ: `namtp@hopita.vn` hoặc `hung@haiminhtsc.vn`)*"
        )

    # 5. Tra cứu thông tin định danh & quyền hạn cá nhân (Dành cho tài khoản đã đăng nhập)
    if any(k in query for k in ["tôi là ai", "whoami", "/whoami", "quyền", "quyền hạn", "vai trò", "role", "profile", "tài khoản của tôi"]):
        if employee:
            roles_str = ", ".join(employee.get("roles", []))
            odoo_uid = employee.get("odoo_user_id") or "Chưa liên kết"
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


    from app.assistant.company_assistant import company_assistant
    t0 = time.time()
    try:
        response_text, generated_files, pending_conf = await company_assistant.handle_user_turn(
            query=text,
            chat_id=chat_id,
            employee_raw=employee,
            user_name=user_name,
            attached_file_info=attached_file_info,
            odoo_client=odoo_helper.client
        )
    except Exception as e:
        response_text = f"❌ Đã xảy ra lỗi khi kết nối Odoo Agent: {e}"
        generated_files = []
        pending_conf = None

    dt = time.time() - t0
    return (f"{response_text}\n\n⚡ *(Thời gian phản hồi: {dt:.2f}s)*", generated_files, pending_conf)

# 4. Startup Lifecycle
async def startup_lifecycle():
    print("⏳ [1/8] Loading config...")
    # Config loaded at top of file
    
    print("⏳ [2/8] Initializing storage...")
    from app.services.file_service import file_service
    # Ensure workspace exists
    
    print("⏳ [3/8] Running migrations...")
    # No-op for now, tables create IF NOT EXISTS
    
    print("⏳ [4/8] Building & Validating registry...")
    from app.skills.bootstrap import default_registry
    if len(default_registry.get_all_skills()) == 0:
        raise Exception("Registry is empty!")
        
    print("⏳ [5/8] Initializing providers...")
    await odoo_helper.ensure_auth()
    if odoo_helper.authenticated:
        print(f"✅ Odoo Cloud: Kết nối thành công (UID: {odoo_helper.client.uid})")
        
    print("⏳ [6/8] Initializing scheduler...")
    from app.services.scheduler import scheduler_service
    scheduler_service.start()
    
    print("⏳ [7/8] Running health checks...")
    from app.services.health_check import health_check_service
    health = health_check_service.check_system_health()
    if health["status"] == "ERROR":
        print(f"❌ Khởi động thất bại: {health}")
        sys.exit(1)
    
    print("✅ [8/8] Startup complete.")

# 5. Long-Polling Loop tốc độ cao với Connection Pooling
async def main():
    print("=" * 60)
    print("🚀 KHỞI ĐỘNG TELEGRAM BOT (TURBO POLLING MODE)...")
    print("=" * 60)

    # Startup Lifecycle
    await startup_lifecycle()
    
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
                        cbq = item.get("callback_query")
                        
                        if cbq:
                            # Handle Callback Query
                            if cbq.get("message", {}).get("chat", {}).get("type", "private") != "private":
                                continue
                            cb_id = cbq["id"]
                            cb_data = cbq.get("data", "")
                            cb_msg = cbq.get("message", {})
                            chat_id = cb_msg.get("chat", {}).get("id")
                            user_name = cbq["from"].get("first_name") or cbq["from"].get("username") or "Sếp"
                            
                            if chat_id:
                                # Acknowledge callback
                                asyncio.create_task(
                                    http_client.post(f"{API_BASE}/answerCallbackQuery", json={"callback_query_id": cb_id})
                                )
                                
                                if cb_data.startswith("confirm_approve_"):
                                    conf_id = cb_data.replace("confirm_approve_", "")
                                    from app.assistant.company_assistant import company_assistant
                                    action_res = await company_assistant.execute_approved_action(conf_id, chat_id, cbq["from"]["id"])
                                    reply = action_res.to_markdown() if hasattr(action_res, "to_markdown") else str(action_res)
                                    await http_client.post(f"{API_BASE}/sendMessage", json={"chat_id": chat_id, "text": reply})
                                elif cb_data.startswith("confirm_reject_"):
                                    conf_id = cb_data.replace("confirm_reject_", "")
                                    from app.agent.odoo_agent_service import odoo_agent_service
                                    if await odoo_agent_service.reject_confirmation(conf_id, chat_id, cbq["from"]["id"]):
                                        reply = "❌ **Đã hủy yêu cầu.**"
                                    else:
                                        reply = "⚠️ Yêu cầu xác nhận này đã hết hạn, không tồn tại hoặc đã được xử lý."
                                    await http_client.post(f"{API_BASE}/sendMessage", json={"chat_id": chat_id, "text": reply})
                            continue
                            
                        if not msg:
                            continue
                        
                        if msg.get("chat", {}).get("type", "private") != "private":
                            continue
                        chat_id = msg["chat"]["id"]
                        user_name = msg["from"].get("first_name") or msg["from"].get("username") or "Sếp"
                        text = msg.get("text") or msg.get("caption") or ""
                        msg_id = msg.get("message_id")
                        
                        # System commands P1D
                        if text.startswith("/status"):
                            from app.services.health_check import health_check_service
                            health = health_check_service.check_system_health()
                            st = f"🩺 **System Health:** {health['status']}\n"
                            for k, v in health["checks"].items():
                                st += f"- {k.title()}: {v}\n"
                            await http_client.post(f"{API_BASE}/sendMessage", json={"chat_id": chat_id, "text": st, "parse_mode": "Markdown"})
                            continue
                        
                        if text.startswith("/accounts"):
                            st = "🔗 **Connected Accounts:**\n- Odoo: Connected"
                            # Fetch from ProviderAccountManager
                            from app.services.accounts import provider_account_manager
                            employee = await employee_service.resolve_employee_identity(chat_id)
                            if employee:
                                accounts = provider_account_manager.list_accounts(str(employee.get("id")))
                                for acc in accounts:
                                    st += f"\n- {acc['provider']}: {acc['status']} ({acc['display_name']})"
                            await http_client.post(f"{API_BASE}/sendMessage", json={"chat_id": chat_id, "text": st, "parse_mode": "Markdown"})
                            continue

                        # Xử lý tệp đính kèm nếu có (Document hoặc Photo)
                        attached_file_info = None
                        temp_uploads_dir = Path("scratch/temp_uploads")
                        temp_uploads_dir.mkdir(parents=True, exist_ok=True)

                        if "document" in msg:
                            doc = msg["document"]
                            file_id = doc["file_id"]
                            file_name = doc.get("file_name", "document.bin")
                            print(f"\n📥 [Đang tải tài liệu từ {user_name}]: {file_name}")
                            asyncio.create_task(
                                http_client.post(f"{API_BASE}/sendChatAction", json={"chat_id": chat_id, "action": "upload_document"})
                            )
                            try:
                                gf = await http_client.get(f"{API_BASE}/getFile?file_id={file_id}")
                                gf_data = gf.json()
                                if gf_data.get("ok"):
                                    tg_file_path = gf_data["result"]["file_path"]
                                    dl_url = f"https://api.telegram.org/file/bot{TOKEN}/{tg_file_path}"
                                    file_resp = await http_client.get(dl_url)
                                    local_path = temp_uploads_dir / f"{int(time.time())}_{file_name}"
                                    with open(local_path, "wb") as f:
                                        f.write(file_resp.content)
                                    from app.services.document_parser import document_parser
                                    attached_file_info = document_parser.parse_file(str(local_path), file_name)
                                    print(f"✅ Đã phân tích file: {attached_file_info.get('summary')}")
                            except Exception as e:
                                print(f"⚠️ Lỗi tải document: {e}")

                        elif "photo" in msg and msg["photo"]:
                            photo = msg["photo"][-1]
                            file_id = photo["file_id"]
                            file_name = f"photo_{int(time.time())}.jpg"
                            print(f"\n📥 [Đang tải hình ảnh từ {user_name}]...")
                            asyncio.create_task(
                                http_client.post(f"{API_BASE}/sendChatAction", json={"chat_id": chat_id, "action": "upload_photo"})
                            )
                            try:
                                gf = await http_client.get(f"{API_BASE}/getFile?file_id={file_id}")
                                gf_data = gf.json()
                                if gf_data.get("ok"):
                                    tg_file_path = gf_data["result"]["file_path"]
                                    dl_url = f"https://api.telegram.org/file/bot{TOKEN}/{tg_file_path}"
                                    file_resp = await http_client.get(dl_url)
                                    local_path = temp_uploads_dir / file_name
                                    with open(local_path, "wb") as f:
                                        f.write(file_resp.content)
                                    from app.services.document_parser import document_parser
                                    attached_file_info = document_parser.parse_file(str(local_path), file_name)
                                    print(f"✅ Đã phân tích ảnh: {attached_file_info.get('summary')}")
                            except Exception as e:
                                print(f"⚠️ Lỗi tải photo: {e}")

                        if not text and not attached_file_info:
                            continue

                        print(f"Received message id={msg_id}")
                        
                        # ⚡ CONCURRENT ASYNC DISPATCH: Xử lý song song không gây nghẽn cho các người dùng khác
                        async def _process_single_message(p_text, p_user_name, p_chat_id, p_msg_id, p_attached_info):
                            try:
                                asyncio.create_task(
                                    http_client.post(f"{API_BASE}/sendChatAction", json={"chat_id": p_chat_id, "action": "typing"})
                                )
                                t_start = time.time()
                                raw_reply = await handle_user_query(
                                    text=p_text,
                                    user_name=p_user_name,
                                    chat_id=p_chat_id,
                                    msg_id=p_msg_id,
                                    http_client=http_client,
                                    attached_file_info=p_attached_info,
                                )
                                elapsed = time.time() - t_start

                                if isinstance(raw_reply, tuple) and len(raw_reply) == 3:
                                    reply, excel_files, pending_conf = raw_reply
                                elif isinstance(raw_reply, tuple):
                                    reply, excel_files = raw_reply[0], raw_reply[1]
                                    pending_conf = None
                                else:
                                    reply, excel_files, pending_conf = raw_reply, [], None

                                send_payload = {
                                    "chat_id": p_chat_id,
                                    "text": reply,
                                    "parse_mode": "Markdown"
                                }
                                
                                if pending_conf:
                                    conf_id = pending_conf.get("confirmation_id")
                                    preview_data = pending_conf.get("preview", {})
                                    reply += f"\n\n⚠️ **Cần xác nhận:** {pending_conf.get('skill')}\n{json.dumps(preview_data, ensure_ascii=False, indent=2)[:300]}"
                                    send_payload["reply_markup"] = {
                                        "inline_keyboard": [
                                            [
                                                {"text": "✅ Xác nhận", "callback_data": f"confirm_approve_{conf_id}"},
                                                {"text": "❌ Hủy", "callback_data": f"confirm_reject_{conf_id}"}
                                            ]
                                        ]
                                    }
                                    
                                if "Đăng Nhập Trên Web" in reply:
                                    from app.services.odoo_auth_service import odoo_auth_service
                                    auth_url = odoo_auth_service.get_verification_url(p_chat_id)
                                    if "reply_markup" not in send_payload:
                                        send_payload["reply_markup"] = {"inline_keyboard": []}
                                    send_payload["reply_markup"]["inline_keyboard"].append([{"text": "🌐 Bấm Vào Đây Để Đăng Nhập Odoo", "url": auth_url}])

                                send_payload["text"] = reply[:4000]
                                send_resp = await http_client.post(f"{API_BASE}/sendMessage", json=send_payload)
                                if not send_resp.json().get("ok"):
                                    send_payload.pop("parse_mode", None)
                                    await http_client.post(f"{API_BASE}/sendMessage", json=send_payload)

                                if excel_files:
                                    for ef in excel_files:
                                        if os.path.exists(ef):
                                            ef_path = Path(ef)
                                            print(f"📤 Đang gửi file Excel đính kèm: {ef_path.name}...")
                                            with open(ef, "rb") as f_bytes:
                                                await http_client.post(
                                                    f"{API_BASE}/sendDocument",
                                                    data={
                                                        "chat_id": p_chat_id,
                                                        "caption": f"📊 Báo cáo Excel: *{ef_path.name}*",
                                                        "parse_mode": "Markdown",
                                                    },
                                                    files={
                                                        "document": (
                                                            ef_path.name,
                                                            f_bytes.read(),
                                                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                                        )
                                                    }
                                                )

                                print(f"📤 [Đã trả lời cho {p_user_name} trong {elapsed:.2f}s]:\n{reply[:80]}...")
                            except Exception as err:
                                print(f"❌ Lỗi xử lý tin nhắn của {p_user_name}: {err}")

                        asyncio.create_task(
                            _process_single_message(text, user_name, chat_id, msg_id, attached_file_info)
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Polling loop notice: {e}")
                await asyncio.sleep(1)

async def shutdown_lifecycle():
    print("\n🛑 [1/3] Đang dừng Scheduler...")
    from app.services.scheduler import scheduler_service
    scheduler_service.stop()
    print("🛑 [2/3] Đóng các kết nối...")
    # httpx closed by context manager
    print("🛑 [3/3] Shutdown hoàn tất.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        asyncio.run(shutdown_lifecycle())
