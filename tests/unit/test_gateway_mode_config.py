import pytest
from pydantic import ValidationError
from app.core.config import Settings
from fastapi.testclient import TestClient
from app.main import create_application


def test_secrets_default_empty():
    s = Settings(
        INTEGRATION_MODE="direct",
        GATEWAY_TO_AGENT_SECRET="",
        AGENT_TO_GATEWAY_SECRET="",
    )
    assert s.GATEWAY_TO_AGENT_SECRET == ""
    assert s.AGENT_TO_GATEWAY_SECRET == ""


def test_gateway_mode_requires_secrets():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            INTEGRATION_MODE="gateway",
            GATEWAY_TO_AGENT_SECRET="",
            AGENT_TO_GATEWAY_SECRET="",
        )
    assert "GATEWAY_TO_AGENT_SECRET" in str(exc_info.value) or "AGENT_TO_GATEWAY_SECRET" in str(exc_info.value)


def test_gateway_mode_requires_min_32_chars():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            INTEGRATION_MODE="gateway",
            GATEWAY_TO_AGENT_SECRET="short_secret_under_32_chars",
            AGENT_TO_GATEWAY_SECRET="another_short_secret_under_32",
        )
    assert "32" in str(exc_info.value)


def test_gateway_mode_requires_different_secrets():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            INTEGRATION_MODE="gateway",
            GATEWAY_TO_AGENT_SECRET="this_is_a_very_long_secret_that_has_at_least_32_characters",
            AGENT_TO_GATEWAY_SECRET="this_is_a_very_long_secret_that_has_at_least_32_characters",
        )
    assert "different" in str(exc_info.value).lower() or "distinct" in str(exc_info.value).lower() or "khác nhau" in str(exc_info.value).lower()


def test_direct_mode_does_not_mount_internal_turn():
    settings = Settings(
        INTEGRATION_MODE="direct",
    )
    app = create_application(settings=settings)
    client = TestClient(app)
    resp = client.post("/internal/v1/telegram/turn", json={})
    assert resp.status_code == 404


def test_gateway_mode_disables_direct_telegram_webhook():
    settings = Settings(
        INTEGRATION_MODE="gateway",
        GATEWAY_TO_AGENT_SECRET="gateway_to_agent_secret_32_chars_long_12345",
        AGENT_TO_GATEWAY_SECRET="agent_to_gateway_secret_32_chars_long_67890",
    )
    app = create_application(settings=settings)
    client = TestClient(app)
    resp = client.post("/api/v1/telegram/webhook", json={})
    assert resp.status_code in [403, 404]
