import pytest
from starlette.testclient import TestClient
from app.main import create_application
from app.core.config import Settings


def test_oauth_google_callback_forwards_to_gateway():
    settings = Settings(
        APP_ENV="development",
        INTEGRATION_MODE="gateway",
        GATEWAY_BASE_URL="http://localhost:8080",
        GATEWAY_TO_AGENT_SECRET="gw_secret_32_chars_minimum_test123",
        AGENT_TO_GATEWAY_SECRET="ag_secret_32_chars_minimum_test123",
    )
    app = create_application(settings)
    with TestClient(app) as client:
        res = client.get(
            "/oauth/google/callback?code=mock_code&state=mock_state",
            follow_redirects=False,
        )
        assert res.status_code == 307
        assert res.headers["location"] == "http://localhost:8080/oauth/google/callback?code=mock_code&state=mock_state"
