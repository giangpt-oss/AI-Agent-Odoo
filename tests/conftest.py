"""Run tests without real credentials, external network or application databases."""
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
import pytest

_ORIGINAL_CWD = Path.cwd()
_SANDBOX = tempfile.TemporaryDirectory(prefix="agent-tests-")
os.environ.update({
    "APP_DEBUG": "false", "DEBUG": "true", "AGENT_DATA_DIR": str(Path(_SANDBOX.name) / ".agent_data"),
    "GEMINI_API_KEY": "", "OPENAI_API_KEY": "", "ODOO_API_KEY": "test-only",
    "ODOO_URL": "https://odoo.invalid", "ODOO_DB": "test", "ODOO_ADMIN_USERNAME": "test",
    "TELEGRAM_BOT_TOKEN": "123456789:TEST_ONLY_NOT_A_REAL_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET": "test-webhook-secret", "ODOO_WEBHOOK_SECRET": "test-odoo-secret",
    "APP_SECRET_KEY": "test-only-app-secret-key-for-unit-tests",
    "GOOGLE_CLIENT_ID": "test-client", "GOOGLE_CLIENT_SECRET": "test-secret",
    "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/test",
    "REDIS_URL": "redis://127.0.0.1:1/0",
})

def pytest_sessionstart(session):
    os.chdir(_SANDBOX.name)

def pytest_unconfigure(config):
    os.chdir(_ORIGINAL_CWD)
    # Third-party SQLite/vector clients may keep files open on Windows.
    _SANDBOX._finalizer.detach()

@pytest.fixture(autouse=True)
def block_external_io(monkeypatch):
    import httpx
    import asyncpg
    from redis.asyncio import Redis
    def blocked(*args, **kwargs):
        raise httpx.ConnectError("External network is disabled in unit tests")
    async def blocked_async(*args, **kwargs):
        raise ConnectionError("External network is disabled in unit tests")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    async def blocked_http(*args, **kwargs):
        raise httpx.ConnectError("External HTTP is disabled in unit tests")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_http)
    monkeypatch.setattr(asyncpg, "connect", blocked_async)
    monkeypatch.setattr(Redis, "execute_command", blocked_async)

@pytest.fixture(scope="session", autouse=True)
def offline_embeddings():
    import hashlib
    import re
    from app.providers.embeddings.gemini import default_embedding_provider
    def vector(text):
        result = [0.0] * 768
        for token in re.findall(r"\w+", text.lower()):
            index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:2], "big") % 768
            result[index] += 1
        norm = sum(v*v for v in result) ** 0.5 or 1
        return [v/norm for v in result]
    async def batch(texts):
        return [vector(text) for text in texts]
    async def single(text):
        return vector(text)
    patch = pytest.MonkeyPatch()
    patch.setattr(default_embedding_provider, "embed_batch", batch)
    patch.setattr(default_embedding_provider, "embed_text", single)
    yield
    patch.undo()

@pytest.fixture
def linked_employees():
    from app.services.identity_store import identity_store
    profiles = {
        999999: {"id": "sales-test", "roles": ["sales_user", "employee"]},
        888888: {"id": "warehouse-test", "roles": ["warehouse_user", "employee"]},
        777777: {"id": "manager-test", "roles": ["sales_manager", "employee"]},
    }
    for chat, profile in profiles.items():
        profile.update(full_name="Test User", email="test@example.com", is_active=True, odoo_user_id=42)
        identity_store.save(chat, profile, "test", "test-only")
    yield
    for chat in profiles:
        identity_store.delete(chat)

@pytest.fixture(autouse=True)
def reset_kill_switch():
    from app.security.kill_switch import kill_switch
    kill_switch._in_memory_state = None
    yield
    kill_switch._in_memory_state = None
