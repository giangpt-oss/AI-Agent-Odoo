"""Tests for Emergency Kill Switch and Scoped Circuit Breaker."""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.security.kill_switch import kill_switch
from app.security.circuit_breaker import (
    ScopedCircuitBreaker,
    CircuitState,
    CircuitBreakerOpenException,
)
from app.main import app

pytestmark = pytest.mark.usefixtures("linked_employees")
from app.core.config import get_settings


@pytest.mark.asyncio
async def test_emergency_kill_switch_blocks_telegram():
    """Khi bật Kill Switch, mọi tin nhắn Telegram đều bị ngắt lập tức."""
    transport = ASGITransport(app=app)
    payload = {
        "message": {
            "chat": {"id": 999999},
            "text": "Cho tôi xem báo cáo doanh thu",
        }
    }

    # Bật Kill Switch
    await kill_switch.set_state(True)
    assert await kill_switch.is_active() is True

    with patch("app.connectors.telegram.client.TelegramConnector.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret", "X-Odoo-Webhook-Secret": "test-odoo-secret"}) as client:
            resp = await client.post("/api/v1/telegram/webhook", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "kill_switch_active"

            mock_send.assert_called_once()
            sent_text = mock_send.call_args[0][1]
            assert "HỆ THỐNG TẠM NGƯNG BẢO TRÌ KHẨN CẤP" in sent_text

    # Tắt lại Kill Switch để không ảnh hưởng test khác
    await kill_switch.set_state(False)
    assert await kill_switch.is_active() is False
    print("\n[OK] Emergency Kill Switch backend interception verified")


@pytest.mark.asyncio
async def test_scoped_circuit_breaker_trips_on_correlated_failures():
    """Khi dịch vụ Odoo bị 3 lỗi liên tiếp -> Cầu dao nhảy sang OPEN và từ chối gọi tiếp (Fast-fail)."""
    breaker = ScopedCircuitBreaker(service_name="odoo_test", failure_threshold=3, recovery_timeout_seconds=2.0)
    assert breaker.state == CircuitState.CLOSED

    async def failing_api_call():
        raise Exception("HTTP 429 Too Many Requests")

    # 2 lỗi đầu: vẫn CLOSED
    for _ in range(2):
        with pytest.raises(Exception):
            await breaker.execute(failing_api_call)
        assert breaker.state == CircuitState.CLOSED

    # Lỗi thứ 3: Nhảy sang OPEN
    with pytest.raises(Exception):
        await breaker.execute(failing_api_call)
    assert breaker.state == CircuitState.OPEN
    assert breaker.consecutive_failures == 3

    # Cuộc gọi thứ 4: Không gọi API nữa, Fast-Fail với CircuitBreakerOpenException
    with pytest.raises(CircuitBreakerOpenException) as exc_info:
        await breaker.execute(failing_api_call)

    assert "CODE_429_CORRELATED_FAILURE" in str(exc_info.value)
    print("[OK] Scoped Circuit Breaker state transition (CLOSED -> OPEN) and Fast-Fail verified")


@pytest.mark.asyncio
async def test_system_status_and_admin_toggle_api():
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret", "X-Odoo-Webhook-Secret": "test-odoo-secret"}) as client:
        # 1. Status check
        status_resp = await client.get("/api/v1/system/status")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert "kill_switch_active" in data
        assert "circuit_breakers" in data

        # 2. Toggle Kill Switch không có key -> Bị 403
        bad_toggle = await client.post("/api/v1/system/kill-switch", json={"active": True})
        assert bad_toggle.status_code == 403

        # 3. Toggle có Admin Secret Key -> Thành công
        good_toggle = await client.post(
            "/api/v1/system/kill-switch",
            json={"active": False, "reason": "Routine test"},
            headers={"x-admin-key": settings.APP_SECRET_KEY},
        )
        assert good_toggle.status_code == 200
        assert good_toggle.json()["status"] == "success"
    print("[OK] Admin System status and Kill Switch API verified")
