"""Tests for Database Models, Token Cipher, and Redis Service."""
import pytest
from app.security.tokens import token_cipher
from app.models import Base, Employee, AuditLog, OAuthToken
from app.core.redis import redis_service


def test_token_cipher_encrypt_decrypt():
    original_secret = "1//04_fake_google_refresh_token_xyz123"
    encrypted = token_cipher.encrypt(original_secret)

    # Đảm bảo mã hóa ra chuỗi khác hoàn toàn
    assert encrypted != original_secret
    assert len(encrypted) > 20

    # Giải mã lại chính xác
    decrypted = token_cipher.decrypt(encrypted)
    assert decrypted == original_secret
    print("\n[OK] TokenCipher AES-Fernet encryption & decryption verified")


def test_sqlalchemy_model_metadata():
    tables = Base.metadata.tables
    assert "employees" in tables
    assert "audit_logs" in tables
    assert "oauth_tokens" in tables

    emp_table = tables["employees"]
    assert "telegram_chat_id" in emp_table.c
    assert "email" in emp_table.c
    assert "roles" in emp_table.c

    audit_table = tables["audit_logs"]
    assert "request_id" in audit_table.c
    assert "status" in audit_table.c
    assert "tool_name" in audit_table.c

    oauth_table = tables["oauth_tokens"]
    assert "encrypted_refresh_token" in oauth_table.c
    print("[OK] SQLAlchemy ORM metadata and schemas verified")


@pytest.mark.asyncio
async def test_redis_service_graceful_handling():
    # Kiểm tra phương thức ping/is_connected an toàn
    connected = await redis_service.is_connected()
    # Dù Redis local có đang bật hay tắt thì hàm không được crash mà phải trả về bool
    assert isinstance(connected, bool)
    print(f"[OK] Redis service connectivity check: {connected}")
