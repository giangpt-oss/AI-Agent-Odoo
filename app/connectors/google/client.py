import logging
from typing import Any
import httpx
from app.core.config import get_settings
from app.security.tokens import token_cipher

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_GMAIL_API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_CALENDAR_API_URL = "https://www.googleapis.com/calendar/v3"

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
]


class GoogleOAuthService:
    """Quản lý vòng đời OAuth2 của Google: Authorize URL, Exchange Token, Refresh Token."""

    def __init__(self):
        self.settings = get_settings()

    def get_authorization_url(self, state: str) -> str:
        """Tạo đường link redirect để nhân viên cấp quyền Google Workspace."""
        params = {
            "client_id": self.settings.GOOGLE_CLIENT_ID,
            "redirect_uri": self.settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(DEFAULT_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,  # Thường là employee_id hoặc telegram_chat_id
        }
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{GOOGLE_AUTH_URL}?{query_string}"

    async def exchange_code_for_tokens(self, code: str) -> dict[str, Any]:
        """Đổi authorization code lấy access_token và refresh_token."""
        data = {
            "code": code,
            "client_id": self.settings.GOOGLE_CLIENT_ID,
            "client_secret": self.settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": self.settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=data)
            response.raise_for_status()
            return response.json()

    async def refresh_access_token(self, encrypted_refresh_token: str) -> str:
        """Giải mã refresh_token và lấy access_token mới từ Google."""
        plain_refresh_token = token_cipher.decrypt(encrypted_refresh_token)
        data = {
            "refresh_token": plain_refresh_token,
            "client_id": self.settings.GOOGLE_CLIENT_ID,
            "client_secret": self.settings.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=data)
            response.raise_for_status()
            result = response.json()
            return result.get("access_token", "")
