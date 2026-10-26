from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.oauth import router as oauth_router
from app.api.v1.telegram import router as telegram_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.system import router as system_router
from app.core.config import Settings, get_settings


def get_api_v1_router(settings: Settings | None = None) -> APIRouter:
    if settings is None:
        settings = get_settings()
    router = APIRouter(prefix="/api/v1")
    router.include_router(health_router)
    router.include_router(oauth_router)
    # Gateway mode disables direct telegram webhook
    if settings.INTEGRATION_MODE != "gateway":
        router.include_router(telegram_router)
    router.include_router(webhooks_router)
    router.include_router(system_router)
    return router


api_v1_router = get_api_v1_router()
