import json
from pathlib import Path

import httpx
import pytest

from app.services.gateway_client import GatewayClient


CASES = json.loads(Path(__file__).with_name("workspace-v1.json").read_text())["cases"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", CASES)
async def test_workspace_contract(name):
    case = CASES[name]
    client = GatewayClient(base_url="https://gateway.example.invalid", secret="contract-test-secret")

    def exchange(request):
        assert request.method == case["method"]
        assert request.url.path == case["path"]
        assert request.headers["Authorization"] == "Bearer contract-test-secret"
        if "request" in case:
            actual = json.loads(request.content)
            assert {field: actual[field] for field in case["request"]} == case["request"]
        return httpx.Response(case["http_status"], json=case["response"])

    client._pool = httpx.AsyncClient(transport=httpx.MockTransport(exchange))
    try:
        if name == "queued":
            result = await client.create_workspace_job(1, **case["request"])
        elif case["method"] == "GET":
            result = await client.get_workspace_job(1, "fixture-job")
        else:
            payload = case["request"]
            result = await client.workspace_query(1, payload["service"], {"method": payload["method"]})
        assert result == case["response"]
    finally:
        await client.close()
