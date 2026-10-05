from cryptography.fernet import Fernet
from app.core.config import get_settings


class TokenCipher:
    """Mã hóa đối xứng Fernet (AES-128-CBC + HMAC-SHA256) cho OAuth Refresh Tokens.
    Tuyệt đối không lưu token dạng plain-text vào database.
    """

    def __init__(self, key: str | None = None):
        if not key:
            key = get_settings().TOKEN_ENCRYPTION_KEY
        self.fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plain_text: str) -> str:
        """Mã hóa chuỗi plain_text thành chuỗi an toàn."""
        if not plain_text:
            return ""
        return self.fernet.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def decrypt(self, cipher_text: str) -> str:
        """Giải mã chuỗi cipher_text về dạng plain_text ban đầu."""
        if not cipher_text:
            return ""
        return self.fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")


token_cipher = TokenCipher()
