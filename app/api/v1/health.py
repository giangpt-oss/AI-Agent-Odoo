from fastapi import APIRouter
from app.core.config import get_settings

router = APIRouter(prefix="/health", tags=["Health & System"])


@router.get("")
async def health_check():
    """Kiểm tra sức khỏe của API Gateway và trạng thái Kill Switch."""
    settings = get_settings()
    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "kill_switch_active": settings.EMERGENCY_KILL_SWITCH,
        "service": "api-gateway"
    }
