import pytest
from unittest.mock import AsyncMock, patch
import httpx

from app.services.gateway_client import GatewayClient


@pytest.mark.asyncio
async def test_gateway_client_create_workspace_job_fast():
    client = GatewayClient(base_url="http://test-gw:8080", secret="test_secret")
    
    mock_resp = httpx.Response(
        status_code=200,
        json={"status": "completed", "job_id": "job_123", "result": {"items": []}},
        request=httpx.Request("POST", "http://test-gw:8080/internal/v1/users/1/workspace/jobs"),
    )
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        
        res = await client.create_workspace_job(
            user_id=1,
            request_id="req_fast",
            service="calendar",
            operation="events.list",
            wait_seconds=2.0,
        )
        assert res["status"] == "completed"
        assert res["job_id"] == "job_123"


@pytest.mark.asyncio
async def test_gateway_client_create_workspace_job_slow_returns_accepted():
    client = GatewayClient(base_url="http://test-gw:8080", secret="test_secret")
    
    mock_resp = httpx.Response(
        status_code=202,
        json={"status": "queued", "job_id": "job_slow_456", "retry_after_seconds": 2},
        request=httpx.Request("POST", "http://test-gw:8080/internal/v1/users/1/workspace/jobs"),
    )
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        
        res = await client.create_workspace_job(
            user_id=1,
            request_id="req_slow",
            service="drive",
            operation="files.list",
            wait_seconds=2.0,
        )
        assert res["status"] == "queued"
        assert res["job_id"] == "job_slow_456"
        assert res["retry_after_seconds"] == 2

