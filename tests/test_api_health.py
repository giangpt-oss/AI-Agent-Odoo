"""Test FastAPI Gateway endpoints and middlewares."""
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_root_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "Enterprise AI Agent" in data["message"]
        assert "X-Request-ID" in response.headers
        assert "X-Response-Time" in response.headers
        print("\n[OK] Root endpoint verified with correlation headers")


@pytest.mark.asyncio
async def test_health_check_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["kill_switch_active"] is False
        assert data["service"] == "api-gateway"
        print("[OK] Healthcheck endpoint verified")
