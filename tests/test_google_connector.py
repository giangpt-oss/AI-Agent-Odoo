"""Tests for Google OAuth, Token Lifecycle, and Google Connector."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.connectors.google.client import GoogleOAuthService
from app.connectors.google.connector import GoogleConnector
from app.security.tokens import token_cipher
from app.main import app


def test_google_oauth_authorize_url_generation():
    service = GoogleOAuthService()
    url = service.get_authorization_url(state="emp-user-123")
    assert "https://accounts.google.com/o/oauth2/v2/auth" in url
    assert "state=emp-user-123" in url
    assert "gmail.readonly" in url
    print("\n[OK] Google OAuth authorization URL generated correctly")


@pytest.mark.asyncio
async def test_google_oauth_refresh_access_token_with_cipher():
    service = GoogleOAuthService()
    # Giả lập token gốc và mã hóa vào DB
    real_refresh_token = "1//04_google_secret_refresh_token_abc"
    encrypted_token = token_cipher.encrypt(real_refresh_token)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "ya29.new_access_token_xyz"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        new_token = await service.refresh_access_token(encrypted_token)
        assert new_token == "ya29.new_access_token_xyz"
        mock_post.assert_called_once()
    print("[OK] Google token refresh using decrypted token verified")


@pytest.mark.asyncio
async def test_google_connector_search_gmail_messages():
    connector = GoogleConnector()

    mock_list_resp = MagicMock()
    mock_list_resp.status_code = 200
    mock_list_resp.json.return_value = {
        "messages": [{"id": "msg_001"}]
    }
    mock_list_resp.raise_for_status.return_value = None

    mock_detail_resp = MagicMock()
    mock_detail_resp.status_code = 200
    mock_detail_resp.json.return_value = {
        "id": "msg_001",
        "snippet": "Kính gửi báo giá dự án...",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Báo giá tháng 10"},
                {"name": "From", "value": "partner@domain.com"},
            ]
        },
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        # Lần 1: search list, Lần 2: get detail
        mock_get.side_effect = [mock_list_resp, mock_detail_resp]

        results = await connector.search_gmail_messages(
            access_token="fake_token",
            query="Báo giá",
            max_results=1,
        )

        assert len(results) == 1
        assert results[0]["id"] == "msg_001"
        assert results[0]["subject"] == "Báo giá tháng 10"
        assert results[0]["from"] == "partner@domain.com"
    print("[OK] GoogleConnector Gmail search & metadata parse verified")


@pytest.mark.asyncio
async def test_api_oauth_authorize_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/oauth/google/authorize?state=chat-112233")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_url" in data
        assert "state=chat-112233" in data["authorization_url"]
    print("[OK] FastAPI Google OAuth authorize endpoint verified")
