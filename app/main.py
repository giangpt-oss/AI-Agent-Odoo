import uuid
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.core.config import Settings, get_settings
from app.api.v1.router import api_v1_router


import asyncio
import logging

logger = logging.getLogger(__name__)


async def _background_lease_reconciliation_loop():
    """Tác vụ nền chạy mỗi 30 giây: chỉ GET trạng thái, chỉ xử lý lease đã hết hạn."""
    from app.services.confirmation_service import reconcile_leases_with_gateway
    while True:
        try:
            await asyncio.sleep(30)
            await reconcile_leases_with_gateway()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Lỗi trong vòng lặp đối soát lease định kỳ: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý khởi tạo và dọn dẹp tài nguyên (DB Pool, Redis, Scheduler v.v.)."""
    settings = get_settings()
    print(f"[*] Starting {settings.APP_NAME} in [{settings.APP_ENV}] mode...")
    
    # 1. Phục hồi executing hết lease sang trạng thái cần đối soát ngay khi boot
    from app.services.confirmation_service import confirmation_service
    recovered = confirmation_service.recover_expired_leases()
    if recovered:
        print(f"[*] Phục hồi {recovered} confirmation hết hạn lease sang trạng thái unknown_result.")

    # 2. Khởi động Scheduler Service cho thông báo nhắc hẹn
    from app.services.scheduler import scheduler_service
    scheduler_service.start()

    # 3. Khởi động Indexing Worker để phục hồi các job gián đoạn ngay khi boot
    from app.knowledge.indexing import indexing_service
    await indexing_service.start_worker()

    # 4. Khởi động background reconciliation task (chu kỳ 30s)
    reconciliation_task = asyncio.create_task(_background_lease_reconciliation_loop())
    
    yield
    
    print(f"[*] Shutting down {settings.APP_NAME}...")
    reconciliation_task.cancel()
    try:
        await reconciliation_task
    except asyncio.CancelledError:
        pass
    scheduler_service.stop()
    indexing_service.stop_worker()



def create_application(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description="Enterprise AI Agent Gateway - Decoupled Odoo & Google Workspace Assistant",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )
    app.state.settings = settings

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.DEBUG else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Middleware gắn X-Request-ID cho audit trail
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start_time = time.perf_counter()

        response: Response = await call_next(request)

        duration = time.perf_counter() - start_time
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration:.4f}s"
        return response

    # Mount API routers
    from app.api.v1.router import get_api_v1_router
    app.include_router(get_api_v1_router(settings))

    if settings.INTEGRATION_MODE != "gateway":
        from app.api.v1.odoo_auth import router as odoo_auth_router
        app.include_router(odoo_auth_router)
    else:
        from app.api.v1.internal import router as internal_router
        app.include_router(internal_router)

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "message": "Enterprise AI Agent API Gateway is running",
            "docs": "/docs" if settings.DEBUG else "disabled"
        }

    @app.get("/oauth/google/callback", include_in_schema=False)
    async def forward_google_oauth_callback(request: Request):
        from starlette.responses import RedirectResponse

        gateway_base = (settings.GATEWAY_BASE_URL or "http://localhost:8080").rstrip("/")
        query = str(request.query_params)
        dest_url = (
            f"{gateway_base}/oauth/google/callback?{query}"
            if query
            else f"{gateway_base}/oauth/google/callback"
        )
        return RedirectResponse(url=dest_url, status_code=307)

    return app


app = create_application()
