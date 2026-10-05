from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.oauth import router as oauth_router
from app.api.v1.telegram import router as telegram_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.system import router as system_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(oauth_router)
api_v1_router.include_router(telegram_router)
api_v1_router.include_router(webhooks_router)
api_v1_router.include_router(system_router)
