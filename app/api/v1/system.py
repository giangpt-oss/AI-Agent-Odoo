from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from app.core.config import get_settings
from app.security.kill_switch import kill_switch
from app.security.circuit_breaker import odoo_circuit_breaker, google_circuit_breaker

router = APIRouter(prefix="/system", tags=["System & Governance"])


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str | None = None


@router.get("/status")
async def get_system_status():
    """Kiểm tra trạng thái hạ tầng, Kill Switch và các Circuit Breakers."""
    return {
        "kill_switch_active": await kill_switch.is_active(),
        "circuit_breakers": {
            "odoo_cloud": {
                "state": odoo_circuit_breaker.state.value,
                "consecutive_failures": odoo_circuit_breaker.consecutive_failures,
            },
            "google_workspace": {
                "state": google_circuit_breaker.state.value,
                "consecutive_failures": google_circuit_breaker.consecutive_failures,
            },
        },
    }


@router.post("/kill-switch")
async def toggle_kill_switch(
    body: KillSwitchRequest,
    x_admin_key: str | None = Header(default=None),
):
    """Kích hoạt khẩn cấp ngắt toàn bộ hệ thống hoặc chuyển sang chế độ bảo trì."""
    settings = get_settings()
    if x_admin_key != settings.APP_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Yêu cầu quyền Quản trị tối cao (Admin Secret Key)")

    await kill_switch.set_state(body.active)
    return {
        "status": "success",
        "kill_switch_active": body.active,
        "message": f"Hệ thống đã {'BẬT NGẮT KHẨN CẤP' if body.active else 'MỞ LẠI BÌNH THƯỜNG'}",
        "reason": body.reason,
    }
