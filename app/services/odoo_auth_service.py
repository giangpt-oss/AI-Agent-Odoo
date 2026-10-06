import time
import hmac
import hashlib
import base64
import json
import logging
from typing import Any

from app.core.config import get_settings
from app.connectors.odoo.client import OdooAsyncClient
from app.services.employee import DEV_EMPLOYEES_STORE

logger = logging.getLogger(__name__)


class OdooAuthService:
    """Dịch vụ tạo link xác thực và kiểm tra thông tin đăng nhập trực tiếp với Odoo Cloud."""

    def __init__(self):
        self.settings = get_settings()
        self.secret_key = self.settings.APP_SECRET_KEY.encode('utf-8')

    def generate_token(self, chat_id: int, expires_in_seconds: int = 900) -> str:
        """Sinh One-Time Token an toàn có ký HMAC-SHA256 (hạn 15 phút)."""
        payload = {
            "chat_id": chat_id,
            "exp": int(time.time()) + expires_in_seconds
        }
        raw_bytes = json.dumps(payload).encode('utf-8')
        encoded_data = base64.urlsafe_b64encode(raw_bytes).decode('utf-8')
        signature = hmac.new(self.secret_key, encoded_data.encode('utf-8'), hashlib.sha256).hexdigest()
        return f"{encoded_data}.{signature}"

    def verify_token(self, token: str) -> int | None:
        """Kiểm tra chữ ký và hạn sử dụng của token. Trả về Telegram Chat ID nếu hợp lệ."""
        try:
            if not token or "." not in token:
                return None
            encoded_data, signature = token.split(".", 1)
            expected_sig = hmac.new(self.secret_key, encoded_data.encode('utf-8'), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected_sig):
                logger.warning("Token chữ ký không khớp")
                return None

            raw_bytes = base64.urlsafe_b64decode(encoded_data.encode('utf-8'))
            payload = json.loads(raw_bytes.decode('utf-8'))
            if payload.get("exp", 0) < int(time.time()):
                logger.warning("Token đã hết hạn")
                return None

            return payload.get("chat_id")
        except Exception as e:
            logger.error(f"Lỗi giải mã token: {e}")
            return None

    def get_verification_url(self, chat_id: int) -> str:
        """Tạo URL xác thực Odoo cho người dùng."""
        token = self.generate_token(chat_id)
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            lan_ip = "192.168.1.97"
        base_url = f"http://{lan_ip}:{self.settings.PORT}"
        return f"{base_url}/auth/odoo-verify?token={token}"

    async def authenticate_and_link(
        self, chat_id: int, login: str, password_or_key: str
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """Gửi trực tiếp thông tin người dùng lên Odoo Cloud để xác thực (Proof of Ownership).
        - Nếu Odoo trả về UID > 0: Xác thực thành công -> Trích xuất quyền hạn Odoo thực tế và liên kết với Chat ID.
        - Nếu Odoo từ chối: Trả về thất bại ngay lập tức, không cấp quyền.
        """
        clean_login = (login or "").strip().lower()
        clean_secret = (password_or_key or "").strip()

        if not clean_login or not clean_secret:
            return False, None, "Vui lòng nhập đầy đủ Email đăng nhập Odoo và Mật khẩu / API Key."

        # 1. Gọi trực tiếp Odoo Cloud để kiểm tra xem tài khoản & mật khẩu có đúng không
        user_client = OdooAsyncClient(
            base_url=self.settings.ODOO_URL,
            db=self.settings.ODOO_DB,
            username=clean_login,
            api_key=clean_secret,
            timeout=8.0
        )

        try:
            verified_uid = await user_client.authenticate()
        except Exception as e:
            logger.warning(f"Odoo Cloud từ chối xác thực cho {clean_login}: {e}")
            return False, None, "❌ Xác thực thất bại! Tài khoản hoặc mật khẩu không chính xác trên hệ thống Odoo ERP."

        if not verified_uid or verified_uid <= 0:
            return False, None, "❌ Tài khoản Odoo không hợp lệ hoặc không có quyền truy cập."

        # Read the profile using the authenticated user's ACL, never a service admin.
        system_client = user_client
        full_name = clean_login
        job_title = "Nhân sự Odoo"
        department = "Công ty Hải Minh / Hopita"

        emps = []
        # Tra cứu tên trong hr.employee
        try:
            emps = await system_client.execute_kw(
                model='hr.employee',
                method='search_read',
                args=[[['user_id', '=', verified_uid]]],
                kwargs={'fields': ['name', 'work_email', 'job_title', 'department_id'], 'limit': 1}
            )
            if emps:
                full_name = emps[0].get('name') or full_name
                job_title = emps[0].get('job_title') or job_title
                dept = emps[0].get('department_id')
                if dept and isinstance(dept, (list, tuple)) and len(dept) > 1:
                    department = dept[1]
        except Exception as e:
            logger.warning(f"Không thể lấy hr.employee cho UID {verified_uid}: {e}")

        roles = []
        groups = {
            "base.group_user": "employee",
            "base.group_system": "admin",
            "sales_team.group_sale_salesman": "sales_user",
            "sales_team.group_sale_manager": "sales_manager",
            "hr.group_hr_user": "hr_user",
            "hr.group_hr_manager": "hr_manager",
        }
        for group, role in groups.items():
            try:
                member = await user_client.execute_kw("res.users", "has_group", [[verified_uid], group], {})
                if member is True:
                    roles.append(role)
            except Exception:
                logger.warning("Could not verify Odoo group %s; permission not granted", group)
        try:
            users = await user_client.execute_kw("res.users", "read", [[verified_uid]], {"fields": ["name"]})
            if users and not emps:
                full_name = users[0].get("name") or full_name
        except Exception:
            logger.warning("Could not read authenticated user's display name")
        roles = sorted(set(roles))

        profile = {
            "id": f"odoo-uid-{verified_uid}",
            "email": clean_login,
            "full_name": full_name,
            "job_title": job_title,
            "department": department,
            "roles": roles,
            "odoo_user_id": verified_uid,
            "is_active": True,
        }

        # 3. Gắn Telegram Chat ID vĩnh viễn với tài khoản Odoo đã xác thực
        from app.services.identity_store import identity_store
        identity_store.save(chat_id, profile, clean_login, clean_secret)
        logger.info(f"✅ ĐÃ XÁC THỰC THÀNH CÔNG: Chat ID {chat_id} -> Odoo UID {verified_uid} ({full_name}) - Roles: {roles}")

        return True, profile, "Xác thực thành công!"


odoo_auth_service = OdooAuthService()
