import logging
import httpx
from fastapi import APIRouter, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.services.odoo_auth_service import odoo_auth_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Odoo Authentication Portal"])


@router.get("/auth/odoo-verify", response_class=HTMLResponse)
async def odoo_verify_page(token: str = Query(..., description="Mã xác thực từ Telegram")):
    """Hiển thị trang đăng nhập bảo mật để xác thực tài khoản Odoo Cloud."""
    chat_id = odoo_auth_service.verify_token(token)
    if not chat_id:
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html lang="vi">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Lỗi Xác Thực</title>
                <style>
                    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background: #1e293b; padding: 2.5rem; border-radius: 1rem; border: 1px solid #ef4444; max-width: 420px; text-align: center; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); }
                    h2 { color: #ef4444; margin-bottom: 1rem; }
                    p { color: #94a3b8; line-height: 1.5; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h2>⚠️ LIÊN KẾT HẾT HẠN</h2>
                    <p>Liên kết xác thực này đã hết hạn hoặc không hợp lệ.</p>
                    <p>Vui lòng quay lại Telegram và gõ <b>/start</b> hoặc <b>xác thực</b> để nhận liên kết mới.</p>
                </div>
            </body>
            </html>
            """,
            status_code=400
        )

    settings = get_settings()
    odoo_host = settings.ODOO_URL.replace("https://", "").replace("http://", "")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Xác Thực Tài Khoản Odoo Cloud</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background: linear-gradient(135deg, #0b0f19 0%, #1e1b4b 100%);
                color: #f8fafc;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                padding: 1rem;
            }}
            .container {{
                background: rgba(30, 41, 59, 0.7);
                backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 1.25rem;
                padding: 2.5rem 2rem;
                max-width: 440px;
                width: 100%;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
            }}
            .badge {{
                display: inline-block;
                background: rgba(99, 102, 241, 0.2);
                color: #818cf8;
                font-size: 0.75rem;
                font-weight: 600;
                padding: 0.25rem 0.75rem;
                border-radius: 9999px;
                margin-bottom: 1rem;
                border: 1px solid rgba(99, 102, 241, 0.3);
            }}
            h1 {{
                font-size: 1.5rem;
                font-weight: 700;
                margin-bottom: 0.5rem;
                background: linear-gradient(to right, #ffffff, #cbd5e1);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }}
            .desc {{
                color: #94a3b8;
                font-size: 0.875rem;
                margin-bottom: 1.75rem;
                line-height: 1.5;
            }}
            .server-info {{
                background: rgba(15, 23, 42, 0.6);
                border-radius: 0.5rem;
                padding: 0.75rem 1rem;
                font-size: 0.8rem;
                color: #38bdf8;
                margin-bottom: 1.5rem;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }}
            .form-group {{
                margin-bottom: 1.25rem;
            }}
            label {{
                display: block;
                font-size: 0.875rem;
                font-weight: 500;
                color: #cbd5e1;
                margin-bottom: 0.5rem;
            }}
            input {{
                width: 100%;
                background: rgba(15, 23, 42, 0.8);
                border: 1px solid #334155;
                color: #fff;
                padding: 0.75rem 1rem;
                border-radius: 0.5rem;
                font-size: 0.95rem;
                outline: none;
                transition: border-color 0.2s;
            }}
            input:focus {{
                border-color: #6366f1;
                box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2);
            }}
            button {{
                width: 100%;
                background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
                color: #ffffff;
                font-weight: 600;
                padding: 0.875rem;
                border: none;
                border-radius: 0.5rem;
                cursor: pointer;
                font-size: 1rem;
                margin-top: 0.5rem;
                box-shadow: 0 10px 15px -3px rgba(79, 70, 229, 0.3);
                transition: transform 0.1s, opacity 0.2s;
            }}
            button:hover {{
                opacity: 0.95;
            }}
            button:active {{
                transform: scale(0.99);
            }}
            .security-note {{
                font-size: 0.75rem;
                color: #64748b;
                text-align: center;
                margin-top: 1.5rem;
                line-height: 1.4;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <span class="badge">🔒 XÁC THỰC DOANH NGHIỆP</span>
            <h1>Hopita AI Agent</h1>
            <p class="desc">Vui lòng đăng nhập bằng tài khoản Odoo Cloud của bạn để cấp quyền tương ứng cho Telegram.</p>
            
            <div class="server-info">
                <span>🌐</span>
                <span>Odoo Server: <b>{odoo_host}</b></span>
            </div>

            <form action="/auth/odoo-verify" method="POST">
                <input type="hidden" name="token" value="{token}">
                
                <div class="form-group">
                    <label for="login">Email đăng nhập Odoo</label>
                    <input type="email" id="login" name="login" placeholder="ví dụ: hung@haiminhtsc.vn" required autofocus>
                </div>

                <div class="form-group">
                    <label for="password">Mật khẩu hoặc API Key Odoo</label>
                    <input type="password" id="password" name="password" placeholder="Nhập mật khẩu Odoo của bạn" required>
                </div>

                <button type="submit">Xác Thực & Liên Kết</button>
            </form>

            <p class="security-note">
                🛡️ Thông tin đăng nhập được gửi trực tiếp tới máy chủ Odoo Cloud qua giao thức mã hóa SSL/TLS để đối soát, hệ thống không lưu trữ mật khẩu của bạn.
            </p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.post("/auth/odoo-verify", response_class=HTMLResponse)
async def odoo_verify_submit(
    token: str = Form(...),
    login: str = Form(...),
    password: str = Form(...)
):
    """Xử lý xác thực thông tin đăng nhập với Odoo Cloud."""
    chat_id = odoo_auth_service.verify_token(token)
    if not chat_id:
        return HTMLResponse(
            content="<h3>⚠️ Phiên xác thực không hợp lệ hoặc đã hết hạn. Vui lòng thử lại từ Telegram.</h3>",
            status_code=400
        )

    success, profile, message = await odoo_auth_service.authenticate_and_link(
        chat_id=chat_id,
        login=login,
        password_or_key=password
    )

    if not success or not profile:
        error_html = f"""
        <!DOCTYPE html>
        <html lang="vi">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Xác Thực Thất Bại</title>
            <style>
                body {{ font-family: -apple-system, sans-serif; background: #0f172a; color: #fff; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; padding: 1rem; }}
                .card {{ background: #1e293b; padding: 2rem; border-radius: 1rem; border: 1px solid #ef4444; max-width: 400px; text-align: center; }}
                h2 {{ color: #ef4444; margin-bottom: 1rem; font-size: 1.25rem; }}
                p {{ color: #cbd5e1; font-size: 0.9rem; margin-bottom: 1.5rem; line-height: 1.5; }}
                a {{ display: inline-block; background: #334155; color: #fff; padding: 0.75rem 1.5rem; border-radius: 0.5rem; text-decoration: none; font-weight: 500; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>❌ XÁC THỰC THẤT BẠI</h2>
                <p>{message}</p>
                <p>Nếu bạn không có tài khoản trên Odoo Cloud công ty, bạn không thể truy cập các tính năng nội bộ.</p>
                <a href="/auth/odoo-verify?token={token}">Thử lại</a>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=error_html, status_code=401)

    # Gửi thông báo trực tiếp vào Telegram Chat của người dùng
    settings = get_settings()
    if settings.TELEGRAM_BOT_TOKEN:
        try:
            tg_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
            roles_text = ", ".join(profile.get("roles", []))
            notify_text = (
                f"🎉 **XÁC THỰC THÀNH CÔNG VỚI ODOO ERP!**\n"
                f"────────────────────────────\n"
                f"• **Họ và tên:** {profile['full_name']}\n"
                f"• **Tài khoản Odoo:** `{profile['email']}` (UID: {profile['odoo_user_id']})\n"
                f"• **Chức vụ / Bộ phận:** {profile['job_title']} | {profile['department']}\n"
                f"• **Quyền hạn được cấp:** `{roles_text}`\n\n"
                f"✅ **Tài khoản Telegram của bạn đã được liên kết chính thức.**\n"
                f"Bây giờ bạn có thể bắt đầu tra cứu các phân hệ Odoo tương ứng với chức vụ của mình!\n\n"
                f"👉 Hãy thử gõ: *'tôi là ai'*, *'tình hình pipeline'*, hoặc đặt câu hỏi bất kỳ."
            )
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                await http_client.post(tg_url, json={
                    "chat_id": chat_id,
                    "text": notify_text,
                    "parse_mode": "Markdown"
                })
        except Exception as e:
            logger.error(f"Không thể gửi thông báo Telegram: {e}")

    success_html = f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Xác Thực Thành Công</title>
        <style>
            body {{ font-family: -apple-system, sans-serif; background: #0f172a; color: #fff; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; padding: 1rem; }}
            .card {{ background: #1e293b; padding: 2.5rem 2rem; border-radius: 1.25rem; border: 1px solid #22c55e; max-width: 440px; text-align: center; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7); }}
            .icon {{ font-size: 3rem; margin-bottom: 1rem; }}
            h2 {{ color: #22c55e; margin-bottom: 0.5rem; font-size: 1.5rem; }}
            .info {{ background: rgba(15, 23, 42, 0.6); padding: 1rem; border-radius: 0.5rem; margin: 1.5rem 0; text-align: left; font-size: 0.875rem; line-height: 1.6; color: #cbd5e1; }}
            .info b {{ color: #fff; }}
            p {{ color: #94a3b8; font-size: 0.9rem; line-height: 1.5; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">✅</div>
            <h2>XÁC THỰC THÀNH CÔNG!</h2>
            <p>Hệ thống Odoo Cloud đã xác nhận danh tính và phân quyền của bạn.</p>
            
            <div class="info">
                <div>• Họ và tên: <b>{profile['full_name']}</b></div>
                <div>• Chức vụ: <b>{profile['job_title']}</b></div>
                <div>• Email: <b>{profile['email']}</b></div>
                <div>• Vai trò: <b>{", ".join(profile['roles'])}</b></div>
            </div>

            <p>👉 Bạn có thể đóng tab này và <b>quay lại Telegram</b> để tiếp tục trò chuyện với Hopita AI Agent.</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=success_html)
